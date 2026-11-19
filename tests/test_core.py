#!/usr/bin/env python3
"""Self-test for mindvault. Creates isolated trial instances in a temp parent."""
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from vaultkit import manager, seal  # noqa: E402

PASS = []
FAIL = []


def check(name, condition, detail=""):
    (PASS if condition else FAIL).append(name)
    print(("  OK   " if condition else "  FAIL ") + name + ("  " + detail if detail else ""))


def main():
    tmp = Path(tempfile.mkdtemp(prefix="mindvault-test-"))
    try:
        # ---- doctor ----
        d = manager.doctor("Asia/Shanghai")
        check("doctor ready", d["ready"], str(d["errors"]))

        # ---- protected-root overlap ----
        try:
            manager.create(tmp, "bad", "trial", "Asia/Shanghai", moats=[tmp])
            check("self-overlap rejected", False)
        except ValueError:
            check("self-overlap rejected", True)

        # ---- create ----
        r = manager.create(tmp, "验收库", "trial", "Asia/Shanghai")
        sv = r["seal"]
        home = Path(sv["home"])
        key = sv["key"]
        check("instance ready", sv["phase"] == "ready")
        check("seal file exists", (home / seal.ANCHOR_NAME).is_file())
        check("handoff exists", (home / "handoff.json").is_file())
        check("engine copied", (home / "engine" / "run.py").is_file())
        check("six shelves created", all((home / sv["library"] / s).is_dir()
              for s in ("项目", "资产", "技能与模板", "资源", "灵感库", "归档")))

        engine = [sys.executable, "-B", str(home / "engine" / "run.py"),
                  "--vault", str(home), "--key", key]

        def call(req):
            p = subprocess.run(engine, input=json.dumps(req, ensure_ascii=False),
                               capture_output=True, text=True, encoding="utf-8")
            return json.loads(p.stdout)

        # ---- identity checks ----
        wrong = call({"op": "ping"})
        check("bound engine ping", wrong["ok"] is True)
        source_runner = ("import sys; sys.path.insert(0, r'" + str(ROOT) +
                         "'); from vaultkit.entry import main; raise SystemExit(main())")
        moved = subprocess.run([sys.executable, "-B", "-c", source_runner,
                                "--vault", str(home), "--key", key],
                               input="{}", capture_output=True, text=True, encoding="utf-8")
        mv = json.loads(moved.stdout)
        check("source engine rejected", mv["ok"] is False, mv.get("error", "")[:48])
        bad_key = subprocess.run(engine[:-1] + ["00000000-0000-0000-0000-000000000000"],
                                 input="{}", capture_output=True, text=True, encoding="utf-8")
        check("wrong key rejected", json.loads(bad_key.stdout)["ok"] is False)

        # ---- empty read-only checks ----
        check("empty scan", call({"op": "scan"})["result"]["total"] == 0)
        check("empty agenda", call({"op": "agenda"})["result"]["saved"] == [])
        check("empty backlog", call({"op": "backlog"})["result"]["total"] == 0)
        check("empty hint", call({"op": "hint"})["result"]["total"] == 0)

        # ---- explicit-save gate ----
        gated = call({"op": "note", "shelf": "资产", "title": "不应保存", "body": "x"})
        check("save without consent refused", gated["ok"] is False, gated.get("error", "")[:40])
        check("nothing written by gated call",
              call({"op": "scan"})["result"]["total"] == 0)

        # ---- create owned note ----
        made = call({"op": "note", "consent": "记下来", "ask": "保存这条",
                     "shelf": "资产", "title": "复盘笔记", "body": "我的理解和做法。",
                     "tags": ["复盘"], "origin": "本次对话"})
        check("note created", made["ok"] and made["result"]["created"] == 1, str(made)[:80])
        path = made["result"]["path"]
        check("note on owned shelf", path.startswith("资产/"), path)

        dup = call({"op": "note", "consent": "记下来", "shelf": "资产",
                    "title": "复盘笔记", "body": "我的理解和做法。"})
        check("identical body deduped", dup["result"].get("status") == "duplicate")

        outside = call({"op": "note", "consent": "记下来", "shelf": "资源",
                        "title": "外部文章", "body": "别人写的。"})
        check("outside note created", outside["ok"])

        # ---- ownership boundary ----
        owned_scan = call({"op": "scan", "scope": "own"})
        check("owned scan excludes outside", owned_scan["result"]["total"] == 1,
              "total=" + str(owned_scan["result"]["total"]))
        outside_scan = call({"op": "scan", "scope": "outside"})
        check("outside scan finds only outside", outside_scan["result"]["total"] == 1)
        all_scan = call({"op": "scan", "scope": "all"})
        check("all scan finds both", all_scan["result"]["total"] == 2)
        escaped = call({"op": "open", "scope": "own", "path": "资源/外部文章.md"})
        check("cross-scope read rejected", escaped["ok"] is False)

        # ---- search ----
        found = call({"op": "find", "scope": "all", "query": "复盘"})
        check("search hits", found["result"]["total"] == 1)
        and_search = call({"op": "find", "scope": "all", "query": "复盘 不存在"})
        check("search AND semantics", and_search["result"]["total"] == 0)

        # ---- read / revise / history / rollback ----
        read = call({"op": "open", "scope": "own", "path": path})["result"]
        rev1 = read["revision"]
        updated = call({"op": "revise", "consent": "记下来", "path": path,
                        "expected_revision": rev1, "body": "更新后的理解。",
                        "properties": {"tags": ["复盘", "已更新"]}})
        check("revise ok", updated["ok"], str(updated)[:80])
        rev2 = updated["result"]["revision"]
        conflict = call({"op": "revise", "consent": "记下来", "path": path,
                         "expected_revision": rev1, "body": "覆盖尝试"})
        check("stale revision refused", conflict["ok"] is False)
        trail = call({"op": "trail", "path": path})["result"]
        check("history saved", trail["count"] >= 1, "versions=" + str(trail["count"]))
        version = trail["versions"][0]["file"]
        snap = call({"op": "snapshot", "path": path, "version": version})
        check("snapshot readable", snap["ok"] and "我的理解" in snap["result"]["body"])
        rolled = call({"op": "rewind", "consent": "记下来", "path": path,
                       "version": version, "expected_revision": rev2})
        check("rollback ok", rolled["ok"])
        after = call({"op": "open", "scope": "own", "path": path})["result"]
        check("rollback restored body", after["body"] == "我的理解和做法。")

        # ---- archive / restore ----
        arc = call({"op": "archive", "consent": "记下来", "path": path,
                    "expected_revision": after["revision"]})
        check("archive ok", arc["ok"])
        check("archived hidden by default",
              call({"op": "scan", "scope": "own"})["result"]["total"] == 0)
        inc = call({"op": "scan", "scope": "own", "include_archived": True})
        check("archived visible on request", inc["result"]["total"] == 1)
        restore = call({"op": "unarchive", "consent": "记下来", "path": path,
                        "expected_revision": arc["result"]["revision"]})
        check("restore ok", restore["ok"])

        # ---- navigation ----
        tidy = call({"op": "tidy", "consent": "记下来"})
        check("navigation rebuilt", tidy["ok"] and tidy["result"]["owned_notes"] == 1,
              str(tidy.get("result"))[:60])
        nav_today = home / sv["library"] / "项目" / "每日流" / "今日.md"
        check("today note written", nav_today.is_file() and "复盘" in nav_today.read_text(encoding="utf-8"))

        # ---- instance manager ops ----
        insp = manager.inspect(home, key)
        check("inspect returns rev", "rev" in insp)
        cfg = manager.configure(home, key, insp["rev"], "改名", {"title": "新名称"})
        check("configure ok", cfg["seal"]["title"] == "新名称", str(cfg)[:80])
        frozen = manager.flip_phase(home, key, cfg["rev"], "冻结", frozen=True)
        check("freeze ok", frozen["phase"] == "frozen", str(frozen)[:80])
        check("frozen instance rejects writes", call({"op": "note", "consent": "记下来",
              "shelf": "资产", "title": "x", "body": "y"})["ok"] is False)
        revived = manager.flip_phase(home, key, frozen["rev"], "恢复", frozen=False)
        check("revive ok", revived["phase"] == "ready", str(revived)[:80])

        check("list finds instance",
              any(i.get("key") == key for i in manager.instances(tmp)["items"]))

        print("\nPASS:", len(PASS), " FAIL:", len(FAIL))
        if FAIL:
            print("failed:", FAIL)
        return 1 if FAIL else 0
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
