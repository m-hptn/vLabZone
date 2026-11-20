# Tunnel addresses: 10.250.\<setup\>.\<user\>

Every WireGuard context (one user in one setup) gets one tunnel address from `10.250.0.0/16`:

```text
10.250.<setup>.<user>          e.g. Alice (user 1) in setup 12  ->  10.250.12.1
       └─ 1-255  └─ 1-254           Bob   (user 2) in setup 7   ->  10.250.7.2
                                    CI    (user 3) in setup 5   ->  10.250.5.3
10.250.0.1                     the edge itself (tunnel gateway, all modes)
10.250.<setup>.0/24            all contexts of one setup  ("setup prefix")
```

It is computed in one place, `vlab-cfg tunnel-ip <setup> <user>` / `vlab-cfg setup-prefix <setup>`
([`vlab-cfg`](../images/base/rootfs/usr/local/bin/vlab-cfg)); the tests use the same functions
(`tests/lib/labcfg.py`, `tests/lib/wg.py`).

**Changed on 2026-11-20.** Until then the layout was 10.250.\<user\>.\<setup\>, as in the concept
([idea.md §4.3](../../../idea.md)). This document explains why it was turned round.

---

## 1. Why

### The constraint: one port in the corporate firewall

The corporate firewall should need **one rule with one UDP port** towards the lab edge. That rules out the VRF
design (one WireGuard interface and port per setup) and leads to one WireGuard interface `wg0` for every setup:
the PBR or the hybrid design ([vrf-vs-pbr.md](vrf-vs-pbr.md)). With one interface, the port no longer tells
the edge which setup a tunnel belongs to. **The tunnel address has to.**

### With the setup in the last octet, the edge needs a rule per booking

With 10.250.\<user\>.\<setup\>, the contexts of setup 12 are 10.250.1.12, 10.250.2.12, 10.250.3.12, … They
have no common prefix. A routing rule can only match a prefix, so every booking needed its own rule:

```text
ip rule from 10.250.1.12 iif wg0 lookup 1012       # Alice -> setup 12
ip rule from 10.250.2.7  iif wg0 lookup 1007       # Bob   -> setup 7
```

Each of these rules is a per-booking change that can be wrong, and a wrong one gives access to a foreign
setup (E10, PBR lab: Alice booked setup 12 and got answers from setup 7).

### Linux could check the last octet, RouterOS can't

On the Linux edge, a firewall rule can catch such a mistake by masking the address:
`ip saddr & 0.0.0.255 == 12` ("only addresses ending in .12 may enter setup 12"). The real edge is a MikroTik.
In the RouterOS trial ([routeros-spike.md, section 6](routeros-spike.md#6-hybrid-design-on-routeros)):

```text
src-address=10.250.0.12/255.255.0.255   ->  RouterOS stores it as the range 10.250.0.12-10.250.255.12
```

That range contains every address in between; it is no last-octet match. RouterOS firewall and routing rules
only work with **prefixes and ranges**. So with the old layout the MikroTik could neither group a setup's
bookings into one rule nor check that a rule is right.

### With the setup in the third octet, each setup is a prefix

```text
setup 12  ->  10.250.12.0/24      setup 7  ->  10.250.7.0/24      setup 5  ->  10.250.5.0/24
```

That turns per-booking configuration into per-setup configuration:

| | old: 10.250.\<user\>.\<setup\> | **new: 10.250.\<setup\>.\<user\>** |
|---|---|---|
| Rule that selects the setup (hybrid) | one per **booking** | one per **setup**, written when the setup is created: `from 10.250.12.0/24 iif wg0 lookup 1012` |
| Changes on the edge per booking | 2 (peer + rule) | **1** (the peer), as in the VRF design |
| Check "this address belongs to setup N" | Linux: mask on the last octet; **RouterOS: impossible** | a plain prefix: Linux `ip saddr 10.250.12.0/24`, RouterOS `src-address=10.250.12.0/24` |
| Wrong setup rule | foreign access unless the mask check exists | **fail-closed** on both platforms (E10 hybrid, RouterOS trial) |
| Address outside the setup's prefix registered by mistake | not detectable | **refused** by `edge-ctl peer add` (E10) |
| Reading an address | user first | setup first: `10.250.12.x` is "someone in setup 12" in every log, capture and rule |

### What a booking can still get wrong

A booking now only adds a WireGuard peer with `allowed-ips 10.250.<setup>.<user>/32`. `edge-ctl` refuses an
address outside the setup's prefix. A peer registered on the **wrong setup together with that setup's address**
(`--setup 7 --ip 10.250.7.1` for a setup-12 booking) is consistent in itself. If the agent then connects with
10.250.12.1, the edge drops its packets (cryptokey routing: the key may only send from 10.250.7.1). Only if
the agent is also told the wrong address does the user end up in setup 7. That needs the same wrong setup in
two places, and it is the same class of mistake as a VRF peer on the wrong `wg-sN`. The broker's reconcile
loop (stage 3) compares peers with bookings.

---

## 2. What it means per mode

| Mode | Effect |
|---|---|
| **hybrid** | the rule per booking is gone: one static rule per setup (`from 10.250.N.0/24`), and the forward safeguard is a prefix (`ip saddr 10.250.N.0/24`). Per booking: 1 change. See [lab-hybrid.md](lab-hybrid.md) |
| **VRF** | no functional change: the setup is chosen by the WireGuard interface. Addresses are only easier to read |
| **PBR** | kept with a rule per booking (`from 10.250.12.1 lookup 1012`), because that is the design being compared. With the new layout a static rule per setup would work there too |

---

## 3. Limits

| Limit | Value | Why |
|---|---|---|
| Setups | 1–255 | third octet; setup 0 would collide with the edge's 10.250.0.1 |
| Users per setup | 1–254 | fourth octet without .0 and .255 |
| Contexts in total | 255 × 254 | `10.250.0.0/16` |

If setups ever need more than 254 users, or more than 255 setups exist, the tunnel net grows (e.g. a `/15` with
two octets per setup). Rules and checks stay prefix based.

---

## 4. Where it is used

| Place | What |
|---|---|
| [`vlab-cfg`](../images/base/rootfs/usr/local/bin/vlab-cfg) | `tunnel-ip`, `setup-prefix` (one implementation for nodes and tests) |
| [`edge-ctl`](../images/edge/edge-ctl) | `peer add` refuses an address outside `10.250.<setup>.0/24` (every mode) |
| [`edge-hybrid.sh`](../images/edge/edge-hybrid.sh) | static rule and forward safeguard per setup prefix |
| [`tests/lib/wg.py`](../tests/lib/wg.py) | `tunnel_ip(user, setup)` → 10.250.\<setup\>.\<user\> |
| [`scripts/spike-routeros.py`](../scripts/spike-routeros.py) | RouterOS trials, same layout |
| broker and agent (stage 3) | allocate addresses with this layout; the reconcile loop checks peers against it |
