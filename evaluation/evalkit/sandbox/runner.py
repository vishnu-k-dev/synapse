"""Run the mock sandbox as a real uvicorn server in a background thread.

The generated MCP server makes *real* httpx calls to its baked-in BASE_URL, so the
sandbox must listen on an actual TCP port (an in-process TestClient won't do). This
starts uvicorn on a free port and hands back the URL + the live store (so success
oracles can read final state directly).
"""
from __future__ import annotations

import socket
import threading
import time

import uvicorn

from evalkit.sandbox.simulator import build_simulator
from evalkit.sandbox.state import EntityStore


def _free_port(host: str = "127.0.0.1") -> int:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        s.bind((host, 0))
        return s.getsockname()[1]
    finally:
        s.close()


class SandboxServer:
    def __init__(self, spec_path: str, seed: int = 42, host: str = "127.0.0.1",
                 port: int | None = None) -> None:
        self.app, self.store = build_simulator(spec_path, seed=seed)
        self.host = host
        self.port = port or _free_port(host)
        self._server: uvicorn.Server | None = None
        self._thread: threading.Thread | None = None

    @property
    def base_url(self) -> str:
        return f"http://{self.host}:{self.port}"

    def start(self, timeout_s: float = 10.0) -> "SandboxServer":
        config = uvicorn.Config(self.app, host=self.host, port=self.port, log_level="warning")
        self._server = uvicorn.Server(config)
        self._thread = threading.Thread(target=self._server.run, daemon=True)
        self._thread.start()
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            if self._server.started:
                return self
            time.sleep(0.05)
        raise TimeoutError("sandbox server did not start in time")

    def stop(self) -> None:
        if self._server is not None:
            self._server.should_exit = True
        if self._thread is not None:
            self._thread.join(timeout=5)

    def reset(self) -> None:
        self.store.reset()

    def __enter__(self) -> "SandboxServer":
        return self.start()

    def __exit__(self, *exc: object) -> None:
        self.stop()
