"""Per-role × per-dataset prompt templates.

The compiler binds nodes to roles (solver, specialist, reviewer, formatter,
programmer, router, planner, extractor, integrator). Each role has a
default prompt; for specific datasets we override with a more focused
template. The module also decides whether the model should respond in JSON
mode and how to parse the raw model text into a structured `output` dict
suitable for `NodeResult.output`.

Design rules (keep simple per CLAUDE.md §2):
- Solvers/programmers/specialists answer in free-form text. They include a
  short final-answer marker that downstream graders / formatters can pick up.
- Formatters / extractors / reviewers / routers respond in JSON.
- Datasets we know about: gsm8k, math, humaneval, mbpp, hotpotqa, drop.
- Anything else uses the generic role prompts.
"""

from __future__ import annotations

import json
import re
from typing import Any


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def build_messages(
    *,
    role: str,
    node_id: str,
    dataset: str | None,
    payload: dict[str, Any],
) -> tuple[str, str, bool]:
    """Return (system, user, want_json) for an LLM node call.

    `dataset` is the AFlow-aligned dataset name (gsm8k / math / humaneval /
    mbpp / hotpotqa / drop) when known; None for ad-hoc tasks.
    """
    role = (role or "specialist").lower()
    dataset = (dataset or "").lower()
    system = _system_prompt(role, dataset, node_id)
    user = _user_prompt(role, dataset, node_id, payload)
    want_json = _wants_json(role, dataset)
    if want_json and "json" not in user.lower() and "json" not in system.lower():
        user += "\n\nReturn ONLY a single JSON object."
    return system, user, want_json


def parse_output(
    *,
    role: str,
    node_id: str,
    dataset: str | None,
    raw_text: str,
) -> dict[str, Any]:
    """Convert raw model text into the structured `output` dict.

    For free-form roles (solver, programmer, ...) we extract the final
    answer / code with a regex. For JSON-mode roles we parse the JSON.
    Always include a `rationale_summary` key (truncated raw text) so the
    reviewer / integrator can quote it without leaking full chain-of-thought.
    """
    role = (role or "specialist").lower()
    dataset = (dataset or "").lower()
    raw_text = raw_text or ""
    if _wants_json(role, dataset):
        parsed = _parse_json(raw_text)
        if parsed is None:
            parsed = {"final_answer": "", "confidence": 0.3, "issues": ["json_parse_fail"]}
        else:
            # Repair LaTeX backslashes if the model dropped escapes (math).
            if dataset == "math" and isinstance(parsed.get("final_answer"), str):
                parsed["final_answer"] = _restore_latex_backslashes(parsed["final_answer"])
        parsed.setdefault("rationale_summary", _summarize(raw_text))
        return parsed

    if role == "programmer" or dataset in {"humaneval", "mbpp"}:
        code = _extract_code(raw_text)
        return {
            "code": code,
            "final_answer": code,
            "confidence": 0.7 if code else 0.2,
            "rationale_summary": _summarize(raw_text),
        }

    if dataset == "math":
        repaired = _restore_latex_backslashes(raw_text)
        boxed = _extract_boxed(repaired)
        if boxed is not None:
            return {
                "final_answer": boxed,
                "boxed": boxed,
                "confidence": 0.7,
                "rationale_summary": _summarize(repaired),
            }
        # fall through to last-line / final-line heuristic
    if dataset == "gsm8k":
        ans = _extract_after_marker(raw_text, "Answer:") or _extract_last_number(raw_text)
        return {
            "final_answer": ans or "",
            "confidence": 0.7 if ans else 0.2,
            "rationale_summary": _summarize(raw_text),
        }
    if dataset == "hotpotqa":
        ans = _extract_after_marker(raw_text, "Answer:") or raw_text.strip().splitlines()[-1].strip() if raw_text else ""
        return {
            "final_answer": ans or "",
            "confidence": 0.6 if ans else 0.2,
            "rationale_summary": _summarize(raw_text),
        }
    if dataset == "drop":
        ans = _extract_after_marker(raw_text, "Answer:") or raw_text.strip().splitlines()[-1].strip() if raw_text else ""
        return {
            "final_answer": ans or "",
            "confidence": 0.6 if ans else 0.2,
            "rationale_summary": _summarize(raw_text),
        }

    # Generic fallback.
    ans = _extract_after_marker(raw_text, "Answer:") or _extract_after_marker(raw_text, "Final answer:")
    if not ans:
        ans = raw_text.strip().splitlines()[-1].strip() if raw_text else ""
    return {
        "final_answer": ans or "",
        "confidence": 0.5 if ans else 0.2,
        "rationale_summary": _summarize(raw_text),
    }


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------


