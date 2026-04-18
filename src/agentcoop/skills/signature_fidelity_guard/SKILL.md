---
name: signature-fidelity-guard
type: agent-skill
description: Rewriter discipline — when rewriting from scratch, copy the function signature verbatim; hidden tests call by keyword and silently break on renamed parameters.
domain_tags:
  - coding
  - humaneval
  - mbpp
  - signature
capability_tags:
  - signature_preservation
  - parameter_names
  - keyword_compatibility
shareable: true
---

## The rule

When writing or rewriting a function, the signature — **name, parameter names, parameter order, default values, and type hints (if present)** — must match the prompt exactly.

This is non-obvious: hidden tests often use keyword arguments (`f(s="hello")`), so renaming `s` to `text` is a silent failure even if the logic is correct.

## Checklist

1. **Function name** — identical casing, underscores, and spelling.
2. **Parameter names** — identical, in the same order. Never "improve" a parameter name.
3. **Default values** — preserve `=None`, `=0`, `=[]`, `=()` exactly (but beware `=[]` / `=` mutable-default anti-pattern; if the prompt uses it, keep it).
4. **Type hints** — if the prompt has them, keep them. If not, do not add them (adding can cause import errors if `from typing import ...` is missing).
5. **Decorators** — preserve any `@staticmethod`, `@classmethod`, `@property`.
6. **Leading imports** — if the prompt imports specific names at the top, keep exactly those imports.
7. **Do not shadow builtins** with parameter names (e.g., `str`, `list`, `type`) unless the prompt already does so — then keep it.

## Anti-patterns (real failures)

- MBPP/0104: prompt had parameter `s`; rewriter renamed to `h`. Hidden test called `f(s="...")` — `TypeError: f() got unexpected keyword argument 's'`.
- MBPP/0130: prompt had parameter `str`; rewriter renamed to `s`. Same keyword-arg failure.
- HumanEval variants where the return type hint was stripped and a hidden test imported the function's `.__annotations__`.

## Copy-paste protocol

Copy the function signature line directly from the prompt as the FIRST line of your output. Do not retype — typos are common failure sources. Only edit the body below it.
