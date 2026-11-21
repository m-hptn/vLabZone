#!/usr/bin/env python3
"""usbhub-sim – stand-in for the Digi AnywhereUSB 24 Plus (docs/consoles-usb.md).

One TCP port (18574, like the AnywhereUSB) for ALL hub ports. Which ports a client may use is
decided by the hub itself, not by the network:

  group N    = the hub ports of setup N's devices (initial grouping from setups.yaml)
  members    = the clients allowed into group N. On the real hub a client is identified by its
               client ID; here by its tunnel address 10.250.N.U [sim]. The broker adds the
               booking's client when it grants access and removes it on revoke (hub-ctl).

Protocol: Linux USB/IP 1.1.1 (vlab_usbip.py). Every hub port emulates a simple device (vendor
class, no endpoints) that answers enumeration – device, configuration and string descriptors,
product "<device> USB" – so the kernel's `usbip attach` works as well as the userspace client.

State: /run/vlab/hub.json, re-read on every request and every second. A client that loses its
membership is disconnected at once (revoke ends running sessions, like edge-ctl peer del).
"""
from __future__ import annotations

import json
import os
import socket
import struct
import sys
import threading
import time

sys.path.insert(0, "/usr/local/lib/vlab")
import vlab_usbip as u  # noqa: E402

STATE = os.environ.get("HUB_STATE", "/run/vlab/hub.json")
PORT = int(os.environ.get("HUB_TCP_PORT", "18574"))
HUB = os.environ.get("HUB_NAME", "ausb1")
VENDOR, PRODUCT = 0x1D6B, 0x0104                      # Linux Foundation "multifunction composite"

lock = threading.Lock()
sessions: dict[str, tuple[str, socket.socket]] = {}     # busid -> (client ip, socket)


