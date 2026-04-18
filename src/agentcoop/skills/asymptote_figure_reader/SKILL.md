---
name: asymptote-figure-reader
type: agent-skill
description: Solver discipline — when a problem includes an [asy]...[/asy] block, treat the coordinates in that block as ground truth for the figure, not the prose description.
domain_tags:
  - math
  - geometry
  - asymptote
  - figure_interpretation
capability_tags:
  - coordinate_extraction
  - figure_parsing
  - geometry
shareable: true
---

## Why this matters

MATH problems often ship with Asymptote (`[asy]...[/asy]`) code blocks that render the figure. The coordinates in the asy block are the **literal truth** about the figure's shape, side lengths, and angles. The prose description is often a simplification or contains measurements that conflict with the figure.

LLMs frequently ignore the asy code because it looks like unfamiliar syntax. The result: wrong side lengths, wrong heights, wrong coordinates → wrong answer.

## Extraction protocol

When the problem contains `[asy]`:

1. **Find the `pair` / `real` / explicit coordinate assignments.** Example:
   ```
   pair A = (0,0);
   pair B = (4,0);
   pair C = (2, 2*sqrt(3));
   ```
   ⇒ Triangle ABC has vertices (0,0), (4,0), (2, 2√3) — equilateral with side 4.

2. **Find `draw(...)` calls.** `draw(A--B--C--cycle)` confirms which segments exist.

3. **Find `label(...)` and `filldraw(...)` calls.** These mark specific points, angles, or shaded regions.

4. **Recompute side lengths and angles from the coordinates**, not from prose:
   ```
   |AB| = sqrt((4-0)^2 + 0^2) = 4
   |AC| = sqrt(4 + 12) = 4
   ```

5. **If the prose says one thing and the asy says another, the asy wins.** The prose is often a narrator's simplification; the figure is the actual problem.

## Example

Prose: "Three triangles on CD=12, so each base is 4."
Asy: `pair D = (0,0); pair X = (2, 2*sqrt(3)); ...` — base is 2, not 4.

Answer uses base = 2 (from asy), not 4 (from prose math).

## When asy is not present

Fall back to prose. This skill only applies when the asy block is explicit in the problem.

## Anti-patterns (real failures)

- MATH task 0005: three-triangle geometry, prose-derived base = 4, asy-derived base = 2. Solver used 4 → wrong height → wrong area → wrong answer.
- Any circle/sector problem where the asy shows the actual radius and the prose uses a placeholder.
