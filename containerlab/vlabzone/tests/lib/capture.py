"""Packet capture inside a lab node during a test step."""
from __future__ import annotations

import contextlib
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path

from tests.lib.node import Node
from tests.lib.results import RESULTS


@dataclass
class Capture:
    lines: list[str] = field(default_factory=list)
    pcap: Path | None = None


@contextlib.contextmanager
def capture(node: Node, iface: str, bpf: str = "", name: str | None = None):
    """with capture(node, 'eth10', 'udp') as cap: ...  -> cap.lines (tcpdump -nn text)."""
    tag = name or f"{node.name}-{iface}"
    remote = f"/tmp/{tag}.pcap"
    node.run(f"rm -f {remote}")
    subprocess.run(["docker", "exec", "-d", node.container, "sh", "-c",
                    f"timeout 120 tcpdump -i {iface} -nn -U -w {remote} {bpf} 2>/dev/null"], check=True)
    # wait until tcpdump runs and has opened the interface
    for _ in range(50):
        if node.run("pgrep -x tcpdump", check=False).returncode == 0:
            break
        time.sleep(0.1)
    time.sleep(0.7)
    cap = Capture()
    try:
        yield cap
    finally:
        time.sleep(0.5)
        node.run("pkill -INT -x tcpdump", check=False)
        time.sleep(0.5)
        cap.lines = [l for l in node.run(f"tcpdump -nn -r {remote} 2>/dev/null", check=False).stdout.splitlines() if l]
        RESULTS.mkdir(exist_ok=True)
        stamp = time.strftime("%Y%m%d-%H%M%S")
        cap.pcap = RESULTS / f"{tag}-{stamp}.pcap"
        subprocess.run(["docker", "cp", f"{node.container}:{remote}", str(cap.pcap)], capture_output=True)
