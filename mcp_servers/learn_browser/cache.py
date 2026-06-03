"""Tiny on-disk TTL cache for upstream fetches.

Just enough to avoid re-fetching the same Learn page repeatedly during a
single pipeline run (and across runs within a day). Not threadsafe but the
worst case is a duplicate fetch.
"""

from __future__ import annotations

import hashlib
import json
import logging
import time
from pathlib import Path
from typing import Any


logger = logging.getLogger(__name__)


class TTLCache:
    """File-backed key/value cache with a per-entry TTL (seconds)."""

    def __init__(self, root: Path, *, default_ttl_s: int = 24 * 60 * 60) -> None:
        self.root = root
        self.default_ttl_s = default_ttl_s
        self.root.mkdir(parents=True, exist_ok=True)

    def _path_for(self, key: str) -> Path:
        digest = hashlib.sha256(key.encode("utf-8")).hexdigest()[:32]
        return self.root / f"{digest}.json"

    def get(self, key: str) -> Any | None:
        path = self._path_for(key)
        if not path.exists():
            return None
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            logger.debug("Cache read failed for %s: %s", key, exc)
            return None
        if payload.get("expires_at", 0) < time.time():
            return None
        return payload.get("value")

    def set(self, key: str, value: Any, *, ttl_s: int | None = None) -> None:
        path = self._path_for(key)
        expires_at = time.time() + (ttl_s if ttl_s is not None else self.default_ttl_s)
        payload = {"key": key, "expires_at": expires_at, "value": value}
        try:
            path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        except OSError as exc:
            logger.debug("Cache write failed for %s: %s", key, exc)
