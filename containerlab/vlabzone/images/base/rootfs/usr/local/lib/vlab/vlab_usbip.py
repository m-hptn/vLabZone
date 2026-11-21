"""USB/IP protocol pieces shared by the USB hub stand-in (images/usbhub) and the workspace client.

The Digi AnywhereUSB 24 Plus speaks a proprietary protocol. The lab stands in for it with the
Linux USB/IP protocol (version 1.1.1), which has the same shape: a client lists the hub's
ports, imports one, and the URBs of that device then run over the same TCP connection.
Reference: Documentation/usb/usbip_protocol.rst in the Linux kernel.
"""
from __future__ import annotations

import socket
import struct

VERSION = 0x0111
OP_REQ_DEVLIST, OP_REP_DEVLIST = 0x8005, 0x0005
OP_REQ_IMPORT, OP_REP_IMPORT = 0x8003, 0x0003
CMD_SUBMIT, CMD_UNLINK, RET_SUBMIT, RET_UNLINK = 1, 2, 3, 4
DIR_OUT, DIR_IN = 0, 1
SPEED_HIGH = 3
ST_OK, ST_NA = 0, 1

OP_HDR = struct.Struct("!HHI")                      # version, code, status
# struct usbip_usb_device (312 bytes)
DEV = struct.Struct("!256s32sIIIHHHBBBBBB")
IFACE = struct.Struct("!BBBx")
# usbip_header_basic + cmd_submit / ret_submit (48 bytes)
BASIC = struct.Struct("!IIIII")
SUBMIT = struct.Struct("!iiiii8s")                  # flags, length, start_frame, packets, interval, setup
RET = struct.Struct("!iiiii8x")                     # status, actual_length, start_frame, packets, errors
UNLINK = struct.Struct("!I24x")
RET_UNLINK_ST = struct.Struct("!i24x")


def recv_exact(sock: socket.socket, n: int) -> bytes:
    buf = b""
    while len(buf) < n:
        chunk = sock.recv(n - len(buf))
        if not chunk:
            raise ConnectionError("connection closed")
        buf += chunk
    return buf


def pack_dev(path: str, busid: str, busnum: int, devnum: int, vendor: int, product: int,
             bcd: int, dev_class: int, num_ifaces: int) -> bytes:
    return DEV.pack(path.encode(), busid.encode(), busnum, devnum, SPEED_HIGH, vendor, product, bcd,
                    dev_class, 0, 0, 1, 1, num_ifaces)


def unpack_dev(data: bytes) -> dict:
    (path, busid, busnum, devnum, speed, vendor, product, bcd, cls, _sub, _proto, _cfg, _ncfg,
     nifs) = DEV.unpack(data)
    return {"path": path.rstrip(b"\0").decode(), "busid": busid.rstrip(b"\0").decode(),
            "busnum": busnum, "devnum": devnum, "speed": speed, "vendor": vendor, "product": product,
            "num_interfaces": nifs}


# --- client side (workspace) ---------------------------------------------------------------
def devlist(host: str, port: int, timeout: float = 5) -> list[dict]:
    """The hub ports this client may see."""
    with socket.create_connection((host, port), timeout=timeout) as s:
        s.sendall(OP_HDR.pack(VERSION, OP_REQ_DEVLIST, 0))
        _v, code, status = OP_HDR.unpack(recv_exact(s, OP_HDR.size))
        if code != OP_REP_DEVLIST or status != ST_OK:
            raise RuntimeError(f"devlist refused (status {status})")
        (n,) = struct.unpack("!I", recv_exact(s, 4))
        out = []
        for _ in range(n):
            d = unpack_dev(recv_exact(s, DEV.size))
            recv_exact(s, IFACE.size * d["num_interfaces"])
            out.append(d)
        return out


def control_in(s: socket.socket, seq: int, setup: bytes, length: int) -> tuple[int, bytes]:
    s.sendall(BASIC.pack(CMD_SUBMIT, seq, 0, DIR_IN, 0) + SUBMIT.pack(0, length, 0, 0, 0, setup))
    cmd, rseq, _devid, _dir, _ep = BASIC.unpack(recv_exact(s, BASIC.size))
    status, actual, _sf, _np, _err = RET.unpack(recv_exact(s, RET.size))
    if cmd != RET_SUBMIT or rseq != seq:
        raise RuntimeError(f"unexpected reply {cmd}/{rseq}")
    return status, recv_exact(s, actual) if actual > 0 else b""


def get_descriptor(dtype: int, index: int, length: int, lang: int = 0) -> bytes:
    return struct.pack("<BBHHH", 0x80, 6, (dtype << 8) | index, lang, length)


def import_device(host: str, port: int, busid: str, timeout: float = 5) -> dict:
    """Import <busid> like `usbip attach` does, then read the device's product string over the
    imported connection (what the kernel does when it enumerates the device). Raises on refusal."""
    with socket.create_connection((host, port), timeout=timeout) as s:
        s.sendall(OP_HDR.pack(VERSION, OP_REQ_IMPORT, 0) + busid.encode().ljust(32, b"\0"))
        _v, code, status = OP_HDR.unpack(recv_exact(s, OP_HDR.size))
        if code != OP_REP_IMPORT or status != ST_OK:
            raise PermissionError(f"import of {busid} refused (status {status})")
        dev = unpack_dev(recv_exact(s, DEV.size))
        st, desc = control_in(s, 1, get_descriptor(1, 0, 18), 18)
        if st != 0 or len(desc) != 18:
            raise RuntimeError("device descriptor failed")
        i_product = desc[15]
        st, sdesc = control_in(s, 2, get_descriptor(3, i_product, 255, 0x0409), 255)
        dev["product_string"] = sdesc[2:sdesc[0]].decode("utf-16-le") if st == 0 and sdesc else None
        return dev
