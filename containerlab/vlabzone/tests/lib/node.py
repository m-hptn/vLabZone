"""Helpers to run commands inside containerlab nodes via `docker exec`."""
from __future__ import annotations

import shlex
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
            cmd = f"ip netns exec {netns} sh -c {shlex.quote(cmd)}"
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


@dataclass
class RouterOSEdge(Node):
    """lab-edge as MikroTik (EDGE_IMPL=routeros). Only `edge-ctl …` is supported: it goes to
    drivers/edge_routeros.py over the REST API with the same arguments and output. A test that
    runs any other command on the edge looks at Linux internals (nft, ip rule, wg) and is skipped."""
    mode: str = "vrf"

    def run(self, cmd: str, check: bool = True, timeout: int = 60, netns: str | None = None) -> subprocess.CompletedProcess:
        import sys
        from pathlib import Path

        import pytest
        if not cmd.startswith("edge-ctl "):
            pytest.skip(f"Linux edge internals, not on RouterOS: {cmd.split()[0]} …")
        driver = Path(__file__).resolve().parents[2] / "drivers" / "edge_routeros.py"
        args = [sys.executable, str(driver), "--lab", self.lab, "--mode", self.mode, *shlex.split(cmd)[1:]]
        res = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
        if check and res.returncode:
            raise subprocess.CalledProcessError(res.returncode, args, res.stdout, res.stderr)
        return res

    def ready(self) -> bool:
        import sys
        from pathlib import Path
        driver = Path(__file__).resolve().parents[2] / "drivers" / "edge_routeros.py"
        return subprocess.run([sys.executable, str(driver), "--lab", self.lab, "--mode", self.mode, "wait"],
                              capture_output=True, timeout=320).returncode == 0
