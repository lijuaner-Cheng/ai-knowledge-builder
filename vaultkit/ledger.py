"""The knowledge ledger: note storage, ownership scopes, history and archive.

Original design. Ownership is expressed through three "own" shelves and two
"outside" shelves plus a neutral history shelf. Retrieval defaults to own.
"""
from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path

from . import codec, seal

OWN_SHELVES = ("01-Projects（项目库）", "02-Areas（资产库）", "05-Skills（技能库）")
OUT_SHELVES = ("00 - 灵感库", "03-Resources（资源库）")
HISTORY_SHELF = "04-Archive（归档库）"
SHELVES = (
    "00 - 灵感库",
    "01-Projects（项目库）",
    "02-Areas（资产库）",
    "03-Resources（资源库）",
    "04-Archive（归档库）",
    "05-Skills（技能库）",
)

# 导航子目录挂在 Projects 架子下，不算知识正文，检索时排除。
NAV_REL = "01-Projects（项目库）/每日流"

SCOPE_SHELVES = {
    "own": set(OWN_SHELVES),
    "outside": set(OUT_SHELVES),
    "all": set(OWN_SHELVES) | set(OUT_SHELVES),
}

KINDS = ("knowledge", "decision", "task")
STATES = ("pending_decision", "todo", "doing", "done", "dropped")

DEFAULT_STATE = {"decision": "pending_decision", "task": "todo"}


def _safe_name(title: str) -> str:
    name = re.sub(r'[\\/:*?"<>|#\[\]]', "-", title).strip(" .-")
    return (name or "untitled")[:80]


def _shelf_root(vault: Path, shelf: str) -> Path:
    return vault / shelf


def _iter_notes(vault: Path, shelves: set[str], include_archived: bool) -> list[Path]:
    found = []
    for shelf in sorted(shelves):
        base = _shelf_root(vault, shelf)
        if not base.is_dir():
            continue
        for path in sorted(base.rglob("*.md")):
            rel = path.relative_to(vault).as_posix()
            if rel.startswith(NAV_REL + "/"):
                continue
            props, _ = codec.decode(path.read_text(encoding="utf-8"))
            archived = props.get("status") == "archived"
            if archived and not include_archived:
                continue
            found.append(path)
    return found


def _relative(vault: Path, path: Path) -> str:
    return path.relative_to(vault).as_posix()


def _resolve_path(vault: Path, rel: str, allowed: set[str]) -> Path:
    parts = Path(rel).parts
    if not parts or parts[0] not in allowed:
        raise ValueError("path is outside the selected scope")
    target = (vault / rel).resolve()
    if vault.resolve() not in target.parents:
        raise ValueError("path escapes the vault")
    if not target.is_file():
        raise ValueError("note not found: " + rel)
    return target


def _history_dir(root: Path, rel: str) -> Path:
    # mirror the relative path under .trail so histories stay per-note
    return root / seal.TRAIL_DIR / rel


def _snapshot(root: Path, vault: Path, path: Path, rev: str) -> None:
    rel = _relative(vault, path)
    folder = _history_dir(root, rel).parent
    folder.mkdir(parents=True, exist_ok=True)
    stamp = datetime.utcnow().strftime("%Y%m%dT%H%M%S%fZ")
    name = path.stem + "--" + stamp + "--" + rev[:12] + ".md"
    seal.atomic_text(folder / name, path.read_text(encoding="utf-8"))


def list_notes(vault: Path, *, scope="own", include_archived=False, summary=True,
               offset=0, limit=50) -> dict:
    if scope not in SCOPE_SHELVES:
        raise ValueError("scope must be own, outside or all")
    if not (0 <= offset) or not (1 <= limit <= 500):
        raise ValueError("invalid paging")
    paths = _iter_notes(vault, SCOPE_SHELVES[scope], include_archived)
    items = []
    for path in paths[offset:offset + limit]:
        props, _ = codec.decode(path.read_text(encoding="utf-8"))
        items.append({
            "path": _relative(vault, path),
            **({**codec.compact(props), "shelf": path.parts[len(vault.parts)]} if summary else {}),
        })
    return {"scope": scope, "total": len(paths), "offset": offset, "limit": limit, "items": items}


def search_notes(vault: Path, query: str, *, scope="own", include_archived=False,
                 offset=0, limit=50) -> dict:
    if scope not in SCOPE_SHELVES:
        raise ValueError("scope must be own, outside or all")
    words = [w.lower() for w in query.split() if w]
    if not words:
        raise ValueError("search query is empty")
    hits = []
    for path in _iter_notes(vault, SCOPE_SHELVES[scope], include_archived):
        props, body = codec.decode(path.read_text(encoding="utf-8"))
        haystack = " ".join([
            str(props.get("title") or ""), body,
            " ".join(str(x) for x in (props.get("tags") or [])),
            " ".join(str(x) for x in (props.get("aliases") or [])),
        ]).lower()
        if all(word in haystack for word in words):
            hits.append((path, props))
    page = hits[offset:offset + limit]
    return {
        "scope": scope, "query": query, "total": len(hits), "offset": offset, "limit": limit,
        "items": [{"path": _relative(vault, p), **codec.compact(props)} for p, props in page],
    }


def read_note(vault: Path, rel: str, *, scope="own") -> dict:
    path = _resolve_path(vault, rel, SCOPE_SHELVES[scope])
    text = path.read_text(encoding="utf-8")
    props, body = codec.decode(text)
    return {"path": rel, "properties": props, "body": body, "revision": seal.digest(text)}


