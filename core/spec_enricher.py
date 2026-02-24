from __future__ import annotations

import json
from typing import Iterable

from core.contracts import EvidencePack, EvidenceRequest, TaskSpec, TaskSpecPatch
from core.runtime import run_agent
from prompts import (
    build_evidence_prompt,
    build_spec_critic_prompt,
    build_spec_designer_prompt,
    format_memory_block,
)


async def ensure_spec(task: TaskSpec, ctx, agent_pool: dict[str, object], session=None) -> TaskSpec:
    from utils import clip_text, log_event, log_section, should_show_prompts

    if task.constraints and task.success_criteria:
        return task

    log_section("ORCH", "Spec Enrichment")
    memory = getattr(ctx, "memory", None)
    # Recall prior spec templates to keep auto-generated specs consistent.
    memory_block = _build_memory_block(task, memory)

    evidence_packs: list[EvidencePack] = []
    used_web_search = False
    # Optional web search when the task likely depends on external or time-sensitive facts.
    if _should_use_web_search(task):
        used_web_search = True
        evidence_packs = await _collect_spec_evidence(task, ctx, agent_pool, session)

    evidence_summary = _summarize_evidence(evidence_packs)

    designer_prompt = build_spec_designer_prompt(task, memory_block=memory_block, evidence_summary=evidence_summary)
    if should_show_prompts():
        log_event(
            "ORCH",
            "spec_prompt",
            "spec designer prompt preview",
            level="debug",
            data={"preview": clip_text(designer_prompt)},
        )
    designer_patch = await _run_patch_agent(
        agent_pool.get("spec_designer"),
        designer_prompt,
        ctx,
        session=session,
        workflow_name="spec_design",
    )

    critic_prompt = build_spec_critic_prompt(task, designer_patch.model_dump(mode="json"), memory_block=memory_block)
    if should_show_prompts():
        log_event(
            "ORCH",
            "spec_prompt",
            "spec critic prompt preview",
            level="debug",
            data={"preview": clip_text(critic_prompt)},
        )
    critic_patch = await _run_patch_agent(
        agent_pool.get("spec_critic"),
        critic_prompt,
        ctx,
        session=session,
        workflow_name="spec_critic",
    )

    merged_patch = _merge_patches(designer_patch, critic_patch)
    merged_patch.used_web_search = used_web_search

    # Apply the patch without overwriting user-provided specs.
    enriched = _apply_patch(task, merged_patch, evidence_packs)
    log_event(
        "ORCH",
        "spec_enriched",
        "spec enrichment complete",
        data={
            "constraints": len(enriched.constraints),
            "success_criteria": len(enriched.success_criteria),
            "spec_source": enriched.spec_source,
        },
    )

    if memory and memory.enabled:
        _write_spec_memory(enriched, memory)

    return enriched


def _build_memory_block(task: TaskSpec, memory) -> str | None:
    if not memory or not memory.enabled:
        return None
    global_recall = memory.recall_global(
        query=f"{task.goal} constraints success criteria template",
        k=memory.top_k,
    )
    agent_recall = memory.recall_agent("spec_designer", query=task.goal, k=memory.top_k)
    combined = _dedupe_records([*global_recall, *agent_recall])
    return format_memory_block(combined, title="Spec Memory")


def _should_use_web_search(task: TaskSpec) -> bool:
    mode = (task.allow_web_search_for_spec or "auto").lower()
    if mode == "off":
        return False
    if mode == "on":
        return True
    text = " ".join([task.goal, *task.constraints, *task.success_criteria]).lower()
    keywords = (
        "latest",
        "recent",
        "today",
        "current",
        "new",
        "news",
        "2024",
        "2025",
        "policy",
        "regulation",
        "standard",
        "specification",
        "api",
        "version",
    )
    return any(keyword in text for keyword in keywords)


async def _collect_spec_evidence(
    task: TaskSpec, ctx, agent_pool: dict[str, object], session=None
) -> list[EvidencePack]:
    from utils import clip_text, log_event, should_show_outputs, should_show_prompts

    if "researcher" not in agent_pool:
        return []

    # Cache evidence by query to avoid repeated searches within the same run.
    queries = _spec_queries(task, task.spec_max_search_queries)
    evidence: list[EvidencePack] = []
    for idx, query in enumerate(queries, start=1):
        request = EvidenceRequest(query=query, max_sources=6, require_citations=True)
        cache_key = _spec_cache_key(request)
        cached = ctx.cache.get(cache_key)
        if isinstance(cached, EvidencePack):
            evidence.append(cached)
            continue
        prompt = build_evidence_prompt(request)
        if should_show_prompts():
            log_event(
                "ORCH",
                "spec_evidence_prompt",
                "spec evidence prompt preview",
                level="debug",
                data={"preview": clip_text(prompt)},
            )
        result = await run_agent(
            agent_pool["researcher"],
            prompt,
            ctx,
            session=session,
            workflow_name=f"spec_evidence:{idx}",
            max_turns=4,
        )
        pack = _coerce_evidence_pack(request, getattr(result, "final_output", None))
        if not pack:
            continue
        ctx.cache.set(cache_key, pack)
        evidence.append(pack)
        log_event(
            "TOOLS",
            "web_search_evidence",
            "spec evidence collected",
            data=_evidence_log_payload(pack),
        )
        if should_show_outputs():
            log_event(
                "ORCH",
                "spec_evidence",
                "spec evidence summary preview",
                level="debug",
                data={"preview": clip_text(pack.summary)},
            )
    return evidence


