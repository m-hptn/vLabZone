# gw-N: setup gateway for the PBR edge mode.
#
#   eth1  transfer VLAN 199: 10.199.0.N/24 (edge = 10.199.0.1)
#   eth2  setup VLAN: the setup's gateway address (192.168.1.254/24)
#
# In PBR mode the edge has no interface in any setup. It routes each tunnel IP
# (ip rule) to the gateway of the booked setup; the gateway does the MASQUERADE,
# so the devices see 192.168.1.254 as client exactly like in VRF mode.
# shellcheck shell=bash

SETUP=${SETUP:?SETUP must be set for ROLE=gw}
GW_IP=$(vlab-cfg gw-ip "$SETUP")
TRANSFER_PREFIX=$(vlab-cfg get edge.transfer.net | cut -d/ -f2)
EDGE_IP=$(vlab-cfg get edge.transfer.edge_ip)
TUN_NET=$(vlab-cfg get edge.tunnel_net)
SETUP_GW=$(vlab-cfg setup "$SETUP" gateway)
SETUP_PREFIX=$(vlab-cfg setup "$SETUP" net | cut -d/ -f2)

wait_if 60 eth1 eth2
ip addr add "$GW_IP/$TRANSFER_PREFIX" dev eth1 && ip link set eth1 up
ip addr add "$SETUP_GW/$SETUP_PREFIX" dev eth2 && ip link set eth2 up
# Replies to the tunnel addresses go back to the edge. No default route:
# nothing from the setup may leave towards anything else.
ip route add "$TUN_NET" via "$EDGE_IP"
sysctl_set net.ipv4.ip_forward 1

nft -f - <<NFT
flush ruleset
table inet gw {
  chain input {
    type filter hook input priority filter; policy drop;
    ct state established,related accept
    iif lo accept
    iifname "eth1" ip saddr $EDGE_IP icmp type echo-request accept comment "edge may ping its gateways"
    counter comment "dropped input"
  }
  chain forward {
    type filter hook forward priority filter; policy drop;
    ct state established,related accept
    ct state invalid drop
    # only tunnel traffic from the edge into this setup – nothing in the other direction,
    # nothing back into the transfer VLAN (other gateways)
    iifname "eth1" oifname "eth2" ip saddr $TUN_NET accept
    counter comment "dropped forward"
  }
  chain postrouting {
    type nat hook postrouting priority srcnat; policy accept;
    jump nat
  }
  # separate chain so that gw-ctl nat off can flush it (E04)
  chain nat {
    oifname "eth2" masquerade
  }
}
NFT
log "gw-$SETUP: transfer $GW_IP/$TRANSFER_PREFIX, setup gateway $SETUP_GW/$SETUP_PREFIX"
