# gw-N: setup gateway for the PBR edge mode.
#
#   eth1        one trunk to lab-agg (like a VM on a Proxmox node at the aggregation switch)
#   eth1.199    transfer VLAN: 10.199.0.N/24 (edge = 10.199.0.1)
#   eth1.<V>    setup VLAN: the setup's gateway address (192.168.1.254/24)
#
# In PBR mode the edge has no interface in any setup. It routes each tunnel IP
# (ip rule) to the gateway of the booked setup; the gateway does the MASQUERADE,
# so the devices see 192.168.1.254 as client exactly like in VRF mode.
# shellcheck shell=bash

SETUP=${SETUP:?SETUP must be set for ROLE=gw}
GW_IP=$(vlab-cfg gw-ip "$SETUP")
TRANSFER_VLAN=$(vlab-cfg get edge.transfer.vlan)
TRANSFER_PREFIX=$(vlab-cfg get edge.transfer.net | cut -d/ -f2)
EDGE_IP=$(vlab-cfg get edge.transfer.edge_ip)
TUN_NET=$(vlab-cfg get edge.tunnel_net)
SETUP_VLAN=$(vlab-cfg setup "$SETUP" vlan)
SETUP_GW=$(vlab-cfg setup "$SETUP" gateway)
SETUP_PREFIX=$(vlab-cfg setup "$SETUP" net | cut -d/ -f2)
TIF=eth1.$TRANSFER_VLAN
SIF=eth1.$SETUP_VLAN

wait_if 60 eth1
ip link set eth1 up
ip link add link eth1 name "$TIF" type vlan id "$TRANSFER_VLAN"
ip link add link eth1 name "$SIF" type vlan id "$SETUP_VLAN"
ip addr add "$GW_IP/$TRANSFER_PREFIX" dev "$TIF" && ip link set "$TIF" up
ip addr add "$SETUP_GW/$SETUP_PREFIX" dev "$SIF" && ip link set "$SIF" up
# Replies to the tunnel addresses go back to the edge. No default route:
# nothing from the setup may leave towards anything else.
ip route add "$TUN_NET" via "$EDGE_IP"
sysctl_set net.ipv4.ip_forward 1
echo "$SIF" > /run/vlab-setup-if

nft -f - <<NFT
flush ruleset
table inet gw {
  chain input {
    type filter hook input priority filter; policy drop;
    ct state established,related accept
    iif lo accept
    iifname "$TIF" ip saddr $EDGE_IP icmp type echo-request accept comment "edge may ping its gateways"
    counter comment "dropped input"
  }
  chain forward {
    type filter hook forward priority filter; policy drop;
    ct state established,related accept
    ct state invalid drop
    # only tunnel traffic from the edge into this setup – nothing in the other direction,
    # nothing back into the transfer VLAN (other gateways)
    iifname "$TIF" oifname "$SIF" ip saddr $TUN_NET accept
    counter comment "dropped forward"
  }
  chain postrouting {
    type nat hook postrouting priority srcnat; policy accept;
    jump nat
  }
  # separate chain so that gw-ctl nat off can flush it (E04)
  chain nat {
    oifname "$SIF" masquerade
  }
}
NFT
log "gw-$SETUP: transfer $TIF $GW_IP/$TRANSFER_PREFIX, setup $SIF $SETUP_GW/$SETUP_PREFIX"
