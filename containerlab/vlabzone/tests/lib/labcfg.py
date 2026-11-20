"""The lab configuration as the tests see it – loaded through the SAME code the nodes
use (images/base/rootfs/usr/local/bin/vlab-cfg), so lab and tests cannot disagree."""
from __future__ import annotations

import importlib.machinery
import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
_path = ROOT / "images" / "base" / "rootfs" / "usr" / "local" / "bin" / "vlab-cfg"
_loader = importlib.machinery.SourceFileLoader("vlab_cfg", str(_path))
_spec = importlib.util.spec_from_loader("vlab_cfg", _loader)
vlab_cfg = importlib.util.module_from_spec(_spec)
_loader.exec_module(vlab_cfg)

CFG = vlab_cfg.load(str(ROOT / "config" / "setups.yaml"))


def device_at(setup_id: int, ip: str) -> str:
    """Name of the device that must answer on <ip> in setup <setup_id> (initial grouping)."""
    return vlab_cfg.device_at(CFG, setup_id, ip)


def setup_of(device: str) -> dict | None:
    return vlab_cfg.setup_of(CFG, device)


def device(name: str) -> dict:
    """{'name', 'port', 'ip', 'switch'}"""
    return vlab_cfg.device(CFG, name)


def switch_config(name: str, mode: str) -> list[tuple]:
    return vlab_cfg.switch_config(CFG, name, mode)


def tunnel_ip(setup_id: int, user: int) -> str:
    """10.250.<setup>.<user> (docs/tunnel-addresses.md)"""
    return vlab_cfg.tunnel_ip(CFG, setup_id, user)


def setup_prefix(setup_id: int) -> str:
    return vlab_cfg.setup_prefix(CFG, setup_id)


def alias_ip(setup_id: int, ip: str) -> str:
    """Alias address of <ip> (192.168.1.x) in setup <setup_id>: 10.200.<setup>.x"""
    import ipaddress
    net = ipaddress.ip_network(vlab_cfg.alias_prefix(CFG, setup_id))
    return str(net.network_address + (int(ipaddress.ip_address(ip)) & 0xFF))


def alias_prefix(setup_id: int) -> str:
    return vlab_cfg.alias_prefix(CFG, setup_id)


def setup(setup_id: int) -> dict:
    return next(s for s in CFG["setups"] if s["id"] == setup_id)
