FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

RUN apt-get update && apt-get install -y --no-install-recommends \
    git build-essential curl ca-certificates \
    && rm -rf /var/lib/apt/lists/*

RUN useradd -m -u 10001 agent
WORKDIR /workspace

RUN mkdir -p /outputs && chown -R agent:agent /outputs /workspace
USER agent
