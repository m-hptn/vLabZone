#!/usr/bin/env bash
# hwpool/serial – stand-in for the Moxa NPort 5600 serial device server (docs/consoles-usb.md).
#
# Env: NAME (nport1). Address, mode and cabling come from setups.yaml (appliances.serial).
#   eth1  access port on lab-agg, appliance VLAN (10.197.0.0/24), route to the tunnels via the edge
# nport-sim.py: serial port K on TCP base_port+K in RFC 2217 mode, the console of the device cabled
# to K behind it, ONE session per port at a time – like the NPort.
set -euo pipefail
. /usr/local/lib/vlab/common.sh

NAME=${NAME:?}
read -r _vlan APP_NET EDGE_IP < <(vlab-cfg appliances)
IP=$(vlab-cfg appliance "$NAME" ip)
log "hwpool/serial ${VLAB_VERSION:-dev} $NAME ip=$IP"

wait_if 60 eth1
ip addr add "$IP/${APP_NET#*/}" dev eth1
ip link set eth1 up
# the clients are the tunnels; the edge routes them (no NAT – the NPort sees 10.250.<setup>.<user>)
ip route add "$(vlab-cfg get edge.tunnel_net)" via "$EDGE_IP"

export NPORT_NAME=$NAME
python3 /usr/local/bin/nport-sim.py >> /var/log/nport.log 2>&1 &
last=$(vlab-cfg serial-server "$NAME" | tail -1 | awk '{print $2}')
deadline=$((SECONDS + 15))
until ss -ltnH | grep -q ":$last "; do
  (( SECONDS < deadline )) || die "nport-sim did not start"
  sleep 0.2
done
mark_ready
idle
