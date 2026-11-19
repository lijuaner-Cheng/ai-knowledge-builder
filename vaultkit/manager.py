"""Instance lifecycle manager: doctor, create, list, inspect, configure, freeze, revive."""
from __future__ import annotations

import argparse
import importlib.util
import json
import shutil
import sys
from datetime import datetime
from pathlib import Path

from . import clock, compass, ledger, seal

PACK = Path(__file__).resolve().parents[1]


def doctor(zone: str) -> dict:
    errors = []
    if sys.version_info < (3, 9):
        errors.append("Python 3.9+ required")
    for name in ("seal.py", "codec.py", "ledger.py", "compass.py", "entry.py", "clock.py"):
        if not (Path(__file__).parent / name).is_file():
            errors.append("runtime incomplete: " + name)
    return {"ready": not errors, "python": sys.version.split()[0], "platform": sys.platform,
            "zone": zone, "zone_exact": clock.available(zone), "errors": errors,
            "timer": "not_configured"}


def create(parent, title, tier, zone, review_clock="20:30", moats=()) -> dict:
    report = doctor(zone)
    if not report["ready"]:
        raise ValueError(json.dumps(report, ensure_ascii=False))
    if tier not in ("daily", "trial"):
        raise ValueError("tier must be daily or trial")
    title = str(title).strip()
    if not (1 <= len(title) <= 100):
        raise ValueError("title must be 1-100 characters")
    datetime.strptime(review_clock, "%H:%M")

    base = seal.clean_path(parent)
    for ancestor in (base, *base.parents):
        if (ancestor / seal.ANCHOR_NAME).exists() or (ancestor / ".obsidian").exists():
            raise ValueError("cannot create inside an existing instance or Obsidian vault")

    guarded = [seal.clean_path(p) for p in moats]
    guarded.append(seal.clean_path(PACK))
    key = seal.make_key()
    home = base / ("kb-" + tier + "-" + key)
    for g in guarded:
        if seal.overlaps(home, g):
            raise ValueError("target overlaps a protected root: " + str(g))

    base.mkdir(parents=True, exist_ok=True)
    home.mkdir(exist_ok=False)
    library_name = "library-" + key
    vault = home / library_name
    sv = {
        "format": seal.FORMAT, "key": key, "title": title, "tier": tier,
        "phase": "building", "home": str(home), "library": library_name,
        "zone": zone, "review_clock": review_clock, "born": seal.utcnow(),
        "touched": seal.utcnow(), "moats": [str(g) for g in guarded],
        "timer": {}, "template": "1.0.0", "reminders": {"enabled": True, "stale_hours": 48},
    }
    try:
        sv = seal.write_seal(home, sv)
        engine = home / "engine"
        shutil.copytree(Path(__file__).parent, engine / "vaultkit",
                        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        (engine / "run.py").write_text(
            "# Entry point bound to this instance.\n"
            "from vaultkit.entry import main\n"
            "raise SystemExit(main())\n",
            encoding="utf-8")
        vault.mkdir()
        for shelf in ledger.SHELVES:
            (vault / shelf).mkdir()
        (vault / ledger.NAV_REL / compass.DAILY_DIR).mkdir(parents=True)
        (vault / "README.md").write_text(
            "# " + title + "\n\n"
            "模式：" + tier + "\n\n"
            "钥匙：`" + key + "`\n\n"
            "在对话里明确说「记下来」才会保存。找「我的」内容只看 01-Projects / 02-Areas / 05-Skills；\n"
            "00-灵感库 与 03-Resources 属于外部输入，04-Archive 不改变归属。\n\n"
            "同一对话继续聊；换对话时把本实例目录下的 handoff.json 交给新 Agent。\n",
            encoding="utf-8")
        compass.rebuild(vault, zone)
        sv["phase"] = "ready"
        sv = seal.write_seal(home, sv)
    except Exception as exc:
        raise RuntimeError("creation incomplete at " + str(home) + ": " + str(exc)) from exc

    handoff = {
        "key": key, "vault": str(home),
        "entry": [sys.executable, "-B", str(home / "engine" / "run.py"),
                  "--vault", str(home), "--key", key],
    }
    seal.atomic_json(home / "handoff.json", handoff)
    return {"seal": {k: sv[k] for k in ("key", "title", "tier", "phase", "home", "library", "zone")},
            "rev": sv["rev"], "handoff": handoff,
            "note": "empty isolated instance; no timer or global settings touched"}


def instances(parent) -> dict:
    base = seal.clean_path(parent)
    items = []
    if not base.is_dir():
        return {"items": items}
    for path in sorted(base.glob("kb-*")):
        manifest = path / seal.ANCHOR_NAME
        if path.is_symlink() or not manifest.is_file():
            continue
        try:
            sv = seal.read_seal(path, ready_only=False)
            items.append({k: sv[k] for k in ("key", "title", "tier", "phase", "home")})
        except Exception as exc:
            items.append({"home": str(path), "error": str(exc)})
    return {"items": items}


def inspect(root, key) -> dict:
    sv = seal.read_seal(root, key, ready_only=False)
    return {"seal": sv, "rev": sv["rev"]}


def configure(root, key, expected, instruction, props: dict) -> dict:
    with seal.writer_lock(root):
        sv = seal.read_seal(root, key, ready_only=False)
        if sv["rev"] != expected:
            raise ValueError("revision conflict; inspect again")
        allowed = {"title", "zone", "review_clock", "timer", "reminders"}
        if set(props) - allowed:
            raise ValueError("unsupported fields: " + ", ".join(sorted(set(props) - allowed)))
        if "zone" in props and not clock.available(props["zone"]):
            raise ValueError("unknown timezone: " + str(props["zone"]))
        if "title" in props and not str(props["title"]).strip():
            raise ValueError("title cannot be empty")
        if "review_clock" in props:
            datetime.strptime(props["review_clock"], "%H:%M")
        for k, v in props.items():
            sv[k] = v
        sv = seal.write_seal(root, sv)
    return {"seal": {k: sv[k] for k in ("key", "title", "zone", "review_clock", "timer")}, "rev": sv["rev"]}


def flip_phase(root, key, expected, instruction, *, frozen: bool) -> dict:
    with seal.writer_lock(root):
        sv = seal.read_seal(root, key, ready_only=False)
        if sv["rev"] != expected:
            raise ValueError("revision conflict; inspect again")
        sv["phase"] = "frozen" if frozen else "ready"
        sv = seal.write_seal(root, sv)
    return {"key": key, "phase": sv["phase"], "rev": sv["rev"]}


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="mindvault manager")
    sub = p.add_subparsers(dest="op", required=True)
    d = sub.add_parser("doctor"); d.add_argument("--zone", default="UTC")
    c = sub.add_parser("create")
    c.add_argument("--parent", required=True); c.add_argument("--title", required=True)
    c.add_argument("--tier", required=True, choices=["daily", "trial"])
    c.add_argument("--zone", default="UTC"); c.add_argument("--review-clock", default="20:30")
    c.add_argument("--moat", action="append", default=[])
    ls = sub.add_parser("list"); ls.add_argument("--parent", required=True)
    for name in ("inspect", "configure", "freeze", "revive"):
        q = sub.add_parser(name)
        q.add_argument("--vault", required=True); q.add_argument("--key", required=True)
        if name != "inspect":
            q.add_argument("--expected", required=True); q.add_argument("--instruction", required=True)
    args = p.parse_args(argv)
    try:
        if args.op == "doctor":
            result = doctor(args.zone)
        elif args.op == "create":
            result = create(args.parent, args.title, args.tier, args.zone, args.review_clock, args.moat)
        elif args.op == "list":
            result = instances(args.parent)
        elif args.op == "inspect":
            result = inspect(args.vault, args.key)
        elif args.op == "configure":
            result = configure(args.vault, args.key, args.expected, args.instruction, json.load(sys.stdin))
        else:
            result = flip_phase(args.vault, args.key, args.expected, args.instruction,
                                frozen=(args.op == "freeze"))
        print(json.dumps({"ok": True, "result": result}, ensure_ascii=False, indent=2, default=str))
        return 0 if (args.op != "doctor" or result["ready"]) else 1
    except Exception as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