_JSON_ROLES = {"formatter", "extractor", "reviewer", "router", "integrator"}
# Datasets where extractors / passage readers should still answer in JSON
# (their output is structured data downstream nodes consume).
_DATASETS_WITH_STRUCTURED_EXTRACTORS = {"hotpotqa", "drop"}


def _wants_json(role: str, dataset: str) -> bool:
    # MATH formatter/extractor must stay in plain text — JSON mode mangles
    # LaTeX backslashes (`\boxed` → backspace). Reviewer/router answer with
    # short scalars in JSON, which is safe.
    if dataset == "math" and role in {"formatter", "extractor"}:
        return False
    if role in _JSON_ROLES:
        return True
    if role == "extractor" and dataset in _DATASETS_WITH_STRUCTURED_EXTRACTORS:
        return True
    return False


def _restore_latex_backslashes(s: str) -> str:
    """Undo JSON control-char folding for the common LaTeX prefixes.

    `json.loads` turns `"\\b..."` into the backspace character because the
    model dropped the second backslash. We restore the most common LaTeX
    starters (`\\b`, `\\f`, `\\t`, `\\v`) when followed by a letter.
    """
    if not s:
        return s
    out = []
    i = 0
    while i < len(s):
        c = s[i]
        nxt = s[i + 1] if i + 1 < len(s) else ""
        if c in "\b\f\t\v" and nxt.isalpha():
            out.append("\\")
            out.append({"\b": "b", "\f": "f", "\t": "t", "\v": "v"}[c])
        else:
            out.append(c)
        i += 1
    return "".join(out)


# ----- system prompts -------------------------------------------------------


def _system_prompt(role: str, dataset: str, node_id: str) -> str:
    # Highly specific overrides first.
    if dataset == "humaneval" or dataset == "mbpp":
        return _code_system_prompt(role, node_id)
    if dataset == "gsm8k":
        return _gsm8k_system_prompt(role, node_id)
    if dataset == "math":
        return _math_system_prompt(role, node_id)
    if dataset == "hotpotqa":
        return _hotpotqa_system_prompt(role, node_id)
    if dataset == "drop":
        return _drop_system_prompt(role, node_id)
    return _generic_system_prompt(role, node_id)


def _code_system_prompt(role: str, node_id: str) -> str:
    if role in {"solver", "programmer", "specialist"}:
        return (
            "You are an expert Python engineer. Implement the requested "
            "function so that all behavioral requirements in the docstring "
            "are met. Think briefly, then output ONLY a single fenced "
            "```python``` block containing the COMPLETE implementation, "
            "including the function signature, all imports it needs, and "
            "any helpers. Do not include the test harness, examples, or "
            "explanatory prose outside the code block."
        )
    if role in {"reviewer", "verifier"} or "repair" in node_id:
        return (
            "You are a senior code reviewer. The candidate implementation "
            "failed a sandbox test. Diagnose the root cause from the "
            "traceback, propose a minimal fix, and rewrite the FULL function "
            "in a single ```python``` block. Do not change the function "
            "signature. Do not add unrelated changes."
        )
    if role == "extractor" or role == "parser":
        return (
            "You are a Python parser. Extract the function signature, "
            "docstring, and any public examples from the prompt. Respond "
            "as JSON: {\"signature\": str, \"docstring\": str, \"examples\": "
            "[\"...\"], \"entry_point\": str, \"confidence\": float}."
        )
    if role == "router":
        return (
            "You are a code routing agent. Classify the task as one of "
            "{algorithm, string, math_code, data_structures, recursion}. "
            "Respond as JSON {\"category\": str, \"confidence\": float, "
            "\"rationale_summary\": str}."
        )
    if role == "integrator":
        return (
            "You are an integrator that compares multiple candidate Python "
            "implementations. Pick the most correct one based on the "
            "docstring and any provided test traces. Respond as JSON "
            "{\"final_answer\": <python code>, \"chosen_index\": int, "
            "\"confidence\": float, \"rationale_summary\": str}."
        )
    if role == "formatter":
        return (
            "You are a code formatter. Given a candidate implementation, "
            "ensure it parses, return ONLY a single fenced ```python``` "
            "block with the final code, then a JSON object on a new line: "
            "{\"final_answer\": <complete python code as string>, "
            "\"confidence\": float}."
        )
    return _generic_system_prompt(role, node_id)


