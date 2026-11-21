# EDGE_MODE=vrf – one VRF per setup (docs/concept.md §5.6; access concept §5.4).
#
# Per setup N (VLAN V, net 192.168.1.0/24):
#   vrf-sN     VRF with routing table 1000+N
#   eth2.V     VLAN subinterface in vrf-sN, gateway IP (192.168.1.254)
#   wg-sN      WireGuard on UDP 51800+N, 10.250.0.1/16, in vrf-sN
#   MASQUERADE on eth2.V (devices answer to .254 even with a wrong default gateway)
# The WireGuard UDP sockets live in the default VRF (eth1), the decrypted traffic
# only exists inside vrf-sN – so a setup can never route into another one.
#
# nftables + VRF: for traffic arriving on a VRF slave, input/forward hooks see the
# VRF device as iifname (vrf-sN). The real ingress interface is `meta sdifname`.
# shellcheck shell=bash

PORT_BASE=$(vlab-cfg get edge.wg_port_base)
TUN_IP=$(vlab-cfg get edge.tunnel_ip)
TUN_PREFIX=$(vlab-cfg get edge.tunnel_net | cut -d/ -f2)

nft -f - <<NFT
flush ruleset
table inet edge {
  chain input {
    type filter hook input priority filter; policy drop;
    ct state established,related accept
    ct state invalid drop
    iif lo accept
    iifname "eth1" udp dport 51800-52099 accept comment "WireGuard from corporate"
    meta sdifname "wg-s*" ip daddr $TUN_IP icmp type echo-request accept comment "tunnel gateway ping"
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
  # one rule per setup: tunnel -> own VLAN only
  chain setups { }
  chain postrouting {
    type nat hook postrouting priority srcnat; policy accept;
    jump nat-setups
  }
  # one chain per setup so that `edge-ctl nat off` can flush it (E04)
  chain nat-setups { }
}
NFT

while read -r id vlan net gw; do
  prefix=${net#*/}
  vrf=vrf-s$id; vif=eth2.$vlan; wg=wg-s$id; port=$((PORT_BASE + id))

  ip link add "$vrf" type vrf table $((1000 + id))
  ip link set "$vrf" up

  ip link add link eth2 name "$vif" type vlan id "$vlan"
  ip link set "$vif" master "$vrf" up
  ip addr add "$gw/$prefix" dev "$vif"

  wg genkey > "/run/vlab/keys/$wg.key"
  chmod 600 "/run/vlab/keys/$wg.key"
  ip link add "$wg" type wireguard
  wg set "$wg" private-key "/run/vlab/keys/$wg.key" listen-port "$port"
  ip link set "$wg" master "$vrf"
  ip addr add "$TUN_IP/$TUN_PREFIX" dev "$wg"
  ip link set "$wg" up

  nft add rule inet edge setups meta sdifname "$wg" oifname "$vif" accept
  nft add chain inet edge "nat-s$id"
  nft add rule inet edge nat-setups jump "nat-s$id"
  nft add rule inet edge "nat-s$id" oifname "$vif" masquerade

  log "setup $id: $vrf, $vif ($gw/$prefix), $wg on UDP $port"
done < <(vlab-cfg setups)
