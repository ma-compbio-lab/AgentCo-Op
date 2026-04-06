from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import APIRouter

router = APIRouter(prefix="/api", tags=["skills"])


@router.get("/skills")
def get_skills() -> dict[str, Any]:
    from agentcoop.skills.library import SkillLibrary
    project_root = Path(__file__).resolve().parents[3]
    lib = SkillLibrary(search_paths=[project_root / "skills", project_root / "src" / "agentcoop" / "skills"])
    return {
        "meta_skills": [
            {"name": s.name, "description": s.description, "domain_tags": s.domain_tags,
             "capability_tags": s.capability_tags, "roles": s.roles, "body": s.body[:500]}
            for s in lib.meta_skills
        ],
        "agent_skills": [
            {"name": s.name, "description": s.description, "domain_tags": s.domain_tags,
             "capability_tags": s.capability_tags, "body": s.body[:500]}
            for s in lib.agent_skills
        ],
    }
