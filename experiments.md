# AgentCo-Op Experiments

This file is the short experiment overview. Detailed implementation guides are split into:

- `benchmarks.md`: AFlow-aligned standard benchmark protocol.
- `docs/experiments/case_study.md`: detailed biological and dynamic-workflow case studies.

## 1. Experimental goals

AgentCo-Op should be evaluated as a **task-conditioned multi-agent workflow compiler**, not as another validation-set topology search method. The experiments should test four claims:

1. **Compilation instead of search**: AgentCo-Op can synthesize a reasonable workflow from a task profile, meta-skills, agent-skills, tools, and repo backends without repeatedly searching over a validation metric.
2. **Simplicity first**: simple tasks should route to simple direct or single-agent workflows, while complex tasks should receive additional specialists, tools, or gated repair only when justified.
3. **Dynamic refinement**: runtime evidence such as failed tests, invalid artifacts, low verifier confidence, or specialist disagreement can trigger local topology repair without re-running global topology search.
4. **Specialized collaboration**: AgentCo-Op can coordinate heterogeneous execution nodes, including LLM-backed agents, R/Python bioinformatics tools, GPU foundation-model repos, and sandboxed GitHub repos.

## 2. Experiment tracks

### Track A: AFlow-aligned standard benchmarks

Use HotpotQA, DROP, HumanEval, MBPP, GSM8K, and MATH with the same high-level data protocol and metrics as AFlow. The purpose is to compare AgentCo-Op against direct prompting, common hand-designed workflows, AFlow, and ADAS-style automated workflow design.

The detailed protocol is in `benchmarks.md`.

### Track B: Case studies

The case studies are designed to show capabilities that the six standard benchmarks do not test well.

| Case study | Collaboration form | Main capability tested |
|---|---|---|
| Case Study 1 | upstream/downstream collaboration | Bulk RNA-seq DE analysis feeds a gene-set analysis agent; tests typed artifact exchange and biology-aware integration. |
| Case Study 2 | parallel specialist collaboration | scGPT, GEARS, scFoundation, Geneformer-style embeddings, and simple baselines run in parallel; a high-level agent ensembles, benchmarks, and analyzes the results. |
| Case Study 3 | dynamic topology refinement | Start from an AFlow-discovered or AFlow-like topology on code/math benchmarks, attach skills and tools, then trigger local repair at runtime. |

The detailed protocol is in `docs/experiments/case_study.md`.

## 3. Core method variants

Run the following variants whenever the budget permits:

| Variant | Description | Why it matters |
|---|---|---|
| AC-Direct | Direct or single-agent execution only. | Measures the simplest useful baseline. |
| AC-Compiled | Compile a base workflow, but disable runtime topology repair. | Isolates the value of task-conditioned compilation. |
| AC-Gated | Compile a base workflow and enable gated local refinement. | Main AgentCo-Op method. |
| AC-ForcedMulti | Force multi-agent execution even for simple tasks. | Tests whether simplicity-first routing prevents negative returns. |
| AC-NoMetaSkills | Remove meta-skill evidence and use only shallow routing rules. | Tests whether topology-selection skills matter. |
| AC-NoToolSkills | Disable domain tools, sandboxes, or specialized repo backends. | Tests whether execution backend flexibility is useful. |
| AC-NoReviewer | Remove reviewer/verifier nodes and gates. | Tests whether dynamic repair is grounded or merely extra prompting. |

## 4. Reporting requirements

Every experiment should report:

- task score: F1, pass@1, solve rate, Pearson Delta, pathway agreement, or case-specific metric;
- cost: input/output tokens, model calls, tool calls, sandbox calls, GPU hours, and estimated USD cost;
- latency: wall-clock time per task and total experiment time;
- route distribution: how often each topology family is selected;
- gate analysis: trigger count, rescue rate, harm rate, and average cost added;
- reproducibility: dataset hash, external repo commit, model version, prompts, workflow blueprint JSON, and sandbox image digest.

## 5. Minimum first paper package

A strong first version should include:

1. AFlow-aligned results on all six standard benchmarks, at least for AC-Compiled, AC-Gated, AC-ForcedMulti, and the major baselines.
2. Case Study 1 with the Bioconductor `airway` bulk RNA-seq dataset and a GeneAgent-style downstream gene-set analysis node.
3. Case Study 2 on at least Norman 2019 and Replogle K562 Essential Perturb-seq datasets, with simple baselines included.
4. Case Study 3 on HumanEval/MBPP and MATH, importing or reconstructing AFlow-style topologies and adding tool/skill-driven gates.
5. Route distribution and gate rescue/harm figures, because these directly support the central AgentCo-Op claim.
6. A sandbox/repo wrapping report showing that at least two external specialized repos can be pinned, installed, smoke-tested, and invoked through typed manifests.

## 6. Main references

- AFlow: https://arxiv.org/abs/2410.10762
- AFlow code: https://github.com/FoundationAgents/AFlow
- ADAS: https://arxiv.org/abs/2408.08435
- Anthropic, Building Effective Agents: https://www.anthropic.com/engineering/building-effective-agents
- OpenAI Agents SDK orchestration and handoffs: https://developers.openai.com/api/docs/guides/agents/orchestration
- GeneAgent: https://www.nature.com/articles/s41592-025-02748-6
- scGPT: https://github.com/bowang-lab/scGPT
- GEARS: https://github.com/snap-stanford/GEARS
- scFoundation: https://github.com/biomap-research/scFoundation
- scPerturb: https://projects.sanderlab.org/scperturb/
- SWE-bench: https://www.swebench.com/
