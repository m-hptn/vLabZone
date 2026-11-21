# Experiments

Every experiment answers one question from the [concept](concept.md#6-experiments). Implemented experiments are pytest
modules in [`containerlab/vlabzone/tests/`](../containerlab/vlabzone/tests/) and write a protocol per case to
`containerlab/vlabzone/results/` (JSON, fields of [concept §8](concept.md#8-recording-results); not committed).

**Status as of 2026-11-21** (last full runs after stage 2 step 13, `make test` per lab, 0 failed):

| Lab | Edge | passed | skipped |
|---|---|---|---|
| `vlabzone-vrf` | Linux | 81 | 12 |
| `vlabzone-pbr` | Linux | 86 | 10 |
| `vlabzone-hybrid` | Linux | 82 | 11 |
| `vlabzone-vrf-ros` | MikroTik (CHR 7.24.6) | 80 | 13 |
| `vlabzone-pbr-ros` | MikroTik | 85 | 11 |
| `vlabzone-hybrid-ros` | MikroTik | 82 | 11 |

Skips are by design: mode-specific cases run only in their mode, Linux-internal checks don't apply to the MikroTik,
E27 runs only with the MikroTik, and the kernel USB attach (E26e) needs `vhci-hcd` on the host. The summary at the
end of every `make test` lists the reason of each skip.

## Catalogue

Columns: **V/P/H** = VRF / PBR / hybrid lab, Linux edge · **V-ros/P-ros/H-ros** = the same with the MikroTik edge.
✅ passed · – not applicable in that lab · ⏳ not implemented yet.

### Basic function and isolation (stage 1)

| ID | Question | Module | V | P | H | V-ros | P-ros | H-ros | Details |
|---|---|---|---|---|---|---|---|---|---|
| E00 | Does the host meet the prerequisites? | `test_e00_host.py` | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | [lab README](../containerlab/vlabzone/README.md) |
| E01 | Is one corporate allowance enough (only WireGuard UDP visible)? | `test_e01_corporate_allowance.py` | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | [common.md §1.1](../containerlab/vlabzone/docs/common.md#11-corp-fw--corporate-network-and-firewall) |
| E02 | Are setups with the same network separated (same switch, across switches, trunk pruning)? | `test_e02_overlap_isolation.py` | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | lab docs §2 |
| E03 | Do original IPs work for SSH and logs? | `test_e03_original_ip.py` | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | lab docs |
| E04 | Does access work despite a wrong device gateway (MASQUERADE needed)? | `test_e04_masquerade.py` | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | lab docs |
| E05 | Is there a way around the architecture (workspaces, setups, ungrouped devices)? | `test_e05_no_bypass.py` | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | lab docs |
| E06 | Can a user fake a tunnel IP or use an unregistered key? | `test_e06_spoofing.py` | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | lab docs |
| E09 | Does the namespace isolate a power-user context from the corporate network? | `test_e09_namespace_isolation.py` | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | [common.md §1.2](../containerlab/vlabzone/docs/common.md#12-ws-std-ws-power-ws-ci--workspaces) |

### Variants and access modes (stage 2)

| ID | Question | Module | V | P | H | V-ros | P-ros | H-ros | Details |
|---|---|---|---|---|---|---|---|---|---|
| E10 | VRF, PBR or hybrid: changes per booking, what misconfigurations do | `test_e10_vrf_vs_pbr.py` | ✅ | ✅ | ✅ | ✅¹ | ✅¹ | ✅¹ | [vrf-vs-pbr.md](../containerlab/vlabzone/docs/vrf-vs-pbr.md), [ADR 0003](decisions/0003-one-corporate-port-edge-mode.md) |
| E11 | Parallel setups with the same network for power users, under load | `test_e11_parallel_setups.py` | ✅ | ✅ | ✅ | ✅² | ✅² | ✅² | [common.md §1.2](../containerlab/vlabzone/docs/common.md#12-ws-std-ws-power-ws-ci--workspaces) |
| E13 | Alias mode for CI (setup N as 10.200.N.0/24) | `test_e13_alias_mode.py` | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | [common.md §2.5](../containerlab/vlabzone/docs/common.md#25-alias-addresses-on-the-edge-all-modes) |
| E14 | Serial consoles on the shared NPort: own console, baud change, foreign consoles unreachable, one session per port, regroup | `test_e14_serial.py` | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | [consoles-usb.md](../containerlab/vlabzone/docs/consoles-usb.md), ADRs [0005](decisions/0005-appliance-placement.md), [0006](decisions/0006-nport-operation-mode.md) |
| E20 | Move a device between setups; same IP twice in one setup | `test_e20_regroup.py` | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | [common.md §2.2](../containerlab/vlabzone/docs/common.md#22-lab-agg-lab-sys1-lab-sys2--switches) |
| E26 | USB on the shared AnywhereUSB: only own ports, revoke ends the session | `test_e26_usb.py` | ✅³ | ✅³ | ✅³ | ✅³ | ✅³ | ✅³ | [consoles-usb.md](../containerlab/vlabzone/docs/consoles-usb.md), [ADR 0007](decisions/0007-usb-access-enforced-by-hub-groups.md) |
| E27 | Does the MikroTik edge behave like the Linux edge (misconfigurations over REST)? | `test_e27_routeros_parity.py` | – | – | – | ✅ | ✅ | ✅ | [lab-routeros.md](../containerlab/vlabzone/docs/lab-routeros.md), [ADR 0004](decisions/0004-routeros-edge-driver-rest.md) |

1. On the MikroTik, E10 runs the cases that need no Linux-internal commands; E27 repeats the rest over REST.
2. Free CHR licence: 1 Mbit/s per interface, so the load is limited to 400 kbit/s per stream.
3. The kernel `usbip attach` case (E26e) is skipped unless `vhci-hcd` is loaded on the host (`sudo modprobe vhci-hcd`).

### Broker and agent (stage 3) – not implemented yet

| ID | Question | Status |
|---|---|---|
| E07 | Access without a booking or after expiry? | ⏳ needs the broker |
| E08 | Revoke within ≤ 1 s, also with a running session? | ⏳ |
| E12 | Broker conflict check (same network twice, collision with corporate networks) | ⏳ |
| E18 | Recovery after an edge failure (reconcile loop) | ⏳ |
| E19 | Floating VDI: recreated workspace, new keys, old key useless | ⏳ |

### Operations and load (stage 4) – not implemented yet

| ID | Question | Status |
|---|---|---|
| E15 | Which MTU / MSS clamping? | ⏳ MikroTik needs a CHR licence |
| E16 | Is `PersistentKeepalive` needed with short corporate UDP timeouts? | ⏳ |
| E17 | Latency and packet loss: when does work become uncomfortable? | ⏳ MikroTik needs a licence |
| E21 | Scale: 50 setups / 200 contexts per mode | ⏳ evidence still missing for [ADR 0003](decisions/0003-one-corporate-port-edge-mode.md) |
| E22 | Tunnel throughput and edge CPU | ⏳ MikroTik needs a licence |
| E23 | SSO (Kerberos) instead of tokens | ⏳ optional |
| E24 | Windows access path (setup shell, SOCKS) | ⏳ optional |
| E25 | DNS per setup | ⏳ optional |

## Running experiments

```bash
make test                                  # all experiments against the lab of EDGE_MODE (default vrf)
make test EDGE_MODE=hybrid EDGE_IMPL=routeros
make test-stage2                           # the experiments of one stage
make test PYTEST_ARGS="-k e14"             # one experiment
make test-all                              # all three modes of EDGE_IMPL (make up-all first)
```

How to compare variants and add an experiment: [evaluating.md](evaluating.md).
