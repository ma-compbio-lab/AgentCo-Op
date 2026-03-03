from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Optional

from dynaforge.ir.schema import ArtifactRef


class ArtifactStore:
    """Simple local content-addressable artifact store."""

    def __init__(self, root: str | Path):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def _path_for_digest(self, digest: str, suffix: str = "") -> Path:
        prefix = digest[:2]
        directory = self.root / prefix
        directory.mkdir(parents=True, exist_ok=True)
        return directory / f"{digest}{suffix}"

    def put_bytes(self, payload: bytes, suffix: str = "") -> ArtifactRef:
        digest = hashlib.sha256(payload).hexdigest()
        path = self._path_for_digest(digest, suffix=suffix)
        if not path.exists():
            path.write_bytes(payload)
        return ArtifactRef(sha256=digest, uri=path.resolve().as_uri(), size_bytes=len(payload))

    def put_text(self, text: str, suffix: str = ".txt") -> ArtifactRef:
        return self.put_bytes(text.encode("utf-8"), suffix=suffix)

    def put_json(self, payload: Any, suffix: str = ".json") -> ArtifactRef:
        encoded = json.dumps(payload, ensure_ascii=True, sort_keys=True, indent=2).encode("utf-8")
        return self.put_bytes(encoded, suffix=suffix)

    def link_existing(self, path: str | Path, mime: Optional[str] = None) -> ArtifactRef:
        resolved = Path(path).resolve()
        digest = hashlib.sha256(resolved.read_bytes()).hexdigest()
        return ArtifactRef(
            sha256=digest,
            uri=resolved.as_uri(),
            mime=mime,
            size_bytes=resolved.stat().st_size,
        )

    def resolve(self, artifact: ArtifactRef) -> Path:
        if not artifact.uri:
            raise ValueError("ArtifactRef missing uri")
        return Path(artifact.uri.removeprefix("file://"))
