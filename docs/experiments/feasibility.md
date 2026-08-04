# Experiment feasibility review

Every benchmark, baseline, and harness proposed for the v2 rewrite was checked
by an independent research pass and then by an adversarial verifier whose job
was to *refute* the first pass. Several proposals did not survive. This
document records what can actually be run, what cannot, and what has to be
built.

Method: agents were required to fetch pages rather than rely on search
snippets, and to report `not_found` rather than produce a plausible citation.
Where a verifier contradicted the original report, the verifier's finding is
what appears below.

---

## The finding that changes the plan

> **No public benchmark tests heterogeneous composition.**

Every verified target is one of:

| Shape | Targets |
|---|---|
| a single agent in one pre-provisioned container | RoadmapBench, SciAgentGym, scBench-Long, SpatialBench-Long |
| N prompt personas in one Python process on a shared memory bus | MARBLE — with `communication: false` in its own exemplar config |
| homogeneous self-play where one agent is the ceiling by construction | CooperBench — solo beats coop on all 8 leaderboard rows |
| offline forensics over frozen JSON traces | Who&When, MAST |

Heterogeneity in all of them is *across* tasks, never *within* one. A task never
requires reconciling two independently developed components with incompatible
environments or data conventions.

The consequence is direct: if the paper submits heterogeneous composition as its
headline claim backed by these suites, a reviewer will reply that a strong
single agent explains the results — and on CooperBench the published data
already says exactly that. **A self-built benchmark is not scope creep here; it
is the experimental contribution.**

This also validates the three task regimes in `agentcoop/bench/`
(`single_sufficient`, `multi_necessary`, `multi_harmful`) and the
single-agent-sufficiency screen: without them, there is no way to show that
composition was warranted.

---

## Verdicts

### Drop — do not schedule these

