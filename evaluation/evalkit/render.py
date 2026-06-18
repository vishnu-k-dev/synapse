"""Render the real SYNAPSE server template locally.

Used for offline/smoke runs where no backend is available. Uses the exact Jinja
configuration the synthesizer uses, so the output is byte-identical to a SYNAPSE
artifact for the same tool context.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from jinja2 import Environment, FileSystemLoader, StrictUndefined, select_autoescape


def _default_templates_dir() -> Path:
    # evalkit/ -> evaluation/ -> synapse-clone/ ; templates live under backend/app/templates
    return Path(__file__).resolve().parents[2] / "backend" / "app" / "templates"


def render_python_server(app_name: str, base_url: str, tools: list[dict[str, Any]],
                         templates_dir: str | None = None) -> str:
    env = Environment(
        loader=FileSystemLoader(str(templates_dir or _default_templates_dir())),
        autoescape=select_autoescape(["py", "ts"]),
        undefined=StrictUndefined,
        trim_blocks=True,
        lstrip_blocks=True,
    )
    return env.get_template("python_mcp/server.py.j2").render(
        app_name=app_name, base_url=base_url, tools=tools, workflows=[]
    )
