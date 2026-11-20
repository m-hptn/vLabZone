"""E03 – Do the original IPs also work for SSH and logs?

SSH to 192.168.1.20: banner, hostname and config file show the device's own IP
and name (the device of setup 12 with that IP). The device's syslog shows 192.168.1.254 (the edge, because of
MASQUERADE) as the source – documented behaviour.
"""
import pytest

from tests.conftest import ALICE
from tests.lib import labcfg
from tests.lib.results import write_protocol

pytestmark = pytest.mark.stage1

SSH = ("sshpass -p lab ssh -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null "
       "-o ConnectTimeout=5 lab@192.168.1.20")


def test_e03_ssh_and_syslog(lab, tunnel):
    t = tunnel("ws-std", ALICE, 12)
    res = t.run(f"{SSH} 'hostname; cat /etc/device.conf'", check=False, timeout=30)
    assert res.returncode == 0, res.stderr

    dev = labcfg.device_at(12, "192.168.1.20")
    assert f"Device {dev} on switch sys1 - 192.168.1.20" in res.stderr, "banner missing"
    assert res.stdout.splitlines()[0] == dev
    assert "ip=192.168.1.20" in res.stdout and f"name={dev}" in res.stdout

    log = lab[dev].run("grep 'Accepted password for lab' /var/log/auth.log | tail -1").stdout
    assert "from 192.168.1.254" in log, f"unexpected source in syslog: {log!r}"

    write_protocol("E03", "Original IPs for SSH and logs", True,
                   {"syslog_line": log.strip()},
                   observations="devices see the edge address 192.168.1.254 as client (MASQUERADE)",
                   consequence="per-user attribution must come from the broker audit, not device logs")
