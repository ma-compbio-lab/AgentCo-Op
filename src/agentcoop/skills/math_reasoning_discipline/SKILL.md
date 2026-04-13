---
name: math-reasoning-discipline
description: Structured step-by-step mathematical reasoning with code verification.
type: agent-skill
domain_tags:
  - math
  - symbolic_reasoning
  - computation
capability_tags:
  - chain_of_thought
  - code_verification
  - exact_answer
shareable: true
---

## Mathematical Reasoning Protocol

1. **Read the problem carefully.** Identify what is being asked, what quantities are given, and what form the answer should take (integer, fraction, expression, etc.).

2. **Plan your approach.** Before computing, state which mathematical technique applies (algebra, combinatorics, number theory, geometry, trigonometry, etc.).

3. **Work step by step.** Show each step of your reasoning explicitly. Do not skip steps or jump to conclusions. Each step should follow logically from the previous one.

4. **Verify with a different method when possible.** If you solved algebraically, check with a numerical substitution. If you counted, verify the total makes sense.

5. **Preserve exact forms.** Unless the problem explicitly asks for a decimal approximation, keep answers as fractions, radicals, or symbolic expressions. Never collapse pi, sqrt, or rational expressions into decimals.

6. **State the final answer last.** Only after completing all reasoning steps, state the final answer clearly.

7. **For computational problems:** When the problem involves counting, optimization, or numerical verification, prefer writing executable code (Python with SymPy) to compute the answer rather than reasoning about it purely in natural language. Code execution is more reliable than mental arithmetic.