def create_note(root: Path, vault: Path, req: dict) -> dict:
    shelf = req.get("shelf")
    if shelf not in OWN_SHELVES + OUT_SHELVES:
        raise ValueError("shelf must be one of " + ", ".join(OWN_SHELVES + OUT_SHELVES))
    title = str(req.get("title") or "").strip()
    body = str(req.get("body") or "")
    if not title:
        raise ValueError("title is required")
    kind = req.get("kind", "knowledge")
    if kind not in KINDS:
        raise ValueError("invalid kind")
    if kind != "knowledge" and shelf not in OWN_SHELVES:
        raise ValueError("decisions and tasks must live on an own shelf")
    state = req.get("state") or DEFAULT_STATE.get(kind)
    if state is not None and state not in STATES:
        raise ValueError("invalid state")

    props = {
        "title": title, "kind": kind, "state": state,
        "origin": req.get("origin"), "status": "active",
        "made": seal.utcnow(), "changed": seal.utcnow(),
        "due": req.get("due"), "review": req.get("review"),
        "tags": req.get("tags") or [], "aliases": req.get("aliases") or [],
    }
    text = codec.encode(props, body)
    folder = _shelf_root(vault, shelf)
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / (_safe_name(title) + ".md")

    if target.exists():
        existing = target.read_text(encoding="utf-8")
        eprops, ebody = codec.decode(existing)
        if ebody.strip() == body.strip() and eprops.get("title") == title:
            return {"status": "duplicate", "path": _relative(vault, target), "created": 0}
        raise ValueError("a different note already uses this title; revise it instead")

    with seal.writer_lock(root):
        if target.exists():
            raise ValueError("title collision; retry")
        seal.atomic_text(target, text)
    return {"status": "created", "path": _relative(vault, target), "created": 1,
            "revision": seal.digest(text)}


def update_note(root: Path, vault: Path, req: dict) -> dict:
    rel = req.get("path")
    expected = req.get("expected_revision")
    path = _resolve_path(vault, rel, set(SHELVES))
    current = path.read_text(encoding="utf-8")
    if seal.digest(current) != expected:
        raise ValueError("revision conflict; read again and merge")
    props, body = codec.decode(current)
    changes = req.get("properties") or {}
    allowed = {"title", "kind", "state", "origin", "due", "review", "tags", "aliases"}
    unknown = set(changes) - allowed
    if unknown:
        raise ValueError("unsupported properties: " + ", ".join(sorted(unknown)))
    for key, value in changes.items():
        props[key] = value
    if "state" in changes and changes["state"] not in STATES:
        raise ValueError("invalid state")
    if "kind" in changes and changes["kind"] not in KINDS:
        raise ValueError("invalid kind")
    new_body = body if req.get("body") is None else str(req["body"])
    props["changed"] = seal.utcnow()
    text = codec.encode(props, new_body)
    with seal.writer_lock(root):
        _snapshot(root, vault, path, expected)
        seal.atomic_text(path, text)
    return {"path": rel, "updated": 1, "revision": seal.digest(text)}


def flip_status(root: Path, vault: Path, req: dict, *, archived: bool) -> dict:
    rel = req.get("path")
    expected = req.get("expected_revision")
    path = _resolve_path(vault, rel, set(SHELVES))
    current = path.read_text(encoding="utf-8")
    if seal.digest(current) != expected:
        raise ValueError("revision conflict; read again")
    props, body = codec.decode(current)
    props["status"] = "archived" if archived else "active"
    props["changed"] = seal.utcnow()
    text = codec.encode(props, body)
    with seal.writer_lock(root):
        _snapshot(root, vault, path, expected)
        seal.atomic_text(path, text)
    return {"path": rel, "status": props["status"], "revision": seal.digest(text)}


def trail(vault: Path, root: Path, rel: str) -> dict:
    path = _resolve_path(vault, rel, set(SHELVES))
    folder = _history_dir(root, rel).parent
    versions = []
    if folder.is_dir():
        for file in sorted(folder.glob("*.md")):
            versions.append({"file": file.name, "saved": datetime.utcfromtimestamp(file.stat().st_mtime).isoformat() + "Z"})
    return {"path": rel, "versions": versions, "count": len(versions)}


def read_version(root: Path, vault: Path, rel: str, version: str) -> dict:
    folder = _history_dir(root, rel).parent
    target = (folder / version).resolve()
    if folder.resolve() not in target.parents or not target.is_file():
        raise ValueError("unknown version")
    props, body = codec.decode(target.read_text(encoding="utf-8"))
    return {"path": rel, "version": version, "properties": props, "body": body}


def rewind(root: Path, vault: Path, req: dict) -> dict:
    rel, version, expected = req.get("path"), req.get("version"), req.get("expected_revision")
    path = _resolve_path(vault, rel, set(SHELVES))
    current = path.read_text(encoding="utf-8")
    if seal.digest(current) != expected:
        raise ValueError("revision conflict; read again")
    old = _history_dir(root, rel).parent / version
    if not old.is_file():
        raise ValueError("unknown version")
    text = old.read_text(encoding="utf-8")
    with seal.writer_lock(root):
        _snapshot(root, vault, path, expected)
        seal.atomic_text(path, text)
    return {"path": rel, "rewound_to": version, "revision": seal.digest(text)}


def duplicate_body(vault: Path, body: str, shelf: str) -> bool:
    base = _shelf_root(vault, shelf)
    if not base.is_dir():
        return False
    needle = body.strip()
    for path in base.rglob("*.md"):
        _, existing = codec.decode(path.read_text(encoding="utf-8"))
        if existing.strip() == needle:
            return True
    return False