def log(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {HUB}: {msg}", flush=True)


def state() -> dict:
    with open(STATE) as fh:
        return json.load(fh)


def busid(port: int) -> str:
    return f"1-{port}"


def allowed_ports(st: dict, client: str) -> list[int]:
    """Hub ports of every group <client> is a member of."""
    out = []
    for gid, members in st["members"].items():
        if client in members:
            out += st["groups"].get(gid, [])
    return sorted(set(out))


def device_of(st: dict, port: int) -> str:
    return st["devices"].get(str(port)) or f"{HUB} port {port}"


def dev_record(port: int) -> bytes:
    return u.pack_dev(f"/sys/devices/platform/{HUB}/usb1/1-{port}", busid(port), 1, port + 1,
                      VENDOR, PRODUCT, 0x0100, 0, 1)


# --- emulated device: control transfers on endpoint 0 --------------------------------------
def string_desc(text: str) -> bytes:
    data = text.encode("utf-16-le")
    return bytes([2 + len(data), 3]) + data


def descriptors(name: str, port: int) -> dict:
    device = struct.pack("<BBHBBBBHHHBBBB", 18, 1, 0x0200, 0, 0, 0, 64, VENDOR, PRODUCT, 0x0100, 1, 2, 3, 1)
    iface = struct.pack("<BBBBBBBBB", 9, 4, 0, 0, 0, 0xFF, 0, 0, 0)          # vendor class, no endpoints
    config = struct.pack("<BBHBBBBB", 9, 2, 9 + len(iface), 1, 1, 0, 0x80, 50) + iface
    return {1: device, 2: config,
            (3, 0): bytes([4, 3, 0x09, 0x04]),
            (3, 1): string_desc("vLabZone"),
            (3, 2): string_desc(f"{name} USB"),
            (3, 3): string_desc(f"{HUB}-{port}")}


def control(desc: dict, setup: bytes) -> tuple[int, bytes]:
    req_type, req, value, _index, length = struct.unpack("<BBHHH", setup)
    if req == 6 and req_type == 0x80:                                         # GET_DESCRIPTOR
        dtype, idx = value >> 8, value & 0xFF
        data = desc.get(dtype) if dtype in (1, 2) else desc.get((dtype, idx))
        return (0, data[:length]) if data else (-32, b"")
    if req == 0 and req_type & 0x80:                                          # GET_STATUS
        return 0, b"\0\0"[:length]
    if req == 8 and req_type == 0x80:                                         # GET_CONFIGURATION
        return 0, b"\x01"
    if req in (9, 11) and not req_type & 0x80:                                # SET_CONFIGURATION/INTERFACE
        return 0, b""
    return -32, b""                                                           # stall (-EPIPE)


def serve_urbs(conn: socket.socket, name: str, port: int) -> None:
    desc = descriptors(name, port)
    while True:
        cmd, seq, devid, direction, ep = u.BASIC.unpack(u.recv_exact(conn, u.BASIC.size))
        if cmd == u.CMD_SUBMIT:
            _flags, length, _sf, packets, _iv, setup = u.SUBMIT.unpack(u.recv_exact(conn, u.SUBMIT.size))
            if direction == u.DIR_OUT and length > 0:
                u.recv_exact(conn, length)
            if packets not in (0, -1, 0xFFFFFFFF):
                u.recv_exact(conn, 16 * packets)                              # iso descriptors (unused)
            status, data = control(desc, setup) if ep == 0 else (-32, b"")
            if direction == u.DIR_OUT:
                data = b""
            conn.sendall(u.BASIC.pack(u.RET_SUBMIT, seq, devid, direction, ep)
                         + u.RET.pack(status, len(data) if direction == u.DIR_IN else (length if status == 0 else 0),
                                      0, 0, 0) + data)
        elif cmd == u.CMD_UNLINK:
            u.recv_exact(conn, u.UNLINK.size)
            conn.sendall(u.BASIC.pack(u.RET_UNLINK, seq, devid, direction, ep) + u.RET_UNLINK_ST.pack(0))
        else:
            raise ConnectionError(f"unknown command {cmd}")


def handle(conn: socket.socket, client: str) -> None:
    claimed = None
    try:
        _v, code, _st = u.OP_HDR.unpack(u.recv_exact(conn, u.OP_HDR.size))
        st = state()
        ports = allowed_ports(st, client)
        if code == u.OP_REQ_DEVLIST:
            body = b"".join(dev_record(p) + u.IFACE.pack(0xFF, 0, 0) for p in ports)
            conn.sendall(u.OP_HDR.pack(u.VERSION, u.OP_REP_DEVLIST, u.ST_OK) + struct.pack("!I", len(ports)) + body)
            log(f"devlist {client}: ports {ports or 'none'}")
        elif code == u.OP_REQ_IMPORT:
            bid = u.recv_exact(conn, 32).rstrip(b"\0").decode()
            port = next((p for p in ports if busid(p) == bid), None)
            with lock:
                busy = bid in sessions
                if port is not None and not busy:
                    sessions[bid] = (client, conn)
                    claimed = bid
            if port is None or busy:
                conn.sendall(u.OP_HDR.pack(u.VERSION, u.OP_REP_IMPORT, u.ST_NA))
                log(f"import {bid} by {client}: refused ({'in use' if busy else 'not in its groups'})")
                return
            conn.sendall(u.OP_HDR.pack(u.VERSION, u.OP_REP_IMPORT, u.ST_OK) + dev_record(port))
            name = device_of(st, port)
            log(f"import {bid} ({name}) by {client}")
            serve_urbs(conn, name, port)
    except (ConnectionError, OSError, struct.error):
        pass
    finally:
        if claimed:
            with lock:
                sessions.pop(claimed, None)
            log(f"released {claimed} ({client})")
        conn.close()


def watch_revocations() -> None:
    """Disconnect sessions whose client lost the group membership (or whose port left the group)."""
    while True:
        time.sleep(0.5)
        try:
            st = state()
        except (OSError, ValueError):
            continue
        with lock:
            for bid, (client, conn) in list(sessions.items()):
                if bid not in {busid(p) for p in allowed_ports(st, client)}:
                    log(f"revoked {bid} for {client}: disconnect")
                    try:
                        conn.shutdown(socket.SHUT_RDWR)
                    except OSError:
                        pass


def main() -> None:
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(("0.0.0.0", PORT))
    srv.listen(16)
    threading.Thread(target=watch_revocations, daemon=True).start()
    log(f"listening on TCP {PORT}")
    while True:
        conn, (addr, _p) = srv.accept()
        conn.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        threading.Thread(target=handle, args=(conn, addr), daemon=True).start()


if __name__ == "__main__":
    main()
