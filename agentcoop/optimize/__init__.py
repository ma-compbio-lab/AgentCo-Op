"""Evidence-constrained, non-gradient workflow optimization."""

from agentcoop.optimize.judge import (
    ComponentJudgePayload,
    ComponentPreferenceJudge,
    JudgePanel,
    JudgeRequest,
    JudgeResponse,
    PanelResult,
    PreferenceJudge,
)
from agentcoop.optimize.packets import (
    CandidateView,
    OutputView,
    PacketCheck,
    TraceDigest,
    build_candidate_view,
    validate_evidence_refs,
)
from agentcoop.optimize.preference import (
    CandidateEstimate,
    ComparisonTarget,
    CriterionModel,
    PreferenceModelSet,
    choose_next_comparison,
    fit_preference_models,
    pair_probability,
    preference_dominates,
    preference_front,
    stable_selected_candidate,
)

__all__ = [
    "CandidateView",
    "CandidateEstimate",
    "ComparisonTarget",
    "ComponentJudgePayload",
    "ComponentPreferenceJudge",
    "CriterionModel",
    "JudgePanel",
    "JudgeRequest",
    "JudgeResponse",
    "OutputView",
    "PacketCheck",
    "PanelResult",
    "PreferenceJudge",
    "PreferenceModelSet",
    "TraceDigest",
    "build_candidate_view",
    "choose_next_comparison",
    "fit_preference_models",
    "pair_probability",
    "preference_dominates",
    "preference_front",
    "stable_selected_candidate",
    "validate_evidence_refs",
]
