from __future__ import annotations

import json
from typing import Any, Mapping


PATCH_PLANNER_SYSTEM_PROMPT = """You are PatchPlanner.
Your job: propose MINIMAL structural edits to the current workflow blueprint to fix failures or reduce cost, under budget constraints.

Rules:
- Only use allowed patch operations.
- Prefer 1-3 ops. Never exceed 5 ops.
- Preserve acyclic structure. Do not introduce cycles.
- If the failure is environment/tool-related, fix that BEFORE adding more reasoning agents.
- Do NOT redesign the whole workflow. Use surgical edits.
- Output MUST be valid JSON matching PatchPlan schema exactly (no markdown, no extra keys).
""".strip()


ALLOWED_PATCH_OPS = """ALLOWED_PATCH_OPS (with param hints):
1) ReplaceModel: target=node_id, params={provider,name,temperature,max_output_tokens}
2) SwapTool: target=node_id, params={from:{server,tool}, to:{server,tool}}
3) InsertNode: params={new_node:NodeSpec, position:{before|after|between}, anchors:{node_id(s)}, connect:{edges...}}
4) RemoveNode: target=node_id
5) InsertEdge: params={src,dst,mapping}
6) RemoveEdge: target=edge_id
7) AddGate: params={gate:GateSpec}
8) PruneSubgraph: target=subgraph_id
9) TightenContract: target=node_id, params={io:{input_schema|output_schema|invariants}}
10) RelaxContract: target=node_id, params={io:{input_schema|output_schema|invariants}}
11) ChangeSandbox: target=node_id, params={sandbox:{image,env,cmd,mcp_transport}}
12) AddHardCheck: params={hard_check:HardCheck}
13) AddSoftJudge: params={soft_judge:SoftJudge}""".strip()


def build_patch_planner_user_prompt(
    *,
    remaining_budget: Mapping[str, Any],
    execution_report: Mapping[str, Any],
    blame_candidates: list[Mapping[str, Any]],
    workflow_excerpt: Mapping[str, Any],
    task_description: str,
) -> str:
    return (
        "CURRENT_BUDGET:\n"
        f"- remaining_usd: {remaining_budget.get('remaining_usd')}\n"
        f"- remaining_tokens: {remaining_budget.get('remaining_tokens')}\n"
        f"- remaining_wall_time_s: {remaining_budget.get('remaining_wall_time_s')}\n\n"
        "EXECUTION_REPORT (structured):\n"
        f"{json.dumps(execution_report, ensure_ascii=True, indent=2, sort_keys=True)}\n\n"
        "TOP BLAME CANDIDATES:\n"
        f"{json.dumps(blame_candidates, ensure_ascii=True, indent=2, sort_keys=True)}\n\n"
        "CURRENT_WORKFLOW_IR (abridged but sufficient):\n"
        f"{json.dumps(workflow_excerpt, ensure_ascii=True, indent=2, sort_keys=True)}\n\n"
        f"{ALLOWED_PATCH_OPS}\n\n"
        "TASK CONTEXT:\n"
        f"{task_description}\n\n"
        "Return a PatchPlan JSON."
    )

