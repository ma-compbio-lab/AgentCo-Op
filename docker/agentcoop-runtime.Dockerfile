# Generic AgentCo-Op runtime image — hosts every Python adapter shipped
# with the package so `agentcoop collaborate --docker` can `docker run`
# this single image regardless of which external repo the request targets.
#
# Adapters that need a non-Python toolchain (e.g. R-based Seurat/Signac
# in case_study_2.md §10–§11) build their own image via
# `agentcoop/wrappers/<name>/Dockerfile.agentcoop` and override
# `SandboxSpec.runtime_image`. This image is the default fallback.
FROM python:3.11-slim

ENV DEBIAN_FRONTEND=noninteractive \
    PIP_NO_CACHE_DIR=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

RUN apt-get update && apt-get install -y --no-install-recommends \
        git build-essential curl ca-certificates \
        libhdf5-dev libz-dev libbz2-dev liblzma-dev \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /opt/agentcoop
COPY pyproject.toml README.md ./
COPY agentcoop/ ./agentcoop/

# Editable install + the scientific stack every CS1/CS2 adapter declares
# via `register_required_packages`. Pinned ranges only — let pip pick
# wheels for the host architecture (works on aarch64 + x86_64 alike).
RUN pip install --upgrade pip \
    && pip install -e . \
    && pip install \
        "numpy>=1.26" "pandas>=2.0" "scipy>=1.10" "matplotlib>=3.7" \
        "anndata>=0.10" "scanpy>=1.10" "statsmodels>=0.14" \
        "openpyxl>=3.1" "pyyaml>=6.0" "seaborn>=0.13" "requests>=2.31"

# Default workdir is bind-mounted to the host project root so request
# JSONs and artifact paths work without translation.
WORKDIR /workspace

ENTRYPOINT ["python", "-m", "agentcoop.wrappers"]
