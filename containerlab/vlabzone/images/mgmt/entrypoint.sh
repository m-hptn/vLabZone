#!/usr/bin/env bash
# hwpool/mgmt – plain L2 bridge for the lab management net 10.198.0.0/24
# (broker .1, lab-edge .2, lab-sw .3). Bridges every linked ethN, has no IP itself.
set -euo pipefail
. /usr/local/lib/vlab/common.sh

# PORTS is set in the topology (one port per node on the management net)
PORTS=${PORTS:-eth1 eth2 eth3}
# shellcheck disable=SC2086
wait_if 60 $PORTS
ip link add br0 type bridge && ip link set br0 up
for p in $PORTS; do
  ip link set "$p" master br0 up
  log "bridged $p"
done
# Bridged traffic must not run through the IP firewall. The sysctl only exists
# if the host has br_netfilter loaded (Docker often loads it) – then it matters.
[[ -e /proc/sys/net/bridge/bridge-nf-call-iptables ]] && sysctl_set net.bridge.bridge-nf-call-iptables 0
mark_ready
idle
