"""Host a SYNAPSE-generated MCP server as a launchable stdio subprocess.

In the live harness the ``server.py`` source comes straight from the SYNAPSE
artifact (fetched over HTTP); for the keyless de-risk it comes from rendering the
real Jinja template. Either way this class just writes the source to a temp dir and
produces the launch spec. The generated server has BASE_URL baked in at render time
and reads API_KEY from the environment, so all we inject here is API_KEY.
"""
from __future__ import annotations

import os
import shutil
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class LaunchSpec:
    command: str
    args: list[str]
    env: dict[str, str] = field(default_factory=dict)


class GeneratedServer:
    def __init__(self, source: str, api_key: str = "eval-key", filename: str = "server.py") -> None:
        self._source = source
        self._api_key = api_key
        self._filename = filename
        self._dir: str | None = None
        self._path: str | None = None

    def __enter__(self) -> "GeneratedServer":
        self._dir = tempfile.mkdtemp(prefix="synapse_mcp_")
        path = Path(self._dir) / self._filename
        path.write_text(self._source, encoding="utf-8")
        self._path = str(path)
        return self

    def __exit__(self, *exc: object) -> None:
        if self._dir and os.path.isdir(self._dir):
            shutil.rmtree(self._dir, ignore_errors=True)
        self._dir = None

    @property
    def path(self) -> str:
        if not self._path:
            raise RuntimeError("GeneratedServer must be entered before use")
        return self._path

    def launch_spec(self) -> LaunchSpec:
        # Launch with the eval venv interpreter — it has fastmcp + httpx installed,
        # so the generated server's imports resolve. server.py is an absolute path,
        # so the subprocess cwd is irrelevant.
        env = {**os.environ, "API_KEY": self._api_key, "PYTHONUNBUFFERED": "1"}
        return LaunchSpec(command=sys.executable, args=[self.path], env=env)
