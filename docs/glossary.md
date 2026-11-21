# Glossary

| Term | Meaning |
|---|---|
| **Hardware pool** | the lab devices (boards, DUTs) that users book remotely; the reason this project exists |
| **Workspace** | a user's vSphere VM in the corporate network. In the lab: `ws-std` (desktop mode), `ws-power` (namespace per setup), `ws-ci` (alias mode) |
| **Corporate firewall** | the firewall between workspaces and the lab. It should need **one** rule: WireGuard UDP to the lab edge (`corp-fw` in the lab) |
| **Lab edge** | the router at the lab's border: terminates WireGuard, separates setups, NATs into them. Real: a MikroTik. In the lab: `lab-edge`, Linux or RouterOS CHR |
| **Aggregation switch** | the switch the edge, the Proxmox nodes, the appliances and the system switches hang on. Real: Lancom. Lab: `lab-agg` |
| **System switch** | a switch with the devices of one rack/system. Lab: `lab-sys1`, `lab-sys2` |
| **Device** | one piece of hardware in the pool, on an access port of a system switch (`sys1-dev1` …). Identical boards may have identical IPs |
| **Setup** | a bookable group of devices on **one** system switch, with its own VLAN and its own copy of the (overlapping) network, e.g. setup 12 = `sys1-dev1` + `sys1-dev2` in `192.168.1.0/24`, VLAN 112 |
| **Grouping** | which devices currently form which setup. Changes at runtime (regrouping: switch port VLAN, console access, USB hub group) |
| **Ungrouped device** | a device in no setup: its access port is in no VLAN, it is cut off |
| **Booking** | a user's right to a setup for a period; the broker turns it into a context |
| **Context** | one WireGuard tunnel from a workspace into one setup, with a tunnel address |
| **Tunnel address** | the workspace's address inside the tunnel: `10.250.<setup>.<user>` ([ADR 0002](decisions/0002-tunnel-addresses-setup-first.md)) |
| **Edge mode** | how the edge separates setups: **VRF** (VRF + WireGuard port per setup), **PBR** (one `wg0`, policy routing per booking to a gateway `gw-N` per setup), **hybrid** (one `wg0`, policy routing per setup into edge VRFs) – [ADR 0003](decisions/0003-one-corporate-port-edge-mode.md) |
| **Transfer VLAN** | PBR only: VLAN 199 between edge and the setup gateways `gw-N` |
| **Original-IP mode** | the workspace reaches a device at its real address (`192.168.1.20`); one setup with a given network at a time per namespace |
| **Namespace mode** | power users: one Linux network namespace per setup on the workspace (`lab enter 12`), so setups with the same network run in parallel |
| **Alias mode** | setup N is also reachable as `10.200.N.0/24` (edge translates the prefix), so CI can reach several setups at once without namespaces |
| **MASQUERADE** | the edge (or `gw-N`) rewrites the source to the setup gateway `.254`, so devices answer even with a wrong default gateway |
| **Appliance** | a shared serial or USB server for the devices of all setups: Moxa NPort 5600 (`nport1`), Digi AnywhereUSB 24 Plus (`ausb1`), in the appliance VLAN 197 |
| **Console set** | per setup, the NPort TCP ports of its devices' consoles that the edge lets the setup's tunnels reach (`edge-ctl console`) |
| **Hub group** | on the USB hub, the USB ports of one setup and the clients (bookings) allowed to use them (`hub-ctl`) |
| **Broker** | the service that will own bookings, configure edge/switches/appliances through drivers and reconcile actual with desired state (stage 3) |
| **lab-agent / `lab` CLI** | the workspace side: creates keys and tunnels, `lab list`, `lab enter`, `lab console`, `lab usb` |
| **Reconcile** | the broker's loop that compares the desired state (bookings, grouping) with what the edge, switches and appliances actually have, and fixes the difference |
| **Fail-closed** | a misconfiguration that blocks access instead of opening a path to the wrong setup |
| **[real] / [sim]** | tag on every configuration item in the docs: carries over to the real environment / exists only because the lab is simulated |
| **E01 … E27** | the experiments ([experiments.md](experiments.md)) |
| **Stage** | a block of the roadmap (0 host, 1 network skeleton, 2 variants, 3 broker and agent, 4 operations and load) – [roadmap.md](roadmap.md) |
| **CHR** | MikroTik Cloud Hosted Router: RouterOS as a VM, used in the lab as the MikroTik edge |
