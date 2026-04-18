---
name: ensemble-arbitration
type: agent-skill
description: Selector tactics for choosing among parallel reasoning paths — majority vote with code-execution tie-break and garbage rejection.
domain_tags:
  - ensemble
  - selection
  - arbitration
capability_tags:
  - majority_vote
  - tie_break
  - answer_selection
  - self_consistency
shareable: true
---

## Decision rules (in priority order)

1. **Majority among clean candidates.** If 2 or more candidates agree on the same answer (after light normalization — strip whitespace, percent signs, currency symbols), select that answer with high confidence.

2. **Detect garbage before voting.**
   - `None`, empty string, error messages, stack traces, or placeholder text → treat as invalid and exclude from the vote.
   - A decimal where the question asks for an exact form (e.g., `0.333...` when the answer should be `1/3`) → treat as suspicious.
   - A number that differs from all reasoning-path answers by an order of magnitude → likely a bug in the programmer path.

3. **Tie-break for numeric / code-verifiable answers:** prefer `executed_answer` when it is clean and at least one reasoning path agrees.

4. **Tie-break for textual / span answers:** prefer the reasoning-path answer. Programs rarely reconstruct exact text spans.

5. **All disagree:** prefer the executed answer for numeric questions; prefer the shorter, more direct answer for text questions (grader F1 penalizes verbosity).

6. **Uncertainty disclosure.** If confidence would be below 0.5, still return the best candidate — do not set `needs_review`. The review loop is disabled because weaker models revert correct answers.

## Output discipline

- Return only the final value — no units, no sentence wrapping, no trailing punctuation.
- Populate `output.arbitration_notes` with a one-sentence justification ("solver and programmer agree on 42, selecting").
- Set `confidence`: 0.95+ when ≥2 candidates agree, 0.7 when choosing among disagreement, 0.5 when all candidates are weak.
- Never set `needs_review=true`.
