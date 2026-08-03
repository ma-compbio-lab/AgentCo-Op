"""Filesystem-backed artifact store for per-run files.

Artifacts are content-addressable by SHA-256 but kept under human-readable
subpaths so post-hoc inspection stays ergonomic.
"""

from __future__ import annotations

import hashlib
import shutil
from pathlib import Path
from typing import Iterable


class ArtifactStore:
    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def put_bytes(self, rel_path: str, data: bytes) -> Path:
        target = self.root / rel_path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        return target

    def put_text(self, rel_path: str, text: str) -> Path:
        return self.put_bytes(rel_path, text.encode("utf-8"))

    def put_file(self, rel_path: str, source: str | Path) -> Path:
        source = Path(source)
        target = self.root / rel_path
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
        return target

    def sha256(self, rel_path: str) -> str:
        h = hashlib.sha256()
        with (self.root / rel_path).open("rb") as f:
            for chunk in iter(lambda: f.read(1 << 20), b""):
                h.update(chunk)
        return h.hexdigest()

    def read_bytes(self, rel_path: str) -> bytes:
        return (self.root / rel_path).read_bytes()

    def read_text(self, rel_path: str) -> str:
        return (self.root / rel_path).read_text(encoding="utf-8")

    def listdir(self, rel_path: str = ".") -> list[str]:
        base = self.root / rel_path
        if not base.exists():
            return []
        return sorted(str(p.relative_to(self.root)) for p in base.rglob("*") if p.is_file())

    def exists(self, rel_path: str) -> bool:
        return (self.root / rel_path).exists()


__all__ = ["ArtifactStore"]
