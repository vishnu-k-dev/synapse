"""Central experiment configuration.

Everything that affects reproducibility is pinned here. Values can be overridden
from the environment (so CI / different machines can point at their own backend
without code changes) but the defaults are the canonical experiment settings.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field

from evalkit import SEED


def _env(key: str, default: str) -> str:
    return os.environ.get(key, default)


@dataclass(frozen=True)
class AgentConfig:
    # Pinned per CLAUDE.md research notes: deterministic GPT-4o.
    model: str = _env("EVAL_AGENT_MODEL", "gpt-4o-2024-11-20")
    temperature: float = 0.0
    seed: int = SEED
    max_steps: int = 12            # hard cap on tool-call turns per task
    max_tokens: int = 1024


@dataclass(frozen=True)
class BackendConfig:
    base_url: str = _env("SYNAPSE_API_URL", "http://localhost:8000")
    api_key: str = _env("SYNAPSE_API_KEY", "dev-api-key-replace-in-prod")
    # How long to wait for the 7-stage pipeline to finish, per app/condition.
    pipeline_timeout_s: int = int(_env("SYNAPSE_PIPELINE_TIMEOUT", "600"))


@dataclass(frozen=True)
class SandboxConfig:
    host: str = "127.0.0.1"
    # 0 => let the OS pick a free port (the harness reads it back).
    port: int = int(_env("EVAL_SANDBOX_PORT", "0"))
    seed: int = SEED


@dataclass(frozen=True)
class ApiUnderTest:
    """One API in the benchmark: a spec to feed SYNAPSE + how to back its calls."""
    name: str                      # e.g. "petstore"
    spec_path: str                 # path to the OpenAPI/Postman spec
    source_format: str = "openapi3"
    realism: str = "mock"          # "mock" | "real" | "hybrid"
    real_base_url: str | None = None   # used when realism touches a live API


@dataclass(frozen=True)
class ExperimentConfig:
    agent: AgentConfig = field(default_factory=AgentConfig)
    backend: BackendConfig = field(default_factory=BackendConfig)
    sandbox: SandboxConfig = field(default_factory=SandboxConfig)
    repeats: int = int(_env("EVAL_REPEATS", "3"))   # runs per (api, condition, task)
    results_dir: str = _env("EVAL_RESULTS_DIR", "results")