def _gsm8k_system_prompt(role: str, node_id: str) -> str:
    if role in {"solver", "specialist", "router"} or "router" in node_id:
        return (
            "You are a careful grade-school math tutor. Solve the problem "
            "step by step, showing each arithmetic operation. End your "
            "response with a final line in the exact form `Answer: <number>` "
            "where <number> is a single integer or decimal with no units, "
            "no commas, and no extra text."
        )
    if role == "reviewer" or "repair" in node_id or "verify" in node_id:
        return (
            "You are an arithmetic verifier. Recompute the candidate "
            "solution from the original problem, independently. If the "
            "candidate's final number is wrong, return the corrected number. "
            "Respond as JSON: {\"verified\": bool, \"final_answer\": <number "
            "as string>, \"confidence\": float, \"rationale_summary\": str}."
        )
    if role == "formatter":
        return (
            "You are an answer extractor. Given a candidate solution, "
            "extract the final numeric answer. Respond as JSON: "
            "{\"final_answer\": <number as string>, \"confidence\": float}."
        )
    return _generic_system_prompt(role, node_id)


def _math_system_prompt(role: str, node_id: str) -> str:
    if role == "router" or "math_router" in node_id:
        # Lightweight router — classifies the math sub-domain so the
        # specialist downstream can adopt the right framing.
        return (
            "You classify competition math problems by sub-domain. Output "
            "JSON: {\"category\": one of [algebra, number_theory, "
            "combinatorics, probability, geometry, precalculus, "
            "trigonometry, other], \"hints\": [short string], "
            "\"confidence\": float, \"rationale_summary\": str}."
        )
    if role in {"solver", "specialist"}:
        return (
            "You are an expert competition mathematician (AMC/AIME level). "
            "Solve the problem rigorously, showing the key calculations. "
            "The final answer MUST appear inside `\\boxed{...}` as the very "
            "last expression in your response. Use exact forms (integers, "
            "fractions, surds, intervals) — do not round, do not include "
            "units inside the box."
        )
    if role == "reviewer" or "verify" in node_id or "repair" in node_id:
        return (
            "You are an independent verifier for competition math. Re-derive "
            "the answer from scratch and check whether the candidate matches. "
            "Respond as JSON: {\"verified\": bool, \"final_answer\": "
            "\"<answer string>\", \"confidence\": float, "
            "\"rationale_summary\": str}. The answer string is the content "
            "inside \\boxed{...} only — do not include the box itself."
        )
    if role == "formatter":
        # PLAIN TEXT — JSON mode mangles \boxed (\b → backspace).
        return (
            "You are a final-answer extractor for MATH problems. Read the "
            "candidate solution and output ONLY the LaTeX expression "
            "`\\boxed{...}` containing the final answer. No prose, no JSON, "
            "no commentary. If you cannot find a clear answer, output "
            "`\\boxed{?}`."
        )
    return _generic_system_prompt(role, node_id)


