# 0006 – Moxa NPort operation mode: RFC 2217 or Real COM

- **Status:** Open – the lab assumes RFC 2217
- **Date:** 2026-11-21
- **Scope:** real environment

## Context
The NPort can expose its serial ports as RFC 2217 (any client, e.g. pyserial, `socat`) or in Real COM mode (Moxa
driver on the workspace creates a local COM/tty). The edge enforces serial access per TCP port, so the mode decides
which ports it opens per console.

## Options
| Option | For | Against |
|---|---|---|
| **RFC 2217** (TCP 4000+K) | open standard, no driver; one TCP port per console | Windows tools may need a virtual COM port |
| Real COM (data 950+K, command 966+K) | local COM port for any Windows tool | Moxa driver on every workspace; two TCP ports per console |

## Evidence
- E14 (own console with baud change, foreign consoles unreachable, one session per port, regroup) passes with
  RFC 2217 in all six labs ([consoles-usb.md](../../containerlab/vlabzone/docs/consoles-usb.md#2-serial-consoles-moxa-nport-5600-e14)).
- `vlab-cfg` already computes both port lists (`serial_tcp_ports`); Real COM is a config change
  (`mode:` of the server in `config/setups.yaml`), not yet tested.

## Decision
Open. Working assumption: RFC 2217.

## Consequences
Depends on the Windows workspaces (E24) and the tools the team uses on the consoles.
