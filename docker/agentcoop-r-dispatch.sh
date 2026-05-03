#!/usr/bin/env bash
# Dispatcher for the agentcoop-r-runtime image. The orchestrator passes
# `<agent_name> <invoke.json>` exactly like for the Python runtime
# image; we route to the right Rscript per agent name.
set -euo pipefail

agent="${1:-}"
invoke="${2:-}"

if [[ -z "${agent}" || -z "${invoke}" ]]; then
  echo "usage: agentcoop-r-dispatch <agent_name> <invoke.json>" >&2
  exit 2
fi

case "${agent}" in
  Seurat|seurat)
    exec Rscript /workspace/agentcoop/wrappers/seurat_local/seurat_rna_marker_agent.R "${invoke}"
    ;;
  Signac|signac)
    exec Rscript /workspace/agentcoop/wrappers/signac_local/signac_atac_marker_agent.R "${invoke}"
    ;;
  *)
    echo "agentcoop-r-dispatch: unknown agent '${agent}' — expected Seurat or Signac" >&2
    exit 3
    ;;
esac
