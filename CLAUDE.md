# CLAUDE.md

Behavioral guidelines to reduce common LLM coding mistakes. Merge with project-specific instructions as needed.

**Tradeoff:** These guidelines bias toward caution over speed. For trivial tasks, use judgment.

## 1. Think Before Coding

**Don't assume. Don't hide confusion. Surface tradeoffs.**

Before implementing:
- State your assumptions explicitly. If uncertain, ask.
- If multiple interpretations exist, present them - don't pick silently.
- If a simpler approach exists, say so. Push back when warranted.
- If something is unclear, stop. Name what's confusing. Ask.

## 2. Simplicity First

**Minimum code that solves the problem. Nothing speculative.**

- No features beyond what was asked.
- No abstractions for single-use code.
- No "flexibility" or "configurability" that wasn't requested.
- No error handling for impossible scenarios.
- If you write 200 lines and it could be 50, rewrite it.

Ask yourself: "Would a senior engineer say this is overcomplicated?" If yes, simplify.

## 3. Surgical Changes

**Touch only what you must. Clean up only your own mess.**

When editing existing code:
- Don't "improve" adjacent code, comments, or formatting.
- Don't refactor things that aren't broken.
- Match existing style, even if you'd do it differently.
- If you notice unrelated dead code, mention it - don't delete it.

When your changes create orphans:
- Remove imports/variables/functions that YOUR changes made unused.
- Don't remove pre-existing dead code unless asked.

The test: Every changed line should trace directly to the user's request.

## 4. Goal-Driven Execution

**Define success criteria. Loop until verified.**

Transform tasks into verifiable goals:
- "Add validation" → "Write tests for invalid inputs, then make them pass"
- "Fix the bug" → "Write a test that reproduces it, then make it pass"
- "Refactor X" → "Ensure tests pass before and after"

For multi-step tasks, state a brief plan:
```
1. [Step] → verify: [check]
2. [Step] → verify: [check]
3. [Step] → verify: [check]
```

Strong success criteria let you loop independently. Weak criteria ("make it work") require constant clarification.


## 5. Real world based tests.

Testing must be conducted using real-world data; you are expected to independently identify, locate, download, and process appropriate real-world test datasets. While you may use self-generated "toy data" for preliminary testing, you must *never* rely on such synthetic data for final validation. The code ultimately delivered must be fully tested and verified against real-world datasets.


## 6. Evidence-Based Repair

When code doesn't work:
- Don't guess. Check logs, add print statements, inspect intermediate outputs.
- Identify the specific failure point and error message.
- Formulate a hypothesis: "I think it's because X is None, which shouldn't be."
- Test the hypothesis with a targeted change. Verify if it fixes the issue. If not, repeat the process. Avoid making multiple simultaneous changes without verification.
- Every change should be motivated by a specific observed failure, not a vague intuition.

## SKILLS

### Planning with Files.
Use planning-with-files for complex tasks (more than two steps) to monitor execution state and enable more effective repair. 

### superpowers

Use `superpowers` for design the framework of the code, and the structure of the code. It can be used to generate the initial codebase, and also to refactor and improve the codebase iteratively.