#!/usr/bin/env bash
# hwpool/edge – Linux lab edge.
#
#   eth1  corporate side 10.20.99.2/30 (only WireGuard UDP arrives here)
#   eth2  trunk to lab-sw (one VLAN subinterface per setup)
#   eth3  lab management 10.198.0.2/24
#
# EDGE_MODE=vrf (stage 1) | pbr | hybrid (stage 2). Peers are NOT created here –
# only `edge-ctl` (test fixtures now, the broker from stage 3) adds them.
set -euo pipefail
. /usr/local/lib/vlab/common.sh

EDGE_MODE=${EDGE_MODE:-vrf}
log "hwpool/edge ${VLAB_VERSION:-dev} mode=$EDGE_MODE"
wait_if 60 eth1 eth2 eth3

ip addr add 10.20.99.2/30 dev eth1 && ip link set eth1 up
# Only the corporate networks are reachable – no default route, no internet
ip route add 10.20.0.0/16 via 10.20.99.1
ip addr add 10.198.0.2/24 dev eth3 && ip link set eth3 up
ip link set eth2 up

sysctl_set net.ipv4.ip_forward 1
# net.ipv4.{tcp,udp}_l3mdev_accept stay 0 on purpose: the WireGuard sockets receive on eth1
# (default VRF) and need nothing from the VRFs. With 1, sockets of the edge itself would also
# accept traffic arriving from inside a setup (see docs/lab-vrf.md).

mkdir -p /run/vlab/keys
case "$EDGE_MODE" in
  vrf) . /usr/local/lib/vlab/edge-vrf.sh ;;
  pbr) . /usr/local/lib/vlab/edge-pbr.sh ;;
  hybrid) . /usr/local/lib/vlab/edge-hybrid.sh ;;
  *)   die "unknown EDGE_MODE $EDGE_MODE" ;;
esac

echo "$EDGE_MODE" > /run/vlab/edge-mode
mark_ready
idle
