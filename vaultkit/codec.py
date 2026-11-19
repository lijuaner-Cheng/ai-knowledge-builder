"""Markdown note codec: frontmatter <-> body.

Original format. Frontmatter uses a simple indented-key syntax that is still
valid YAML for Obsidian, so notes render correctly there. We deliberately do
not depend on PyYAML: a small deterministic reader/writer is enough for the
flat metadata we own, and it avoids altering hand-written fields we do not
understand.
"""
from __future__ import annotations

import json
from datetime import datetime

DELIM = "---"
_KNOWN_SCALARS = ("title", "kind", "state", "origin", "status", "made", "changed", "due", "review", "rev")
_LIST_FIELDS = ("tags", "aliases")


def _quote(value: str) -> str:
    return json.dumps(str(value), ensure_ascii=False)


def encode(props: dict, body: str) -> str:
    """Serialize a note deterministically."""
    lines = [DELIM]
    for key in _KNOWN_SCALARS:
        if key in props and props[key] not in (None, ""):
            lines.append(f"{key}: {_quote(props[key])}")
    for key in _LIST_FIELDS:
        values = props.get(key) or []
        if values:
            lines.append(f"{key}:")
            for item in values:
                lines.append(f"  - {_quote(item)}")
    # preserve any extra flat scalar fields the user added by hand
    for key, value in props.items():
        if key in _KNOWN_SCALARS or key in _LIST_FIELDS:
            continue
        if isinstance(value, (str, int, float, bool)) and "\n" not in str(value):
            lines.append(f"{key}: {value if not isinstance(value, str) else _quote(value)}")
    lines.append(DELIM)
    lines.append("")
    text = "\n".join(lines)
    if body:
        text += body.strip("\n") + "\n"
    return text


def decode(text: str) -> tuple[dict, str]:
    """Parse a note. Returns (props, body). Missing frontmatter => empty props."""
    normalized = text.replace("\r\n", "\n")
    if not normalized.startswith(DELIM + "\n"):
        return {}, normalized.strip("\n")
    head_end = normalized.find("\n" + DELIM, len(DELIM) + 1)
    if head_end == -1:
        return {}, normalized.strip("\n")
    head = normalized[len(DELIM) + 1:head_end]
    body = normalized[head_end + len(DELIM) + 1:].lstrip("\n")
    props: dict = {}
    current_list = None
    for line in head.split("\n"):
        if not line.strip():
            continue
        if line.startswith("  - "):
            if current_list:
                current_list.append(_unquote(line[4:].strip()))
            continue
        if ":" not in line:
            continue
        key, raw = line.split(":", 1)
        key = key.strip()
        raw = raw.strip()
        if raw == "":
            props[key] = []
            current_list = props[key]
        else:
            props[key] = _unquote(raw)
            current_list = None
    return props, body.strip("\n")


def _unquote(raw: str):
    if raw.startswith('"') and raw.endswith('"'):
        try:
            return json.loads(raw)
        except Exception:
            return raw[1:-1]
    if raw.lower() in ("null", "none", "~"):
        return None
    return raw


def title_of(props: dict, fallback: str) -> str:
    return str(props.get("title") or fallback)


def compact(props: dict) -> dict:
    """Small summary for list/search results."""
    return {
        "title": props.get("title"),
        "kind": props.get("kind", "knowledge"),
        "state": props.get("state"),
        "status": props.get("status", "active"),
        "tags": props.get("tags") or [],
        "made": props.get("made"),
        "changed": props.get("changed"),
        "due": props.get("due"),
        "review": props.get("review"),
    }
