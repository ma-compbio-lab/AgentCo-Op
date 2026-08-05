"""Executable capability certification.

The layer that replaces "the designer agent decided this tool looks relevant"
with "these probes ran, here is what they established, and here is what is
still unknown". Nothing downstream is allowed to treat a declared capability
as a demonstrated one; :class:`~agentcoop.ir.capability.CertificationLevel` is
computed from the probe record and can only be raised by executing more.
"""

from agentcoop.probe.certificate import (
    KIND_LEVELS,
    LadderStep,
    certification_report,
    derived_level,
    explain_level,
    first_blocking_step,
    ladder,
)
from agentcoop.probe.runner import UNREACHABLE_FAULTS, ProbeRunner, apply_outcomes
from agentcoop.probe.spec import (
    PROBE_KINDS,
    Corruption,
    ProbeKind,
    ProbeSpec,
    canonical,
    corrupt_payload,
    probe_artifact,
    probe_id_for,
    synthesize_payload,
)
from agentcoop.probe.suite import (
    DEFAULT_RESOURCE_LIMIT_S,
    DETERMINISM_SEED,
    applicable_corruptions,
    parameter_domain_suite,
    resolve_type,
    standard_suite,
)

__all__ = [
    "ProbeKind",
    "PROBE_KINDS",
    "Corruption",
    "ProbeSpec",
    "synthesize_payload",
    "corrupt_payload",
    "probe_artifact",
    "probe_id_for",
    "canonical",
    "standard_suite",
    "parameter_domain_suite",
    "applicable_corruptions",
    "resolve_type",
    "DEFAULT_RESOURCE_LIMIT_S",
    "DETERMINISM_SEED",
    "ProbeRunner",
    "apply_outcomes",
    "UNREACHABLE_FAULTS",
    "LadderStep",
    "ladder",
    "derived_level",
    "first_blocking_step",
    "certification_report",
    "explain_level",
    "KIND_LEVELS",
]
