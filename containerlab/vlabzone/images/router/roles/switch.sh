# lab-sw: VLAN switch. Linux bridge with vlan_filtering, trunk to the edge,
# one access port per device (from setups.yaml), management on eth2.
#
#   eth1   trunk to lab-edge (VRF: all setup VLANs, PBR: transfer VLAN 199)
#   eth2   lab management 10.198.0.3/24 (not part of the bridge)
#   ethNN  access ports for devices (and in PBR mode for gw-N), from setups.yaml
# shellcheck shell=bash

EDGE_MODE=${EDGE_MODE:-vrf}; export EDGE_MODE
wait_if 60 eth1 eth2

ip addr add 10.198.0.3/24 dev eth2 && ip link set eth2 up

ip link add br0 type bridge vlan_filtering 1 vlan_default_pvid 0
ip link set br0 up
# The bridge itself takes part in no VLAN – the switch has no IP in the setups
bridge vlan del dev br0 vid 1 self 2>/dev/null || true

ip link set eth1 master br0 up
bridge vlan del dev eth1 vid 1 2>/dev/null || true
# VRF mode: all setup VLANs (the edge is the gateway in each setup).
# PBR mode: only the transfer VLAN (the gateways gw-N sit in the setup VLANs).
for vlan in $(vlab-cfg trunk-vlans); do
  bridge vlan add dev eth1 vid "$vlan"
done
log "trunk eth1 ($EDGE_MODE mode): VLANs $(vlab-cfg trunk-vlans)"

while read -r port vlan device; do
  if has_if "$port" 10; then
    ip link set "$port" master br0 up
    sw-port "$port" "$vlan"
    log "access port $port -> VLAN $vlan ($device)"
  else
    log "WARN: port $port for $device not linked – skipped"
  fi
done < <(vlab-cfg ports)
