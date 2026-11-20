"""E11 – Parallel setups with the same network for a power user.

In ws-power, Bob has contexts to setup 12 and setup 7 at the same time. Both setups use
192.168.1.0/24, both have a device at 192.168.1.20. Each context lives in its own
namespace (`lab enter 12`, `lab enter 7`). HTTP, SSH and an iperf3 load run in both at
once; every session must land in the right setup, also under load.
"""
import json
from concurrent.futures import ThreadPoolExecutor

import pytest

from tests.conftest import BOB, ident
from tests.lib import labcfg
from tests.lib.results import write_protocol

pytestmark = pytest.mark.stage2

SETUPS = (12, 7)
SSH = "sshpass -p lab ssh -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o LogLevel=ERROR"


def test_e11_lab_enter_parallel_setups(lab, tunnel):
    ws = lab["ws-power"]
    for s in SETUPS:
        tunnel("ws-power", BOB, s, netns=f"setup{s}")
    expected = {s: labcfg.device_at(s, "192.168.1.20") for s in SETUPS}

    listing = ws.run("lab list").stdout
    prompt = ws.run("lab enter 12 -- sh -c 'echo $(ip netns identify)'").stdout.strip()

    def enter(s, cmd, timeout=30):
        return ws.run(f"lab enter {s} -- {cmd}", check=False, timeout=timeout)

    def load(s):
        r = enter(s, "iperf3 -c 192.168.1.20 -t 6 -J", timeout=40)
        return json.loads(r.stdout)["end"]["sum_received"]["bits_per_second"] if r.returncode == 0 else None

    def probe(s):
        http = ident(enter(s, "curl -s -m 5 http://192.168.1.20"))
        ssh = enter(s, f"{SSH} lab@192.168.1.20 'grep ^name= /etc/device.conf'").stdout.strip()
        return {"http": http and http["device"], "ssh": ssh.removeprefix("name=")}

    with ThreadPoolExecutor(max_workers=4) as pool:
        loads = {s: pool.submit(load, s) for s in SETUPS}
        # sessions while both iperf3 streams are running
        probes = {s: pool.submit(lambda s=s: [probe(s) for _ in range(3)]) for s in SETUPS}
        bps = {s: f.result() for s, f in loads.items()}
        answers = {s: f.result() for s, f in probes.items()}

    wrong = {s: a for s, rounds in answers.items() for a in rounds
             if a["http"] != expected[s] or a["ssh"] != expected[s]}
    ok = not wrong and all(bps.values())
    write_protocol("E11", "Parallel setups with the same network (power user)", ok,
                   {"expected": expected, "answers": answers,
                    "iperf3_mbit_s": {s: round(b / 1e6) if b else None for s, b in bps.items()}},
                   setup_notes="ws-power, namespaces setup12 + setup7, iperf3 in both while HTTP/SSH probes run",
                   observations=f"lab list:\n{listing}")
    assert "setup12" in prompt, prompt
    assert all(f"{s} " in listing for s in SETUPS), listing
    assert not wrong, f"session landed in the wrong setup: {wrong}"
    assert all(bps.values()), f"iperf3 failed: {bps}"


def test_e11_lab_enter_unknown_setup(lab):
    r = lab["ws-power"].run("lab enter 5 -- true", check=False)
    assert r.returncode != 0 and "no context for setup 5" in r.stderr