| Target | Why |
|---|---|
| **SpatialBench-Long** | Real paper (arXiv 2605.28065). 4 of 24 evals public; **none has a `grader` field** although LatchBio's own spec marks it required; all 9 data nodes sit behind an authenticated Latch platform. `latch-eval-tools` is "© LatchBio" with no open licence; `eval-graders` has **no LICENSE file at all**. A leaderboard with a paper attached, not a self-serve benchmark. |
| **MASS** | Real ICLR'26 paper, **zero code**. DeepMind released nothing; the one candidate reimplementation ships GEPA/MIPRO, not MASS. Reproducing it is a multi-week project against a Gemini-1.5-Pro-era design. Cite as related work only. |
| **AgentFixer** | Real peer-reviewed paper (DOI verified via CrossRef), **zero artifacts**. Also: its released evaluation has **ground-truth leakage in all three methods** — `The Answer for the problem is: {ground_truth}` is injected into the prompt — and scores by substring containment rather than equality. Do not cite its numbers as a baseline. |
| **Who&When Pro** | Paper real; the repo contains a README and one PNG. The "Data" link is a dead `#`. |
| **"Interaction-centric failure taxonomies"** | **Not a paper.** It is a descriptive phrase people apply to MAST's inter-agent-misalignment category. Letting this into related work would be a fabricated citation. Cite MAST's taxonomy branch instead. |
| **MARBLE Minecraft / bargaining** | Minecraft: six required assets missing from a clean clone (issue #243, open since 2026-03-09, zero comments), plus a live Minecraft server and the mineflayer bridge. Bargaining: **1.78 MB of task JSONL ships with no implementation whatsoever** — no env, no config, no runner, no evaluator. |
| **MARBLE full grid** | 4 topologies × {1,3,5,7} agents × 5 models × seeds ≈ 50–100× a single pass, chasing a published **3%** effect measured at temperature 0.7 with no released seeds. |
| **MetaAgent / MetaAgent-X** | MetaAgent has no LICENSE (legally unclear to vendor) and no packaged eval data. MetaAgent-X is a VERL+vLLM RL *trainer*, not an API-callable designer. |
| **SciAgentGym's SPL and L1/L2/L3 analysis** | Not reproducible from the release — SPL is not implemented, and the only scoring entry point is `_calculate_answer_score`. The horizon-degradation headline cannot be re-derived. |

### Primary — these can carry claims

| Target | What it buys | Required fix before use |
|---|---|---|
| **Self-built AgentCo-Op-Bench** | the heterogeneous-composition claim, and every diagnosis/repair metric | must be constructed; 6–10 weeks |
| **RoadmapBench** | a real long-horizon quantitative result (115 version-upgrade tasks, median ~3,700 lines / 51 files) | patch `compute_metrics.py`; provision ~350 GB; **`reward.json` is not a per-target vector**, so the planned multi-objective readout must be built |
| **Who&When** | causal failure attribution against a published ceiling of 53.5% / 14.2% — embarrassingly low, i.e. real headroom | patch the substring-containment scorer, then re-derive baselines; your numbers will not be comparable to the paper's |
| **Codex CLI + Claude Code headless** | the single-strong-agent control arm *and* the pluggable executor backend | pin CLI versions, auto-update off, hermetic flags, API-key auth, n≥3–5 with variance (neither exposes a seed) |
| **AFlow + EvoAgentX** | automatic-workflow-design baselines — "we beat no automatic-workflow baseline" would be fatal | AFlow's tarball covers only 6 of its 12 adapters; EvoAgentX's HotpotQA host is dead (DNS gone) |

### Secondary — cheap, real, but not headline-bearing

CooperBench (**score on `merge.status == 'clean'`, not `both_passed`** — when the
naive merge conflicts the harness falls back to testing one agent's patch alone
and can award a PASS; all four of `openhands_sdk`'s passes came through that
fallback), MAST's 14-mode taxonomy, TraceElephant, SciAgentGym as a
typed-composition and silent-tool-failure testbed, ADAS/GPTSwarm, MaAS as a
cost-vs-accuracy Pareto comparator.

### Sanity-check only

The six v1 benchmarks (HotpotQA, DROP, HumanEval, MBPP, GSM8K, MATH) move to an
appendix. scBench-Long becomes a qualitative case study — its refusal to
collapse pass/fail, rubric, cost, and turns into one number is the best
ready-made articulation of the "no scalar metric exists" motivation, and its 20
shipped trajectories plus SpatialBench-Long's 48 are free failure-analysis
substrate at zero API cost. MARBLE keeps only the database scenario.

---

## Budget

| Line item | Estimate | Note |
|---|---|---|
| RoadmapBench | **$1,000–1,500/pass** central; $400–500 at 85–90% cache hit; **~$2,600 uncached** | Measure the cache hit rate on a 10-task pilot first — this one number moves the budget 6×. |
| Single-agent control arms | ~$1–5/task; 300 tasks × 3 seeds ≈ $900–4,500 per arm | Two control arms + one AgentCo-Op arm ≈ **$3k–13k per full pass**. |
| Who&When | ~$3.50 (`all_at_once`), ~$10–12 (binary search), ~$112 (step-by-step) | Best value on the list. |
| Trajectory mining | $0 | Offline. |

Two cost-control warnings:

- **Claude Code's `total_cost_usd` may understate real spend** — 1-hour-TTL cache
  writes appear to be priced at the 5-minute 1.25× multiplier instead of 2×,
  roughly 33% low on cache-write-heavy runs. Reconcile against an invoice.
- **Codex emits no dollar figure and has no budget ceiling flag.** A runaway
  agentic loop there is financially unbounded. Ship your own price table,
  accounting, and a wall-clock + token kill-switch before running it at scale.

---

## Design requirements for the self-built benchmark

In priority order, from the review:

1. **Single-agent-sufficiency screen.** Before a task enters the suite, measure
   its solo ceiling with Codex and Claude Code headless — same repo snapshot,
   same sandbox, n ≥ 5. Any task solo solves above threshold is a distractor;
   cut it. Report the screen: it is the strongest available defence and costs
   almost nothing. Tasks must require reconciling genuinely conflicting
   dependency closures, incompatible typed interfaces, or cross-environment
   handoffs that cannot fit one context window with one toolchain.
   *If such a task cannot be constructed, that is a finding worth having in
   month 1 rather than month 6.*
2. **Equal-budget baselines.** Match dollars, tokens, and wall-clock — not
   turns. Otherwise composition merely buys more compute and the comparison is
   uninterpretable. Give the single agent the same total budget the workflow
   consumes, and report cost-matched Pareto curves.
3. **Coding-agent baselines at parity.** Pinned CLI versions in a container with
   auto-update off, hermetic flags, API-key auth, n ≥ 3–5 per cell with reported
   variance. Exact reproducibility is unattainable — neither CLI exposes a seed —
   so variance must be reported rather than hidden.
4. **Blind fault injection.** The compiler must not see the fault catalogue at
   design time, and injected faults should be sampled from a pre-registered
   taxonomy. Reusing MAST's 14 modes makes the diagnosis output legible to
   reviewers.

---

## Scheduling warning

The Codex / Claude Code integration is comfortable, tractable work that can
quietly absorb a month while the actual bottleneck — sourcing tasks where
composition beats one strong agent — goes untouched. Timebox it to two weeks and
keep benchmark construction on the critical path.

---

*Open item: a separate survey is checking the SUPER / EnvBench / Terminal-Bench
genre ("install and run N heterogeneous research codebases"), where environment
heterogeneity is the task itself, for use as a substrate to build on rather than
starting wholly from scratch.*
