"""Helpers to run commands inside containerlab nodes via `docker exec`."""
from __future__ import annotations

import subprocess
from dataclasses import dataclass


@dataclass
class Node:
    lab: str
    name: str

    @property
    def container(self) -> str:
        return f"clab-{self.lab}-{self.name}"

    def run(self, cmd: str, check: bool = True, timeout: int = 30, netns: str | None = None) -> subprocess.CompletedProcess:
        if netns:
            cmd = f"ip netns exec {netns} sh -c {cmd!r}"
        return subprocess.run(
            ["docker", "exec", self.container, "sh", "-c", cmd],
            capture_output=True, text=True, check=check, timeout=timeout,
        )

    def is_running(self) -> bool:
        res = subprocess.run(
            ["docker", "inspect", "-f", "{{.State.Running}}", self.container],
            capture_output=True, text=True,
        )
        return res.stdout.strip() == "true"
