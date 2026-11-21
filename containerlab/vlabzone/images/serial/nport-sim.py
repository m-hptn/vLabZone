#!/usr/bin/env python3
"""nport-sim – stand-in for the Moxa NPort 5600 serial device server (docs/consoles-usb.md).

  nport-sim.py     ports and cabling from `vlab-cfg serial-server $NPORT_NAME`

Serial port K listens on TCP base_port+K in RFC 2217 mode (the NPort's "RFC2217" operation
mode): the client sets baud rate, data bits, parity, stop bits and the modem lines over the
TCP connection. Behind each port sits the console of the device cabled to it:

  <enter>          "<device> login:" prompt
  show version     device, firmware, NPort port
  show baud        line settings as the CLIENT set them over RFC 2217
  show lines       DTR/RTS as the client set them
  reboot           boot messages, then the prompt
  anything else    "% unknown command"

One TCP session per port at a time (NPort "max connection 1"): a second client is
disconnected at once.

RFC 2217 is pyserial's server implementation (serial.rfc2217.PortManager) on top of an
in-memory serial port. ser2net on a pty was the first choice, but a pty has no modem lines:
setting DTR, which every client does on open, is never acknowledged.
"""
from __future__ import annotations

import os
import socket
import subprocess
import threading
import time

import serial
import serial.rfc2217

NAME = os.environ.get("NPORT_NAME", "nport1")


def log(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {NAME}: {msg}", flush=True)


class ConsolePort(serial.SerialBase):
    """An in-memory serial port: what the client writes goes to the device console, the
    console's answers go back through the RFC 2217 manager (escaped)."""

    def __init__(self, port_no: int, device: str | None, send) -> None:
        super().__init__()
        self.port_no, self.device, self._send = port_no, device, send
        self._buf = b""
        self.is_open = True

    # --- what serial.SerialBase needs ---------------------------------------------------
    def _reconfigure_port(self, force_update=False):
        pass

    def _update_rts_state(self):
        pass

    def _update_dtr_state(self):
        pass

    def _update_break_state(self):
        pass

    @property
    def cts(self):
        return True

    @property
    def dsr(self):
        return True

    @property
    def ri(self):
        return False

    @property
    def cd(self):
        return True

    def reset_input_buffer(self):
        pass

    def reset_output_buffer(self):
        self._buf = b""

    # --- the device console ---------------------------------------------------------------
    def settings(self) -> str:
        parity = {"N": "n", "E": "e", "O": "o", "M": "m", "S": "s"}[self.parity]
        stop = {1: "1", 1.5: "1.5", 2: "2"}[self.stopbits]
        return f"baud {self.baudrate} {self.bytesize}{parity}{stop}"

    def prompt(self) -> str:
        return f"\n{self.device} login: " if self.device else f"\n(no device on {NAME} port {self.port_no})\n"

    def answer(self, cmd: str) -> str:
        p = f"\n{self.device or ''}> "
        if not cmd:
            return self.prompt()
        if cmd == "show version":
            return f"\n{self.device or '-'} firmware 1.0 - console on {NAME} port {self.port_no}{p}"
        if cmd == "show baud":
            return f"\n{self.settings()}{p}"
        if cmd == "show lines":
            return f"\nDTR {'on' if self.dtr else 'off'} RTS {'on' if self.rts else 'off'}{p}"
        if cmd == "reboot":
            return f"\n{self.device} rebooting ...\nU-Boot 2026.01 ({self.device})\nBooting ...{self.prompt()}"
        return f"\n% unknown command: {cmd}{p}"

    def write(self, data: bytes) -> int:
        out = ""
        for ch in data:
            if ch in (13, 10):
                if ch == 10 and not self._buf:
                    continue
                cmd, self._buf = self._buf.decode(errors="replace").strip(), b""
                out += self.answer(cmd)
            elif ch in (8, 127):
                self._buf = self._buf[:-1]
            else:
                self._buf += bytes([ch])
        if out:
            self._send(out.replace("\n", "\r\n").encode())
        return len(data)


class Session:
    """One RFC 2217 session on one port (like the example server of pyserial)."""

    def __init__(self, conn: socket.socket, port_no: int, device: str | None) -> None:
        self.conn = conn
        self.lock = threading.Lock()
        self.port = ConsolePort(port_no, device, self.send_data)
        self.mgr = serial.rfc2217.PortManager(self.port, self)

    def write(self, data: bytes) -> None:          # used by PortManager for telnet negotiation
        with self.lock:
            self.conn.sendall(data)

    def send_data(self, data: bytes) -> None:
        self.write(b"".join(self.mgr.escape(data)))

    def run(self) -> None:
        while True:
            data = self.conn.recv(1024)
            if not data:
                return
            self.port.write(b"".join(self.mgr.filter(data)))


def serve(port_no: int, tcp: int, device: str | None) -> None:
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(("0.0.0.0", tcp))
    srv.listen(4)
    busy = threading.Lock()
    while True:
        conn, (addr, _p) = srv.accept()
        conn.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        if not busy.acquire(blocking=False):
            log(f"port {port_no}: {addr} refused, in use")
            conn.close()
            continue

        def run(conn=conn, addr=addr):
            log(f"port {port_no} ({device}): session from {addr}")
            try:
                Session(conn, port_no, device).run()
            except OSError:
                pass
            finally:
                conn.close()
                busy.release()
                log(f"port {port_no}: session from {addr} closed")
        threading.Thread(target=run, daemon=True).start()


def main() -> None:
    rows = subprocess.run(["vlab-cfg", "serial-server", NAME], check=True, capture_output=True, text=True).stdout
    for line in rows.splitlines():
        k, tcp, dev = line.split()
        threading.Thread(target=serve, args=(int(k), int(tcp), None if dev == "-" else dev), daemon=True).start()
        log(f"port {k} -> TCP {tcp} RFC 2217 ({dev})")
    while True:
        time.sleep(3600)


if __name__ == "__main__":
    main()
