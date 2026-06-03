"""Filesystem helpers — slug generation and safe writes under a root dir."""

from __future__ import annotations

import re
import unicodedata
from pathlib import Path

_SLUG_STRIP = re.compile(r"[^a-z0-9]+")


def slugify(text: str, *, max_len: int = 60) -> str:
    """Lowercase, ASCII-only, hyphen-separated, length-capped slug."""
    normalised = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    slug = _SLUG_STRIP.sub("-", normalised.lower()).strip("-")
    if not slug:
        slug = "post"
    return slug[:max_len].rstrip("-") or "post"


def safe_write(root: Path, relative_path: str | Path, content: str) -> Path:
    """Write `content` to `root/relative_path`, refusing path traversal."""
    root = root.resolve()
    target = (root / relative_path).resolve()
    if root not in target.parents and target != root:
        raise ValueError(f"Refusing to write outside {root}: {target}")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    return target
