"""Instance identity, anchor file, revision hashing and single-writer lock.

Design notes: this is an original implementation. The anchor is called a
"seal", the unique id is a "key", and protected roots are "moats". Nothing in
this module is copied from another codebase; the vocabulary and data layout
are deliberately independent.
"""
from __future__ import annotations

import hashlib
import json
import os
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

ANCHOR_NAME = "seal.json"
LOCK_NAME = ".seal.lock"
TRAIL_DIR = ".trail"
FORMAT = 1


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def clean_path(value: str | os.PathLike) -> Path:
    """Resolve to an absolute path and refuse symlink components."""
    p = Path(value).expanduser()
    for part in p.parts:
        if not part or part == os.sep:
            continue
        probe = Path(part)
        if probe.is_symlink():
            raise ValueError("symbolic links are not allowed: " + str(value))
    return p.resolve()


def atomic_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".part-" + uuid.uuid4().hex[:10])
    try:
        tmp.write_text(text, encoding="utf-8", newline="\n")
        os.replace(tmp, path)
    finally:
        if tmp.exists():
            try:
                tmp.unlink()
            except OSError:
                pass


def atomic_json(path: Path, obj: dict) -> None:
    atomic_text(path, json.dumps(obj, ensure_ascii=False, indent=2, sort_keys=True) + "\n")


def _without_rev(seal: dict) -> dict:
    return {k: v for k, v in seal.items() if k != "rev"}


def seal_revision(seal: dict) -> str:
    return digest(json.dumps(_without_rev(seal), ensure_ascii=False, sort_keys=True, separators=(",", ":")))


def read_seal(root, key=None, *, ready_only: bool = True) -> dict:
    """Load and verify a seal. Bound to exact root + exact key."""
    home = clean_path(root)
    anchor = home / ANCHOR_NAME
    if not anchor.is_file() or anchor.is_symlink():
        raise ValueError("no valid seal at " + str(home))
    raw = json.loads(anchor.read_text(encoding="utf-8"))
    if raw.get("format") != FORMAT:
        raise ValueError("unsupported seal format")
    if clean_path(raw["home"]) != home:
        raise ValueError("seal home mismatch: path binding failed")
    if key is not None and key != raw["key"]:
        raise ValueError("seal key mismatch: identity check failed")
    if ready_only and raw.get("phase") != "ready":
        raise ValueError("instance is not ready: " + str(raw.get("phase")))
    return raw


def write_seal(home: Path, seal: dict) -> dict:
    seal = dict(seal)
    seal["touched"] = utcnow()
    seal["rev"] = seal_revision(seal)
    atomic_json(home / ANCHOR_NAME, seal)
    return seal


def overlaps(a: Path, b: Path) -> bool:
    a, b = clean_path(a), clean_path(b)
    return a == b or a in b.parents or b in a.parents


@contextmanager
def writer_lock(home):
    """Refuse concurrent writes; never silently delete a stale lock."""
    home = clean_path(home)
    lock = home / LOCK_NAME
    try:
        fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        raise RuntimeError("write lock present at " + str(lock) + "; inspect before retrying")
    try:
        os.write(fd, (utcnow() + " pid=" + str(os.getpid())).encode("utf-8"))
        os.fsync(fd)
        yield
    finally:
        os.close(fd)
        try:
            lock.unlink()
        except OSError:
            pass


def make_key() -> str:
    return str(uuid.uuid4())
