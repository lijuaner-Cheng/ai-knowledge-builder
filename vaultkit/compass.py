"""Daily navigation, agenda, backlog and reminder candidates.

Original design. Navigation is derived data, rebuilt from owned notes. It
never becomes a source of knowledge itself.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

from . import clock, codec, ledger, seal

NAV_DIR = ledger.NAV_REL
TODAY_FILE = "今日.md"
BACKLOG_FILE = "待处理事项.md"
DAILY_DIR = "日报"


def _zone(name: str):
    return clock.zone(name)


def _local_date(stamp: str | None, zone: ZoneInfo) -> str | None:
    if not stamp:
        return None
    try:
        dt = datetime.fromisoformat(str(stamp).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(zone).date().isoformat()
    except Exception:
        return None


def _owned_rows(vault: Path, include_archived=False) -> list[dict]:
    rows = []
    for path in ledger._iter_notes(vault, set(ledger.OWN_SHELVES), include_archived):
        props, _ = codec.decode(path.read_text(encoding="utf-8"))
        rows.append({"path": ledger._relative(vault, path), "props": props})
    return rows


def rebuild(vault: Path, zone_name: str) -> dict:
    """Regenerate navigation from owned notes. Idempotent."""
    zone = _zone(zone_name)
    today = datetime.now(zone).date().isoformat()
    rows = _owned_rows(vault, include_archived=False)

    saved_today = []
    for row in rows:
        props = row["props"]
        if _local_date(props.get("made"), zone) == today or _local_date(props.get("changed"), zone) == today:
            saved_today.append(row)

    pending = []
    for row in rows:
        props = row["props"]
        if props.get("kind") in ("decision", "task") and props.get("state") in ("pending_decision", "todo", "doing"):
            pending.append(row)

    nav = vault / NAV_DIR
    (nav / DAILY_DIR).mkdir(parents=True, exist_ok=True)

    def link(row):
        title = codec.title_of(row["props"], Path(row["path"]).stem)
        return f"- [[{row['path'][:-3]}|{title}]]"

    today_text = (
        "# 今日\n\n日期：" + today + "\n\n"
        "## 今日保存/更新的知识\n\n"
        + ("\n".join(link(r) for r in saved_today) or "- （暂无）") + "\n\n"
        "## 待处理事项\n\n"
        + ("\n".join(link(r) for r in pending) or "- （暂无）") + "\n"
    )
    seal.atomic_text(nav / TODAY_FILE, today_text)

    backlog_text = (
        "# 待处理事项\n\n" + ("\n".join(link(r) for r in pending) or "- （暂无）") + "\n"
    )
    seal.atomic_text(nav / BACKLOG_FILE, backlog_text)

    by_day: dict[str, list[dict]] = {}
    for row in rows:
        day = _local_date(row["props"].get("made"), zone)
        if day:
            by_day.setdefault(day, []).append(row)
    for day, day_rows in by_day.items():
        text = f"# {day}\n\n" + "\n".join(link(r) for r in day_rows) + "\n"
        seal.atomic_text(nav / DAILY_DIR / (day + ".md"), text)

    return {"rebuilt": True, "today": today, "owned_notes": len(rows), "pending": len(pending)}


def agenda(vault: Path, zone_name: str) -> dict:
    zone = _zone(zone_name)
    today = datetime.now(zone).date().isoformat()
    saved = []
    for row in _owned_rows(vault):
        props = row["props"]
        if _local_date(props.get("made"), zone) == today or _local_date(props.get("changed"), zone) == today:
            saved.append({"path": row["path"], **codec.compact(props)})
    return {"date": today, "saved": saved, "backlog": backlog(vault)["items"]}


def daily(vault: Path, zone_name: str, date: str | None = None) -> dict:
    zone = _zone(zone_name)
    date = date or datetime.now(zone).date().isoformat()
    items = []
    for row in _owned_rows(vault):
        if _local_date(row["props"].get("made"), zone) == date:
            items.append({"path": row["path"], **codec.compact(row["props"])})
    return {"date": date, "items": items, "total": len(items)}


def backlog(vault: Path, *, include_closed=False, include_archived=False) -> dict:
    items = []
    for row in _owned_rows(vault, include_archived):
        props = row["props"]
        kind, state = props.get("kind"), props.get("state")
        if kind in ("decision", "task"):
            if not include_closed and state in ("done", "dropped"):
                continue
            items.append({"path": row["path"], **codec.compact(props)})
    return {"items": items, "total": len(items)}


def reminder_candidates(vault: Path, zone_name: str, stale_hours: int = 48) -> dict:
    """Read-only candidate scan. Never emits or acknowledges notifications."""
    zone = _zone(zone_name)
    now = datetime.now(timezone.utc)
    candidates = []
    for row in _owned_rows(vault):
        props = row["props"]
        if props.get("status") == "archived":
            continue
        if props.get("state") in ("done", "dropped"):
            continue
        due = props.get("due")
        review = props.get("review")
        hit = None
        if due:
            try:
                dt = datetime.fromisoformat(str(due).replace("Z", "+00:00"))
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                if dt <= now:
                    hit = "due"
            except Exception:
                pass
        if hit is None and review:
            try:
                dt = datetime.fromisoformat(str(review).replace("Z", "+00:00"))
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                if dt <= now:
                    hit = "review"
            except Exception:
                pass
        if hit is None:
            stamp = props.get("changed") or props.get("made")
            if stamp:
                try:
                    dt = datetime.fromisoformat(str(stamp).replace("Z", "+00:00"))
                    if dt.tzinfo is None:
                        dt = dt.replace(tzinfo=timezone.utc)
                    if now - dt >= timedelta(hours=stale_hours):
                        hit = "stale"
                except Exception:
                    pass
        if hit:
            candidates.append({"path": row["path"], "reason": hit,
                               "title": codec.title_of(props, Path(row["path"]).stem),
                               "due": due, "review": review})
    return {"candidates": candidates, "total": len(candidates), "zone": str(zone)}
