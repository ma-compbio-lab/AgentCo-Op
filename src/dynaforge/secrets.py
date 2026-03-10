from __future__ import annotations

import os
from pathlib import Path
from typing import Dict, Iterable, Mapping


DEFAULT_SECRET_FILE_MAP: dict[str, str] = {
    "openai_api_key": "OPENAI_API_KEY",
    "wandb_api_key": "WANDB_API_KEY",
}


def load_local_secrets(
    *,
    secret_dir: str | Path = ".secrets",
    env_files: Iterable[str | Path] = (".env.local", ".env"),
    overwrite: bool = False,
) -> Dict[str, str]:
    loaded: Dict[str, str] = {}
    secret_root = Path(secret_dir)
    if secret_root.exists():
        for file_name, env_name in DEFAULT_SECRET_FILE_MAP.items():
            secret_path = secret_root / file_name
            secret_value = _read_secret_value(secret_path)
            if secret_value is None:
                continue
            if overwrite or not os.environ.get(env_name):
                os.environ[env_name] = secret_value
                loaded[env_name] = str(secret_path)

    for env_file in env_files:
        env_path = Path(env_file)
        if not env_path.exists():
            continue
        for env_name, value in _parse_env_file(env_path).items():
            if overwrite or not os.environ.get(env_name):
                os.environ[env_name] = value
                loaded[env_name] = str(env_path)

    return loaded


def _read_secret_value(path: Path) -> str | None:
    if not path.exists() or not path.is_file():
        return None
    value = path.read_text(encoding="utf-8").strip()
    return value or None


def _parse_env_file(path: Path) -> Mapping[str, str]:
    values: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, raw_value = stripped.split("=", 1)
        key = key.strip()
        value = raw_value.strip().strip("\"'")
        if key:
            values[key] = value
    return values