def _spec_queries(task: TaskSpec, limit: int) -> list[str]:
    if limit <= 0:
        return []
    base = f"{task.goal} constraints success criteria"
    queries = [base]
    return queries[: max(1, limit)]


def _spec_cache_key(request: EvidenceRequest) -> str:
    payload = request.model_dump()
    if payload.get("allowed_domains"):
        payload["allowed_domains"] = sorted(payload["allowed_domains"])
    return f"spec_evidence:{json.dumps(payload, sort_keys=True, ensure_ascii=True)}"


async def _run_patch_agent(agent, prompt: str, ctx, session=None, workflow_name: str = "spec_patch") -> TaskSpecPatch:
    if agent is None:
        return TaskSpecPatch()
    result = await run_agent(
        agent,
        prompt,
        ctx,
        session=session,
        workflow_name=workflow_name,
        max_turns=4,
    )
    return _coerce_patch(getattr(result, "final_output", None))


def _coerce_patch(obj: object | None) -> TaskSpecPatch:
    if isinstance(obj, TaskSpecPatch):
        return obj
    if isinstance(obj, dict):
        try:
            return TaskSpecPatch(**obj)
        except Exception:
            return TaskSpecPatch()
    return TaskSpecPatch()


def _merge_patches(designer: TaskSpecPatch, critic: TaskSpecPatch) -> TaskSpecPatch:
    # Critic refines or overrides designer suggestions when present.
    merged_constraints = _merge_list(designer.constraints, critic.constraints)
    merged_criteria = _merge_list(designer.success_criteria, critic.success_criteria)
    confidence = critic.spec_confidence or designer.spec_confidence
    if not merged_constraints and not merged_criteria:
        confidence = 0.0
    merged = TaskSpecPatch(
        constraints=merged_constraints,
        success_criteria=merged_criteria,
        spec_notes=critic.spec_notes or designer.spec_notes,
        spec_confidence=confidence,
        used_web_search=designer.used_web_search or critic.used_web_search,
    )
    return merged


def _apply_patch(task: TaskSpec, patch: TaskSpecPatch, evidence_packs: list[EvidencePack]) -> TaskSpec:
    constraints = task.constraints if task.constraints else patch.constraints
    success = task.success_criteria if task.success_criteria else patch.success_criteria

    spec_source = "user"
    if not task.constraints and not task.success_criteria:
        spec_source = "auto"
    elif not task.constraints or not task.success_criteria:
        spec_source = "mixed"

    return task.model_copy(
        update={
            "constraints": constraints,
            "success_criteria": success,
            "spec_source": spec_source,
            "spec_confidence": patch.spec_confidence,
            "spec_notes": patch.spec_notes or None,
            "spec_evidence": evidence_packs,
        }
    )


def _coerce_evidence_pack(request: EvidenceRequest, data: object | None) -> EvidencePack | None:
    if data is None:
        return None
    if isinstance(data, EvidencePack):
        return data
    if isinstance(data, dict):
        try:
            payload = dict(data)
            req_payload = payload.get("request")
            if not req_payload:
                payload["request"] = request.model_dump()
            elif isinstance(req_payload, dict) and req_payload.get("allowed_domains") is None:
                req_payload = dict(req_payload)
                req_payload["allowed_domains"] = []
                payload["request"] = req_payload
            return EvidencePack(**payload)
        except Exception:
            return None
    return None


def _summarize_evidence(packs: Iterable[EvidencePack]) -> str | None:
    summaries = [pack.summary for pack in packs if pack.summary]
    if not summaries:
        return None
    return "\n".join(f"- {summary}" for summary in summaries)


def _evidence_log_payload(pack: EvidencePack, limit: int = 3) -> dict[str, object]:
    sources: list[dict[str, str]] = []
    for item in pack.citations[:limit]:
        sources.append(
            {
                "title": item.title or "Untitled",
                "url": item.url,
                "snippet": (item.snippet or "")[:180],
            }
        )
    return {
        "query": pack.request.query,
        "summary_preview": pack.summary[:240],
        "sources": sources,
    }


def _dedupe_records(records: list[dict]) -> list[dict]:
    seen = set()
    out = []
    for record in records:
        text = record.get("text") if isinstance(record, dict) else str(record)
        if not text or text in seen:
            continue
        seen.add(text)
        out.append(record)
    return out


def _merge_list(a: list[str], b: list[str]) -> list[str]:
    merged = []
    seen = set()
    for item in a + b:
        norm = item.strip()
        if not norm or norm in seen:
            continue
        seen.add(norm)
        merged.append(norm)
    return merged


def _write_spec_memory(task: TaskSpec, memory) -> None:
    # Persist a concise spec template so future spec enrichment can recall it.
    note = [
        f"TASK: {task.goal}",
        f"CONSTRAINTS: {', '.join(task.constraints) if task.constraints else 'None'}",
        f"CRITERIA: {', '.join(task.success_criteria) if task.success_criteria else 'None'}",
    ]
    memory.write_global("\n".join(note), labels=["spec"], category="rule")
