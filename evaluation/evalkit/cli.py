"""Command-line entrypoint for the evaluation harness.

  offline : render a local tool surface (no SYNAPSE backend) and run the agent.
            Approximates the naive baseline; needs only OPENAI_API_KEY.
  live    : drive the SYNAPSE backend to generate each condition's tool set, then run
            the full 2x2 ablation. Needs the backend up + OPENAI_API_KEY.
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

EVAL_DIR = Path(__file__).resolve().parents[1]


def _load_dotenv() -> None:
    """Load env vars from evaluation/.env, then the repo-root .env as a fallback.

    Lets you keep your OPENAI_API_KEY in the single root .env that the backend also
    uses. Existing real-environment vars and earlier files take precedence (setdefault).
    """
    for env_path in (EVAL_DIR / ".env", EVAL_DIR.parent / ".env"):
        if not env_path.exists():
            continue
        for line in env_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            os.environ.setdefault(key.strip(), value.strip())

from evalkit.conditions import ALL_CONDITIONS, CONDITIONS_BY_KEY
from evalkit.config import ExperimentConfig
from evalkit.experiment import FixtureProvider, LiveProvider, run_experiment
from evalkit.report import markdown_table, significance_block
from evalkit.stats import summarize
from evalkit.tasks.schema import load_suite

REPO_ROOT = Path(__file__).resolve().parents[2]
SUITES_DIR = Path(__file__).resolve().parent / "tasks" / "suites"
SPECS = {"petstore": REPO_ROOT / "tests" / "fixtures" / "petstore.yaml"}


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="SYNAPSE evaluation harness")
    p.add_argument("--api", default="petstore", choices=sorted(SPECS))
    p.add_argument("--mode", default="offline", choices=["offline", "live"])
    p.add_argument("--conditions", default="all",
                   help="'all' or comma list of condition keys, e.g. C1_naive,C4_full")
    p.add_argument("--repeats", type=int, default=None)
    p.add_argument("--max-steps", type=int, default=None)
    p.add_argument("--out", default=None)
    args = p.parse_args(argv)

    _load_dotenv()
    cfg = ExperimentConfig()
    spec_path = str(SPECS[args.api])
    tasks = load_suite(str(SUITES_DIR / f"{args.api}.yaml"), api=args.api)
    conditions = (ALL_CONDITIONS if args.conditions == "all"
                  else [CONDITIONS_BY_KEY[k] for k in args.conditions.split(",")])
    out_dir = args.out or f"{cfg.results_dir}/{args.api}/{args.mode}"
    repeats = args.repeats or cfg.repeats
    max_steps = args.max_steps or cfg.agent.max_steps

    # The agent is GPT-4o via OpenAI (reads OPENAI_API_KEY from the environment).
    from evalkit.agent.llm import OpenAIChat
    model = OpenAIChat(model=cfg.agent.model, temperature=cfg.agent.temperature,
                       seed=cfg.agent.seed, max_tokens=cfg.agent.max_tokens)

    if args.mode == "offline":
        provider: object = FixtureProvider()
    else:
        from evalkit.synapse_client import SynapseClient
        client = SynapseClient(cfg.backend.base_url, cfg.backend.api_key)
        if not client.healthy():
            print(f"ERROR: SYNAPSE backend not reachable at {cfg.backend.base_url}")
            return 2
        provider = LiveProvider(client, args.api, spec_path, timeout_s=cfg.backend.pipeline_timeout_s)

    print(f"Running {args.mode} experiment on '{args.api}': "
          f"{len(conditions)} conditions x {len(tasks)} tasks x {repeats} repeats")

    out = run_experiment(
        spec_path=spec_path, tasks=tasks, provider=provider, model=model,
        out_dir=out_dir, conditions=conditions, repeats=repeats,
        max_steps=max_steps, seed=cfg.sandbox.seed,
        title=f"SYNAPSE ablation - {args.api}",
    )

    print("\n" + markdown_table(out["summary"]))
    print(significance_block(out["results"]))
    print(f"\nArtifacts written to: {out['artifacts']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
