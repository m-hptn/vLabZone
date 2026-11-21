#!/usr/bin/env bash
# hwpool/usbhub – stand-in for the Digi AnywhereUSB 24 Plus (docs/consoles-usb.md).
#
# Env: NAME (ausb1). Address, TCP port and cabling come from setups.yaml (appliances.usb).
#   eth1  access port on lab-agg, appliance VLAN (10.197.0.0/24), route to the tunnels via the edge
# No management interface; groups are changed with hub-ctl (docker exec) [sim] – the real hub is
# managed over its own API.
set -euo pipefail
. /usr/local/lib/vlab/common.sh

NAME=${NAME:?}
read -r _vlan APP_NET EDGE_IP < <(vlab-cfg appliances)
IP=$(vlab-cfg appliance "$NAME" ip) TCP_PORT=$(vlab-cfg appliance "$NAME" tcp_port)
log "hwpool/usbhub ${VLAB_VERSION:-dev} $NAME ip=$IP port=$TCP_PORT"

wait_if 60 eth1
ip addr add "$IP/${APP_NET#*/}" dev eth1
ip link set eth1 up
# the clients are the tunnels; the edge routes them (no NAT – the hub sees 10.250.<setup>.<user>)
ip route add "$(vlab-cfg get edge.tunnel_net)" via "$EDGE_IP"

export HUB_NAME=$NAME HUB_TCP_PORT=$TCP_PORT
hub-ctl init
python3 /usr/local/bin/usbhub-sim.py >> /var/log/usbhub.log 2>&1 &
deadline=$((SECONDS + 15))
until ss -ltnH | grep -q ":$TCP_PORT "; do
  (( SECONDS < deadline )) || die "usbhub-sim did not start"
  sleep 0.2
done
mark_ready
idle
