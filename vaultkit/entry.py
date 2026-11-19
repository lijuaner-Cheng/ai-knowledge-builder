"""Note operation entry point.

Every call must carry --vault <absolute root> and --key <full key>.
Reads are open; writes require an explicit consent phrase from this turn.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import compass, ledger, seal

SAVE_MARK = "记下来"
READ_OPS = {"scan", "find", "open", "trail", "snapshot", "agenda", "diary", "backlog", "hint", "ping", "settings"}
WRITE_OPS = {"note", "revise", "archive", "unarchive", "rewind", "tidy", "configure_reminders"}


def _require_consent(req: dict, mark: str) -> None:
    consent = str(req.get("consent") or "")
    ask = str(req.get("ask") or "")
    if mark not in consent and mark not in ask:
        raise ValueError("explicit save intent is required for this operation")


def dispatch(root: Path, key: str, req: dict) -> dict:
    # This engine copy belongs to exactly one instance. A copied/moved runtime
    # must not be usable against a different vault.
    engine_home = Path(__file__).resolve().parent.parent.parent
    if engine_home != root:
        raise ValueError("this engine is bound to " + str(engine_home) + "; use the target instance engine")
    sv = seal.read_seal(root, key)
    vault = root / sv["library"]
    op = req.get("op")
    if op not in READ_OPS | WRITE_OPS:
        raise ValueError("unknown operation: " + str(op))
    zone = sv["zone"]

    if op == "ping":
        return {"seal": {k: sv[k] for k in ("key", "title", "tier", "phase", "home", "library", "zone")}, "rev": sv["rev"]}
    if op == "settings":
        return {"reminders": sv.get("reminders", {"enabled": True, "stale_hours": 48})}
    if op == "scan":
        return ledger.list_notes(vault, scope=req.get("scope", "own"),
                                 include_archived=bool(req.get("include_archived")),
                                 summary=bool(req.get("summary", True)),
                                 offset=int(req.get("offset", 0)), limit=int(req.get("limit", 50)))
    if op == "find":
        return ledger.search_notes(vault, str(req.get("query") or ""), scope=req.get("scope", "own"),
                                   include_archived=bool(req.get("include_archived")),
                                   offset=int(req.get("offset", 0)), limit=int(req.get("limit", 50)))
    if op == "open":
        return ledger.read_note(vault, req["path"], scope=req.get("scope", "own"))
    if op == "trail":
        return ledger.trail(vault, root, req["path"])
    if op == "snapshot":
        return ledger.read_version(root, vault, req["path"], req["version"])
    if op == "agenda":
        return compass.agenda(vault, zone)
    if op == "diary":
        return compass.daily(vault, zone, req.get("date"))
    if op == "backlog":
        return compass.backlog(vault, include_closed=bool(req.get("include_closed")),
                               include_archived=bool(req.get("include_archived")))
    if op == "hint":
        return compass.reminder_candidates(vault, zone, int(sv.get("reminders", {}).get("stale_hours", 48)))

    # ---- writes ----
    if op == "note":
        _require_consent(req, SAVE_MARK)
        result = ledger.create_note(root, vault, req)
    elif op == "revise":
        _require_consent(req, SAVE_MARK)
        result = ledger.update_note(root, vault, req)
    elif op == "archive":
        _require_consent(req, SAVE_MARK)
        result = ledger.flip_status(root, vault, req, archived=True)
    elif op == "unarchive":
        _require_consent(req, SAVE_MARK)
        result = ledger.flip_status(root, vault, req, archived=False)
    elif op == "rewind":
        _require_consent(req, SAVE_MARK)
        result = ledger.rewind(root, vault, req)
    elif op == "tidy":
        _require_consent(req, SAVE_MARK)
        result = compass.rebuild(vault, zone)
    elif op == "configure_reminders":
        _require_consent(req, SAVE_MARK)
        settings = req.get("settings") or {}
        if set(settings) - {"enabled", "stale_hours"}:
            raise ValueError("only enabled and stale_hours can change")
        if "stale_hours" in settings and not (1 <= int(settings["stale_hours"]) <= 8760):
            raise ValueError("stale_hours must be 1..8760")
        with seal.writer_lock(root):
            fresh = seal.read_seal(root, key)
            merged = dict(fresh.get("reminders", {"enabled": True, "stale_hours": 48}))
            merged.update(settings)
            fresh["reminders"] = merged
            seal.write_seal(root, fresh)
        result = {"reminders": merged}
    else:
        raise ValueError("unsupported operation")
    return result


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="mindvault note operations")
    parser.add_argument("--vault", required=True)
    parser.add_argument("--key", required=True)
    args = parser.parse_args(argv)
    try:
        root = seal.clean_path(args.vault)
        req = json.load(sys.stdin)
        if not isinstance(req, dict):
            raise ValueError("stdin must hold one JSON object")
        out = dispatch(root, args.key, req)
        print(json.dumps({"ok": True, "key": args.key, "vault": str(root), "result": out},
                         ensure_ascii=False, indent=2, default=str))
        return 0
    except Exception as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