def _hotpotqa_system_prompt(role: str, node_id: str) -> str:
    if role in {"solver", "specialist"} or node_id in {"answerer", "main_agent"}:
        return (
            "You are a multi-hop HotpotQA expert. Workflow:\n"
            "1. Identify which entity / fact the question asks ABOUT (the "
            "unknown). Do NOT echo entities that are GIVEN in the question.\n"
            "2. Trace the multi-hop chain through the context — the answer "
            "is often only reachable by combining 2+ paragraphs.\n"
            "3. The answer is usually a short noun phrase or a yes/no.\n"
            "4. Verify by mentally substituting back into the question — "
            "the resulting statement must be supported by the context.\n"
            "End with `Answer: <span>` on its own line, where <span> is "
            "copied verbatim from the context when possible. For yes/no "
            "questions, use lowercase `yes` or `no`."
        )
    if role == "planner":
        return (
            "You are a query planner. Identify what entities and relations "
            "the question is about, and which paragraphs in the context "
            "are most relevant. Respond as JSON: {\"entities\": [str], "
            "\"relations\": [str], \"relevant_paragraphs\": [int], "
            "\"confidence\": float}."
        )
    if role == "extractor":
        return (
            "You are an evidence selector. From the context, copy the "
            "1–3 most relevant sentences. Respond as JSON: "
            "{\"evidence\": [str], \"confidence\": float}."
        )
    if role == "reviewer":
        return (
            "You are a QA reviewer. Verify that the candidate answer is "
            "supported by the evidence. Respond as JSON: "
            "{\"verified\": bool, \"final_answer\": str, \"confidence\": "
            "float, \"rationale_summary\": str}."
        )
    if role == "formatter":
        return (
            "You are a HotpotQA finalizer. Given a candidate answer, "
            "produce a concise final answer (1–4 words). Respond as JSON: "
            "{\"final_answer\": str, \"confidence\": float}."
        )
    return _generic_system_prompt(role, node_id)


def _drop_system_prompt(role: str, node_id: str) -> str:
    if role in {"solver", "specialist"} or node_id in {"numeric_reasoner", "main_agent"}:
        return (
            "You are a DROP reading-comprehension expert. Use ONLY the "
            "passage to answer. Answers may be a number, a date, a single "
            "span, or multiple spans separated by `; `. Show brief "
            "reasoning, then end with `Answer: <answer>` on its own line. "
            "Numbers must be plain (no commas, no units) — e.g., `1234` "
            "not `1,234 yards`."
        )
    if role == "extractor" or node_id == "passage_reader":
        return (
            "You are a passage reader. Extract the spans relevant to the "
            "question. Respond as JSON: {\"spans\": [str], \"numbers\": "
            "[str], \"dates\": [str], \"confidence\": float}."
        )
    if role == "specialist" and node_id == "op_classifier":
        return (
            "You are a DROP operation classifier. Decide whether the answer "
            "type is `number`, `date`, `span`, or `multi_span`, and if "
            "numeric, which arithmetic operation is needed (add, subtract, "
            "max, min, count, none). Respond as JSON: {\"answer_type\": str, "
            "\"operation\": str, \"confidence\": float}."
        )
    if role == "reviewer":
        return (
            "You are a DROP reviewer. Verify the candidate against the "
            "operation classification and the extracted spans. Respond as "
            "JSON: {\"verified\": bool, \"final_answer\": str, "
            "\"confidence\": float}."
        )
    if role == "formatter":
        return (
            "You are a DROP answer formatter. Output the final answer in "
            "the exact form expected by the official DROP grader (number "
            "with no commas, span verbatim, or multiple spans separated by "
            "`; `). Respond as JSON: {\"final_answer\": str, "
            "\"confidence\": float}."
        )
    return _generic_system_prompt(role, node_id)


