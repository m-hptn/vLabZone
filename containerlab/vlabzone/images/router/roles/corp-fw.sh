# corp-fw: simulated corporate network. Routes between the workspace net, the
# corporate services and the lab, and enforces ONLY the one-time allowance
# (config/corp-fw.nft): WireGuard UDP to the lab edge, TCP 443/8443 to the broker.
# The WireGuard ports depend on the edge mode: VRF needs one port per setup
# (51800-52099), PBR and hybrid a single port (51820).
#
#   eth1-eth3  workspaces (bridged: 10.20.10.0/24)
#   eth4       corporate services 10.20.20.0/24
#   eth10      transfer to lab-edge 10.20.99.0/30
#   eth11      transfer to broker   10.20.98.0/30
# shellcheck shell=bash

wait_if 60 eth1 eth2 eth3 eth4 eth10 eth11

ip link add br-ws type bridge
for p in eth1 eth2 eth3; do ip link set "$p" master br-ws up; done
ip link set br-ws up
ip addr add 10.20.10.1/24 dev br-ws

ip addr add 10.20.20.1/24 dev eth4 && ip link set eth4 up
ip addr add 10.20.99.1/30 dev eth10 && ip link set eth10 up
ip addr add 10.20.98.1/30 dev eth11 && ip link set eth11 up

sysctl_set net.ipv4.ip_forward 1
# Bridged traffic must not run through the IP firewall. The sysctl only exists
# if the host has br_netfilter loaded (Docker often loads it) – then it matters.
[[ -e /proc/sys/net/bridge/bridge-nf-call-iptables ]] && sysctl_set net.bridge.bridge-nf-call-iptables 0

case "${EDGE_MODE:-vrf}" in
  vrf) WG_PORTS="$(vlab-cfg get edge.wg_port_base)-$(( $(vlab-cfg get edge.wg_port_base) + 299 ))" ;;
  *)   WG_PORTS=$(vlab-cfg get edge.wg_port_pbr) ;;
esac
{ echo "define WG_PORTS = $WG_PORTS"; cat "$VLAB_CONFIG_DIR/corp-fw.nft"; } | nft -f -
log "corporate allowance loaded from corp-fw.nft (WireGuard UDP $WG_PORTS)"
