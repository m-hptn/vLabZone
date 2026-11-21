# Serial consoles and USB devices – shared appliances (E14, E26)

Nodes: `nport1` (serial device server), `ausb1` (USB hub) · in **every** lab and edge mode ·
Tests: [`test_e14_serial.py`](../tests/test_e14_serial.py), [`test_e26_usb.py`](../tests/test_e26_usb.py)

In the real environment the devices' serial consoles are cabled to a **Moxa NPort 5600**, and their USB
interfaces to a **Digi AnywhereUSB 24 Plus**. Neither belongs to a setup: one NPort and one hub serve the
devices of **all** setups, and the grouping changes. (The [concept](../../../docs/concept.md#55-serial-access-instance) planned one access instance per setup,
`acc-12-ser` inside the setup network. That doesn't match this hardware.)

The question is: **how does a booked workspace reach the console and the USB ports of its own devices,
and none of another setup's?** The answer differs for the two appliances, because of how they expose
their ports:

| | Moxa NPort 5600 (serial) | Digi AnywhereUSB 24 Plus (USB) |
|---|---|---|
| How ports are exposed | **one TCP port per serial port** (RFC 2217 mode: 4000+K) | **one TCP port for all USB ports** (18574) |
| Who decides which port a setup gets | **the edge** (network): per setup only the TCP ports of its devices' consoles | **the hub** (groups): group N = setup N's USB ports, members = the clients of setup N's bookings |
| On booking / revoke | nothing on the appliance; the tunnel is the access | the broker adds / removes the client in the group (on the real hub: client ID) |
| On regrouping a device | `edge-ctl console deny` (old setup) + `allow` (new setup) | change the hub's groups (`hub-ctl group set`) |
| Lab stand-in | `nport-sim.py`: RFC 2217 server (pyserial's `PortManager`) and a device console per port | `usbhub-sim.py`: USB/IP server with groups, one emulated device per port |

---

## 1. Network: the appliance VLAN

```text
   tunnel 10.250.12.1 ─ lab-edge ═ eth2.197 (10.197.0.1) ═ lab-agg ─┬─ eth31 nport1 10.197.0.10  TCP 4001-4008
   (setup 12)           routing + filter per setup                  └─ eth32 ausb1  10.197.0.11  TCP 18574
```

| Setting | Why |
|---|---|
| Own VLAN 197, 10.197.0.0/24, on the aggregation switch (access ports eth31/eth32), edge .1 **[real]** | The appliances are shared, so they can't be in a setup VLAN. On their own VLAN they're separate from the switch management (10.198) and reachable **only through the edge**, the one place that knows which setup a tunnel belongs to. The location in the real network isn't decided yet; this is the recommendation |
| The edge trunk carries VLAN 197 in **all** modes (also PBR) **[real]** | the edge is the appliances' gateway |
| **No NAT** towards the appliances **[real]** | the NPort and the hub see the tunnel address `10.250.<setup>.<user>`, so the setup is visible in the source address ([tunnel-addresses.md](tunnel-addresses.md)). Logs on the appliances show which booking used a port |
| Appliances: route `10.250.0.0/16 via 10.197.0.1`, no other route, no management interface **[sim]** | they only talk to the tunnels. The real devices are managed over their own web/REST interface, later by the broker |
| **No new corporate firewall rule** | everything runs inside the WireGuard tunnel; E01 still sees exactly one allowance (UDP 51820 in PBR/hybrid) |

**Routing per edge mode** (`images/edge/edge-appliances.sh`; RouterOS: `_appliances()` in the driver):

| Mode | Towards the appliances | Replies back to the tunnels |
|---|---|---|
| VRF | route leak `10.197.0.0/24 dev eth2.197` in every setup table (`table 1000+N`) | `10.250.N.0/24 dev wg-sN` in main: **one /24 per setup** makes this possible (with `10.250.0.0/16` on every `wg-sN` main couldn't choose) |
| hybrid | the same route leak in every setup VRF table | `10.250.0.0/16 dev wg0` in main (exists anyway) |
| PBR | `ip rule to 10.197.0.0/24 iif wg0 lookup main pref 9000`, before the booking rules | `10.250.0.0/16 dev wg0` in main |

Read from the running labs:

```text
$ docker exec clab-vlabzone-vrf-lab-edge ip route | grep 10.250
10.250.5.0/24 dev wg-s5 scope link
10.250.7.0/24 dev wg-s7 scope link
10.250.12.0/24 dev wg-s12 scope link
$ docker exec clab-vlabzone-pbr-lab-edge ip rule
9000:   from all to 10.197.0.0/24 iif wg0 lookup main
30000:  from all iif wg0 unreachable
```

> **Finding (VRF mode):** the reply route `10.250.N.0/24 dev wg-sN` in main is bound to the interface. Moving
> `wg-sN` to another VRF or recreating it deletes the route, and the appliances' replies for that setup are
> lost from then on, while the setup itself keeps working. The full suite showed this: E10's "wrong VRF"
> case moves `wg-s12` and back, and E14/E26 failed afterwards. The test now restores the route. For the real
> edge, the broker's reconcile loop has to check this route along with the peers. Hybrid and PBR don't have it:
> their reply route is the `10.250.0.0/16` on `wg0`.

**Filter** (nft chain `appliances`, jumped to first in `forward`; one set per setup):

```text
$ docker exec clab-vlabzone-hybrid-lab-edge nft list chain inet edge appliances
iifname "wg0" oifname "eth2.197" ip saddr 10.250.12.0/24 ip daddr . tcp dport @console_s12 accept comment "setup 12 consoles"
iifname "wg0" oifname "eth2.197" ip saddr 10.250.12.0/24 ip daddr . tcp dport @usb_hubs accept comment "setup 12 usb hubs"
…
$ docker exec clab-vlabzone-hybrid-lab-edge nft list set inet edge console_s12
elements = { 10.197.0.10 . 4001, 10.197.0.10 . 4002 }
```

The source prefix `10.250.N.0/24` is what separates the setups (in VRF mode the rule matches
`meta sdifname "wg-sN"`). Setup devices (192.168.1.x) never match, even though the setup VRF holds a route to
the appliance VLAN (E14c). The sets come from the inventory: `serial: { server: nport1, port: K }` per device
in [`setups.yaml`](../config/setups.yaml), `vlab-cfg consoles <setup>`.

---

## 2. Serial consoles (Moxa NPort 5600, E14)

**Operation mode RFC 2217** (assumed, not decided yet): the client gets a network COM port and sets the line
(baud rate, data bits, parity, stop bits, DTR/RTS) over the TCP connection. Works with pyserial, PuTTY-like
tools and `socat`, no driver on the workspace.

```bash
docker exec -it clab-vlabzone-hybrid-ws-std lab console 12 sys1-dev1     # after a tunnel to setup 12 exists
# = python3 -m serial.tools.miniterm --raw rfc2217://10.197.0.10:4001 9600 (in the setup's namespace if any)
```

| Other NPort mode | What changes in the lab design |
|---|---|
| **Real COM** (Moxa driver on the workspace, local COM/ttyr ports) | only the port list: data port 950+K **and** command port 966+K per serial port. `vlab-cfg` already computes both (`mode: realcom`); the sets then hold two entries per console |
| **TCP server** (raw TCP, 4000+K) | same ports as RFC 2217, but the client can't change the baud rate |

**The stand-in** (`images/serial`, node `nport1`, `nport-sim.py`): port K listens on TCP 4000+K and plays the
console of the device cabled to it (`sys1-dev1 login:`, `show version`, `show baud`, `show lines`, `reboot`).
`show baud` reports what the **client** negotiated, so a baud-rate change is visible end to end. One session per
port at a time, like the NPort's "max connection 1"; a second client is disconnected at once.

> First attempt: ser2net (RFC 2217) on a pty. That fails: a pty has no modem lines, so ser2net never
> acknowledges the DTR change that every RFC 2217 client sends when it opens the port, and pyserial and
> miniterm time out. `nport-sim.py` uses pyserial's RFC 2217 server (`serial.rfc2217.PortManager`) on an
> in-memory port instead; the negotiation on the wire is the same.

**Regrouping** (with the switch port, by the broker in stage 3):

```bash
edge-ctl console deny  --setup 7  --device sys1-dev3    # also ends a running session (conntrack -D)
edge-ctl console allow --setup 12 --device sys1-dev3
edge-ctl console list  [--setup N]                      # setup server-ip tcp-port
```

---

## 3. USB (Digi AnywhereUSB 24 Plus, E26)

The AnywhereUSB has one TCP port (18574) for all 24 USB ports, so a firewall can't give a setup only some of
them. The edge lets every setup reach the hub (`@usb_hubs`); **the hub decides**:

- **group N** = the hub ports of setup N's devices (`usb: { hub: ausb1, port: K }` in `setups.yaml`)
- **members** of group N = the clients of setup N's bookings. On the real hub a client is identified by its
  **client ID** (AnywhereUSB Manager on the workspace); in the lab by its tunnel address **[sim]**.
- **The broker keeps both in step**: on booking, add the client to the group; on revoke, remove it. The hub
  then ends running sessions. On regrouping, move the hub port between groups.

```bash
docker exec clab-vlabzone-hybrid-ausb1 hub-ctl group list                       # 12 1 / 7 2 / 5 3
docker exec clab-vlabzone-hybrid-ausb1 hub-ctl client add 10.250.12.1 --group 12
docker exec clab-vlabzone-hybrid-ausb1 hub-ctl client del 10.250.12.1           # revoke
docker exec clab-vlabzone-hybrid-ws-std lab usb list 12                          # usbip-client list
```

**The stand-in** (`images/usbhub`, node `ausb1`, `usbhub-sim.py`) speaks the **Linux USB/IP** protocol (the
AnywhereUSB protocol is proprietary; USB/IP has the same shape: list the ports, import one, then the device's
URBs run over that connection). It lists only the ports of the client's groups, refuses foreign or busy ports,
and disconnects a session within ~0.5 s when the membership is removed. Every port emulates a small device
(vendor class, product string `"<device> USB"`) that answers enumeration.

The workspace has two clients:

| Client | Needs | Used by |
|---|---|---|
| `usbip-client list/import` (userspace, `vlab_usbip.py`) | nothing | the tests: import a port and read its product string over the imported connection |
| `usbip attach` / `lab usb attach` (kernel) | `vhci-hcd` loaded on the **host** | E26e, skipped otherwise. The attached device appears host-wide (`lsusb` on the host), because containers share the kernel. That's why `vhci-hcd` isn't loaded by the Ansible roles |

---

## 4. Results

`make test EDGE_MODE=<mode>` (E14: 7 tests, E26: 4 + 1 optional), Linux edge:

| Test | VRF | PBR | hybrid |
|---|---|---|---|
| E14a own console, baud 9600 → 115200 arrives on the line | ✅ | ✅ | ✅ |
| E14b console of setup 7's device / ungrouped device unreachable from setup 12 | ✅ | ✅ | ✅ |
| E14c setup device can't reach the NPort | ✅ | ✅ | ✅ |
| E14d one session per port | ✅ | ✅ | ✅ |
| E14e regrouping moves the console (deny/allow) | ✅ | ✅ | ✅ |
| E26a hub lists nothing without membership, only own ports with it | ✅ | ✅ | ✅ |
| E26b import own port ✅, foreign port ❌, booked but not a member ❌ | ✅ | ✅ | ✅ |
| E26c revoke ends a running USB session (measured 0.6 s) | ✅ | ✅ | ✅ |
| E26d other setups reach the hub but see none of the ports | ✅ | ✅ | ✅ |
| E26e kernel `usbip attach` | skipped (vhci-hcd) | skipped | skipped |

MikroTik edge: the same 11 pass in all three modes through the driver ([lab-routeros.md](lab-routeros.md)).
Full suites: Linux VRF 81, PBR 86, hybrid 82 passed; MikroTik VRF 80, PBR 85, hybrid 82 passed (0 failed).

---

## Carried over to the real environment

1. Put the NPort and the AnywhereUSB on their own VLAN behind the lab edge, with **no NAT**, so the appliances
   see the tunnel addresses. No change on the corporate firewall. VRF mode only: a reply route per setup into
   its VRF, which must be re-checked whenever the setup's WireGuard interface changes (reconcile).
2. Serial: the edge gives each setup exactly its devices' console ports (one rule per console on RouterOS).
   Regrouping changes them together with the switch port. Decide the NPort mode: RFC 2217 needs one port per
   console, Real COM two.
3. USB: the AnywhereUSB's groups and client IDs are part of every booking. The broker must add the client on
   booking and remove it on revoke, through the hub's management API, and check both in its reconcile loop.
4. Console sessions are exclusive; the broker should show who holds a console.
