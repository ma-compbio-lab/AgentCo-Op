from __future__ import annotations

import os
from dataclasses import dataclass

from config import ModelConfig


@dataclass
class ModelRouting:
    planner: str
    worker: str
    judge: str
    aggregator: str


def configure_openai(api_key: str | None) -> None:
    if api_key:
        os.environ["OPENAI_API_KEY"] = api_key


def ensure_api_key() -> None:
    if not os.environ.get("OPENAI_API_KEY"):
        raise ValueError("OPENAI_API_KEY is required for the OpenAI Agents SDK.")


def validate_backend(backend: str) -> None:
    if backend != "openai":
        raise ValueError(f"Unsupported model backend: {backend}. Use 'openai'.")


def build_model_routing(model_cfg: ModelConfig) -> ModelRouting:
    default_name = model_cfg.name or "gpt-4o-mini"
    return ModelRouting(
        planner=model_cfg.planner or default_name,
        worker=model_cfg.worker or default_name,
        judge=model_cfg.judge or default_name,
        aggregator=model_cfg.aggregator or default_name,
    )