def _generic_system_prompt(role: str, node_id: str) -> str:
    if role == "solver":
        return (
            "You solve problems carefully. Think step by step. End your "
            "response with `Answer: <final answer>` on its own line."
        )
    if role == "programmer":
        return (
            "You implement requested code. Output one fenced ```python``` "
            "block with the complete code. No prose outside the block."
        )
    if role == "router":
        return (
            "You route a task to a specialist category. Respond as JSON: "
            "{\"category\": str, \"confidence\": float, "
            "\"rationale_summary\": str}."
        )
    if role == "planner":
        return (
            "You are a planner. Decompose the task into ordered subtasks. "
            "Respond as JSON: {\"subtasks\": [str], \"confidence\": float}."
        )
    if role == "extractor":
        return (
            "You extract structured information. Respond as JSON: "
            "{\"items\": [str], \"confidence\": float}."
        )
    if role == "reviewer":
        return (
            "You are a reviewer. Determine whether the candidate answer is "
            "correct given the task. Respond as JSON: "
            "{\"verified\": bool, \"final_answer\": str, "
            "\"confidence\": float, \"rationale_summary\": str}."
        )
    if role == "integrator":
        return (
            "You integrate multiple candidate answers and return the most "
            "supported one. Respond as JSON: {\"final_answer\": str, "
            "\"confidence\": float, \"rationale_summary\": str}."
        )
    if role == "formatter":
        return (
            "You are an answer formatter. Output the final answer in the "
            "exact form requested. Respond as JSON: {\"final_answer\": "
            "str, \"confidence\": float}."
        )
    return (
        f"You are the `{role}` node in AgentCo-Op. Respond with helpful, "
        "concise output. End with `Answer: <answer>` if a final answer "
        "is expected."
    )


# ----- user prompts ---------------------------------------------------------


def _user_prompt(role: str, dataset: str, node_id: str, payload: dict[str, Any]) -> str:
    task_input = payload.get("task_input", {}) if isinstance(payload, dict) else {}
    if not isinstance(task_input, dict):
        task_input = {}
    base_task = task_input.get("task") or ""
    inner = task_input.get("input", {}) or {}

    parts: list[str] = []

    # Dataset-specific framing.
    if dataset == "hotpotqa":
        question = inner.get("question") or base_task
        ctx = inner.get("context")
        ctx_str = _format_hotpotqa_context(ctx)
        parts.append(f"QUESTION:\n{question}\n")
        if ctx_str:
            parts.append(f"CONTEXT:\n{ctx_str}\n")
    elif dataset == "drop":
        passage = inner.get("passage") or inner.get("context") or ""
        question = inner.get("question") or base_task
        if passage:
            parts.append(f"PASSAGE:\n{passage}\n")
        parts.append(f"QUESTION:\n{question}\n")
    elif dataset in {"humaneval", "mbpp"}:
        prompt = inner.get("prompt") or base_task
        parts.append(f"TASK:\n{prompt}\n")
    elif dataset == "math":
        problem = inner.get("question") or inner.get("prompt") or base_task
        parts.append(f"PROBLEM:\n{problem}\n")
    elif dataset == "gsm8k":
        problem = inner.get("question") or inner.get("prompt") or base_task
        parts.append(f"PROBLEM:\n{problem}\n")
    else:
        if base_task:
            parts.append(f"TASK:\n{base_task}\n")

    # Inject prior outputs from upstream nodes if any.
    prior_lines: list[str] = []
    for k, v in payload.items():
        if not k.startswith("output:"):
            continue
        if v is None:
            continue
        src_id = k.split(":", 1)[1]
        if src_id == node_id:  # don't show our own previous output
            continue
        snippet = _compact_prior(v)
        if snippet:
            prior_lines.append(f"From `{src_id}`:\n{snippet}")
    if prior_lines:
        parts.append("PRIOR OUTPUTS (use as evidence):\n\n" + "\n\n".join(prior_lines))

    # Reviewer / formatter / repair: highlight the candidate to review.
    if role in {"reviewer", "formatter"} or "repair" in node_id:
        cand = _pick_candidate(payload)
        if cand:
            parts.append(f"CANDIDATE OUTPUT:\n{cand}\n")

    return "\n".join(parts).strip() or (base_task or "(no task input)")


def _format_hotpotqa_context(ctx: Any) -> str:
    """HotpotQA's context is `[[title, [sentence,...]], ...]`."""
    if not ctx:
        return ""
    lines: list[str] = []
    if isinstance(ctx, list):
        for i, item in enumerate(ctx):
            if isinstance(item, (list, tuple)) and len(item) == 2:
                title, sentences = item
                body = " ".join(sentences) if isinstance(sentences, list) else str(sentences)
                lines.append(f"[{i}] {title}: {body}")
            else:
                lines.append(f"[{i}] {item}")
    elif isinstance(ctx, dict):
        # newer HF schema: {"title": [...], "sentences": [[...], [...]]}
        titles = ctx.get("title", [])
        sentences = ctx.get("sentences", [])
        for i, (t, s) in enumerate(zip(titles, sentences)):
            body = " ".join(s) if isinstance(s, list) else str(s)
            lines.append(f"[{i}] {t}: {body}")
    else:
        lines.append(str(ctx))
    return "\n".join(lines)


