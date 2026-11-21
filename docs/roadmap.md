# Roadmap

Where the project stands and what comes next. The detailed log of how each step was built, with its findings, is
[history.md](history.md); the original concept is [concept.md](concept.md).

## Status

| Stage | Content | Experiments | Status |
|---|---|---|---|
| 0 – Host | Ansible provisioning (local and remote), preflight, images, smoke lab | E00 | ✅ 2026-11-20 |
| 1 – Network skeleton | corporate firewall, Linux edge (VRF), switches, devices, workspaces with static tunnels | E01–E06, E09 | ✅ 2026-11-20 |
| 1b – RouterOS trial | MikroTik CHR as VRF, PBR and hybrid edge, by hand | – | ✅ 2026-11-20 |
| 2a – Real switching | aggregation + system switches, devices grouped into setups dynamically | E02, E05, E20 | ✅ 2026-11-20 |
| 2 – Variants | PBR and **hybrid** modes, tunnel addresses `10.250.<setup>.<user>`, namespaces, alias mode, MikroTik edge in every lab, serial consoles (NPort) and USB (AnywhereUSB) | E10, E11, E13, E14, E26, E27 | ✅ 2026-11-21 |
| 3 – Broker and agent | broker API, reconcile loop, `lab-agent`, conflict check, audit | E07, E08, E12, E18, E19, E20 (via broker), E27 part 2 | ⏳ next |
| 4 – Operations and load | MTU, keepalive, latency, scale, throughput; optional SSO, Windows path, DNS | E15–E17, E21–E25 | ⏳ |

Results per experiment and lab: [experiments.md](experiments.md). Decisions and open questions:
[decisions/](decisions/README.md).

## Next: stage 3 – broker and agent

| Step | Content |
|---|---|
| 15 | FastAPI broker + SQLite (users, setups, devices, bookings, contexts, audit), token auth, TLS on 443; endpoints per [concept §5.7](concept.md#57-broker-prototype); drivers behind one interface (Linux edge via `edge-ctl` over SSH, MikroTik via `drivers/edge_routeros.py`, switches via `sw-port`, USB hub via `hub-ctl`) |
| 16 | Reconcile loop: desired state (DB) vs. actual state, expiry, revoke |
| 17 | Conflict checks: two setups with the same network in desktop mode, collision with corporate networks → suggest namespace or alias |
| 18 | `lab-agent` + `lab connect / disconnect`, keys generated locally; replaces the static tunnels of the test fixtures |
| 19 | E18 for the MikroTik: CHR reboot followed by reconcile |
| 20 | Tests E07, E08 (revoke ≤ 1 s), E12, E18, E19, E20 through the broker, for both edge implementations; E27 part 2 |

**What the reconcile loop must cover** – found while building stages 1–2:

- WireGuard peers vs. bookings, including the peer's allowed address (the one thing a booking can still get wrong, [ADR 0003](decisions/0003-one-corporate-port-edge-mode.md))
- PBR only: the `ip rule` per booking
- the console set per setup on the edge (`edge-ctl console`) after regrouping
- the USB hub's groups and client IDs per booking ([ADR 0007](decisions/0007-usb-access-enforced-by-hub-groups.md))
- VRF mode: the main-table reply route `10.250.N.0/24 dev wg-sN`, which is lost when `wg-sN` changes VRF or is recreated ([consoles-usb.md](../containerlab/vlabzone/docs/consoles-usb.md))
- switch port VLANs vs. the grouping, and refusing two same-IP devices in one setup (E20b)
- creating and deleting setups at runtime (`edge-ctl setup add|del` doesn't exist yet)

## Later: stage 4 – operations and load

| Step | Content |
|---|---|
| 21 | netem / MTU / UDP-timeout helpers on `corp-fw`; E15 MTU sweep → MSS-clamp recommendation; E16 keepalive; E17 latency matrix |
| 22 | Scale topology generator: 50 setups / 200 contexts (edge-side devices only) → E21 per mode (evidence for [ADR 0003](decisions/0003-one-corporate-port-edge-mode.md)); E22 throughput. MikroTik numbers need a CHR licence and are not representative of real MikroTik hardware |
| 23 | Optional: `samba-ad` + Kerberos (E23), SOCKS setup shell (E24), DNS per setup (E25) |

## Open items outside the stages

- **Real MikroTik:** repeat the RouterOS tests with the real router's RouterOS version ([ADR 0004](decisions/0004-routeros-edge-driver-rest.md)).
- **Appliances:** decide placement and NPort mode ([ADR 0005](decisions/0005-appliance-placement.md), [0006](decisions/0006-nport-operation-mode.md)); verify client IDs on the real AnywhereUSB ([0007](decisions/0007-usb-access-enforced-by-hub-groups.md)).
- **Kernel USB attach:** run E26e on a host with `vhci-hcd` loaded.
- **Address layout:** make the corporate and management addresses config-driven ([evaluating.md §4](evaluating.md#4-address-layout)).
- **Provisioning:** run the playbook on a fresh Debian 12 and Ubuntu 24.04 VM before the team relies on it (plan of stage 0, step 4).
- **Follow-up stage** beyond the containers (nested Proxmox, Windows VM, one real switch and devices): [concept §9](concept.md#9-limits-and-follow-up-stage).

## Working rules for every step

- Each step ends with the full suite green in every lab it touches (Linux and MikroTik edge), then a commit.
- Findings go into the detail doc of the topic with real outputs, and into an ADR if they change a decision.
- Portability: no hardcoded host paths, users or hostnames; versions pinned in `deploy/ansible/group_vars/all.yml`;
  supported hosts Debian 12/13, Ubuntu 24.04 (x86_64); the MikroTik variant is opt-in because it needs KVM.
