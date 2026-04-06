from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import yaml
from fastapi import APIRouter

router = APIRouter(prefix="/api", tags=["config"])

_config_override_path = Path.home() / ".agentcoop" / "dashboard_config.yaml"


@router.get("/config")
def get_config() -> dict[str, Any]:
    conf_dir = Path(__file__).resolve().parents[3] / "src" / "agentcoop" / "conf"
    result: dict[str, Any] = {}
    config_path = conf_dir / "config.yaml"
    if config_path.exists():
        result["base"] = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    model_dir = conf_dir / "model"
    if model_dir.exists():
        result["available_models"] = [p.stem for p in sorted(model_dir.glob("*.yaml"))]
    experiment_dir = conf_dir / "experiment"
    if experiment_dir.exists():
        result["available_experiments"] = [p.stem for p in sorted(experiment_dir.glob("*.yaml"))]
    return result


@router.put("/config")
def update_config(payload: dict[str, Any]) -> dict[str, Any]:
    _config_override_path.parent.mkdir(parents=True, exist_ok=True)
    _config_override_path.write_text(yaml.dump(payload, default_flow_style=False), encoding="utf-8")
    return {"saved": True, "path": str(_config_override_path)}
