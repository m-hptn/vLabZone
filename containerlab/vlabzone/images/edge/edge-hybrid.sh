# EDGE_MODE=hybrid – one WireGuard port, setups in VRFs (docs/lab-hybrid.md).
#
#   wg0        all tunnels, UDP 51820, 10.250.0.1/16 (default VRF)
#   vrf-sN     per setup: VRF with table 1000+N
#   eth2.V     per setup: VLAN subinterface in vrf-sN, gateway IP 192.168.1.254, MASQUERADE
#   table 1000+N also holds 10.250.0.0/16 dev wg0 (route leak back to the tunnels)
#   per booking (edge-ctl): ip rule from 10.250.U.N iif wg0 lookup 1000+N
#
# Like PBR, a per-booking ip rule decides which setup a tunnel reaches – one port for the
# corporate firewall. Like VRF, the setups live in VRFs on the edge itself – no gateways.
#
# Two safeguards against a wrong or missing rule (both written once, never per booking):
#   1. ip rule iif wg0 unreachable (pref 30000): a lookup that finds nothing in a setup
#      table ends here instead of falling through to the main table (corporate routes).
#   2. forward rule per setup: a tunnel may only enter VLAN V if the last octet of its
#      tunnel address is N (scheme 10.250.<user>.<setup>). A rule pointing to the wrong
#      table is dropped instead of leading into a foreign setup.
# shellcheck shell=bash

PORT=$(vlab-cfg get edge.wg_port_pbr)
TUN_IP=$(vlab-cfg get edge.tunnel_ip)
TUN_NET=$(vlab-cfg get edge.tunnel_net)

wg genkey > /run/vlab/keys/wg0.key
chmod 600 /run/vlab/keys/wg0.key
ip link add wg0 type wireguard
wg set wg0 private-key /run/vlab/keys/wg0.key listen-port "$PORT"
ip addr add "$TUN_IP/${TUN_NET#*/}" dev wg0
ip link set wg0 up

# Safeguard 1: catch-all after the booking rules (pref 10000), before main (32766)
ip rule add iif wg0 unreachable pref 30000

nft -f - <<NFT
flush ruleset
table inet edge {
  chain input {
    type filter hook input priority filter; policy drop;
    ct state established,related accept
    ct state invalid drop
    iif lo accept
    iifname "eth1" udp dport $PORT accept comment "WireGuard from corporate"
    # only the tunnel gateway address: wg0 is in the default VRF, so the local table
    # makes every main address (10.20.99.2, 10.198.0.2) reachable from the tunnels
    iifname "wg0" ip daddr $TUN_IP icmp type echo-request accept comment "tunnel gateway ping"
    iifname "eth3" tcp dport 22 accept comment "broker management (stage 3)"
    counter comment "dropped input"
  }
  chain forward {
    type filter hook forward priority filter; policy drop;
    ct state established,related accept
    ct state invalid drop
    jump setups
    counter comment "dropped forward"
  }
  # Safeguard 2, one rule per setup: tunnel 10.250.x.N -> VLAN of setup N only
  chain setups { }
  chain postrouting {
    type nat hook postrouting priority srcnat; policy accept;
    jump nat-setups
  }
  chain nat-setups { }
}
NFT

while read -r id vlan net gw; do
  prefix=${net#*/}
  vrf=vrf-s$id; vif=eth2.$vlan; table=$((1000 + id))

  ip link add "$vrf" type vrf table "$table"
  ip link set "$vrf" up
  ip link add link eth2 name "$vif" type vlan id "$vlan"
  ip link set "$vif" master "$vrf" up
  ip addr add "$gw/$prefix" dev "$vif"
  # replies from the setup go back to the tunnels (wg0 is outside the VRF)
  ip route add "$TUN_NET" dev wg0 table "$table"

  nft add rule inet edge setups iifname wg0 oifname "$vif" ip saddr \& 0.0.0.255 == "0.0.0.$id" accept
  nft add chain inet edge "nat-s$id"
  nft add rule inet edge nat-setups jump "nat-s$id"
  nft add rule inet edge "nat-s$id" oifname "$vif" masquerade

  log "setup $id: $vrf (table $table), $vif ($gw/$prefix)"
done < <(vlab-cfg setups)
log "wg0 on UDP $PORT, booking rules: ip rule from 10.250.U.N iif wg0 lookup 1000+N"
