# EDGE_MODE=pbr – one WireGuard interface, policy-based routing (docs/concept.md §5.6).
#
#   wg0        all tunnels, UDP 51820, 10.250.0.1/16
#   eth2.199   transfer VLAN 10.199.0.1/24 to the setup gateways gw-N (10.199.0.N)
#   table 1000+N: default via gw-N
#   per context (edge-ctl): ip rule from 10.250.N.U iif wg0 lookup 1000+N
#   (PBR keeps a rule per booking on purpose – it is the design being compared; with the
#   setup-grouped addresses a static rule per setup would work here too, see docs/tunnel-addresses.md)
#
# The edge has no interface in any setup; it only decides – per tunnel source
# address – which gateway a packet goes to. The gateways do the MASQUERADE.
# Isolation therefore rests on the ip rules and on the /32 allowed-ips per key.
# shellcheck shell=bash

PORT=$(vlab-cfg get edge.wg_port_pbr)
TUN_IP=$(vlab-cfg get edge.tunnel_ip)
TUN_NET=$(vlab-cfg get edge.tunnel_net)
TRANSFER_VLAN=$(vlab-cfg get edge.transfer.vlan)
TRANSFER_IP=$(vlab-cfg get edge.transfer.edge_ip)
TRANSFER_PREFIX=$(vlab-cfg get edge.transfer.net | cut -d/ -f2)
TVIF=eth2.$TRANSFER_VLAN

ip link add link eth2 name "$TVIF" type vlan id "$TRANSFER_VLAN"
ip addr add "$TRANSFER_IP/$TRANSFER_PREFIX" dev "$TVIF"
ip link set "$TVIF" up

wg genkey > /run/vlab/keys/wg0.key
chmod 600 /run/vlab/keys/wg0.key
ip link add wg0 type wireguard
wg set wg0 private-key /run/vlab/keys/wg0.key listen-port "$PORT"
ip addr add "$TUN_IP/${TUN_NET#*/}" dev wg0
ip link set wg0 up

# A booking rule into a table without a route would fall through to the main table
# (corporate routes). The catch-all ends the lookup there: no route = unreachable.
ip rule add iif wg0 unreachable pref 30000

while read -r id _vlan _net _gw; do
  gw_ip=$(vlab-cfg gw-ip "$id")
  ip route add default via "$gw_ip" dev "$TVIF" table $((1000 + id))
  log "setup $id: table $((1000 + id)) -> gw-$id ($gw_ip)"
done < <(vlab-cfg setups)

nft -f - <<NFT
flush ruleset
table inet edge {
  chain input {
    type filter hook input priority filter; policy drop;
    ct state established,related accept
    ct state invalid drop
    iif lo accept
    iifname "eth1" udp dport $PORT accept comment "WireGuard from corporate"
    # only the tunnel gateway address – in PBR mode the local table makes ALL edge
    # addresses (e.g. 10.199.0.1, 10.198.0.2) reachable from the tunnels otherwise
    iifname "wg0" ip daddr $TUN_IP icmp type echo-request accept comment "tunnel gateway ping"
    iifname "eth3" tcp dport 22 accept comment "broker management (stage 3)"
    counter comment "dropped input"
  }
  chain forward {
    type filter hook forward priority filter; policy drop;
    ct state established,related accept
    ct state invalid drop
    # tunnels -> transfer VLAN; WHICH gateway is decided by the ip rules
    iifname "wg0" oifname "$TVIF" accept
    counter comment "dropped forward"
  }
}
NFT
log "wg0 on UDP $PORT, transfer $TVIF $TRANSFER_IP/$TRANSFER_PREFIX"