def _compact_prior(value: Any) -> str:
    if isinstance(value, dict):
        # Prefer the most informative keys.
        for k in ("final_answer", "code", "answer", "evidence", "items", "spans"):
            if k in value and value[k] not in (None, "", []):
                v = value[k]
                if isinstance(v, list):
                    return f"{k}: " + "; ".join(str(x) for x in v[:6])
                return f"{k}: {str(v)[:600]}"
        # Fallback: small JSON blob.
        try:
            blob = json.dumps(value, ensure_ascii=False)
        except TypeError:
            blob = str(value)
        return blob[:600]
    if isinstance(value, list):
        return "; ".join(str(x) for x in value[:6])[:600]
    return str(value)[:600]


def _pick_candidate(payload: dict[str, Any]) -> str:
    # Prefer the most recent solver/programmer output.
    keys = [k for k in payload.keys() if k.startswith("output:")]
    if not keys:
        return ""
    candidates: list[tuple[int, str]] = []
    for i, k in enumerate(keys):
        v = payload[k]
        if isinstance(v, dict):
            for kk in ("code", "final_answer", "answer"):
                if v.get(kk):
                    candidates.append((i, str(v[kk])[:1500]))
                    break
    if not candidates:
        return ""
    return candidates[-1][1]


# ----- output parsing -------------------------------------------------------


def _parse_json(text: str) -> dict[str, Any] | None:
    if not text:
        return None
    s = text.strip()
    # Strip markdown fencing.
    if s.startswith("```"):
        s = re.sub(r"^```(?:json)?\s*\n", "", s)
        s = re.sub(r"\n```\s*$", "", s)
    # Find the outermost JSON object.
    try:
        return json.loads(s)
    except Exception:
        pass
    m = re.search(r"\{.*\}", s, re.DOTALL)
    if not m:
        return None
    try:
        return json.loads(m.group(0))
    except Exception:
        return None


_CODE_FENCE_RE = re.compile(r"```(?:python)?\s*\n(.*?)\n```", re.DOTALL)


def _extract_code(text: str) -> str:
    if not text:
        return ""
    m = _CODE_FENCE_RE.search(text)
    if m:
        return m.group(1).strip()
    # Fallback: assume the whole thing is code minus surrounding prose.
    lines = text.strip().splitlines()
    code_lines = [ln for ln in lines if ln.startswith(("def ", "class ", "import ", "from ", "    "))]
    return "\n".join(code_lines).strip() or text.strip()


def _extract_boxed(text: str) -> str | None:
    if not text:
        return None
    idx = text.rfind(r"\boxed{")
    if idx == -1:
        return None
    depth = 0
    i = idx + len(r"\boxed{")
    start = i
    while i < len(text):
        c = text[i]
        if c == "{":
            depth += 1
        elif c == "}":
            if depth == 0:
                return text[start:i].strip()
            depth -= 1
        i += 1
    return None


def _extract_after_marker(text: str, marker: str) -> str | None:
    if not text:
        return None
    idx = text.lower().rfind(marker.lower())
    if idx == -1:
        return None
    rest = text[idx + len(marker):].strip()
    # Take only up to the first newline.
    return rest.splitlines()[0].strip() if rest else None


_NUMBER_RE = re.compile(r"[-+]?\d[\d,]*(?:\.\d+)?")


def _extract_last_number(text: str) -> str | None:
    if not text:
        return None
    matches = _NUMBER_RE.findall(text)
    if not matches:
        return None
    return matches[-1].replace(",", "")


def _summarize(text: str, limit: int = 240) -> str:
    if not text:
        return ""
    s = " ".join(text.split())
    return s if len(s) <= limit else s[: limit - 1] + "…"


__all__ = ["build_messages", "parse_output"]
