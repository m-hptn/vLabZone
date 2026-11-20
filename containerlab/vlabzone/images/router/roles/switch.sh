# lab-agg / lab-sysN: VLAN switch (ROLE=switch, SWITCH=agg|sys1|…).
#
# What each port does comes from `vlab-cfg switch-config $SWITCH` (inventory + grouping
# in setups.yaml, edge mode):
#   mgmt   eth2: management address, NOT part of the bridge
#   trunk  tagged VLANs – only those needed behind that port
#   access untagged access port of a device in its setup's VLAN, or in no VLAN (ungrouped)
# shellcheck shell=bash

SWITCH=${SWITCH:?SWITCH must be set (agg, sys1, …)}
EDGE_MODE=${EDGE_MODE:-vrf}; export EDGE_MODE
mapfile -t CONF < <(vlab-cfg switch-config "$SWITCH")
# shellcheck disable=SC2046
wait_if 60 $(printf '%s\n' "${CONF[@]}" | awk '{print $2}')

ip link add br0 type bridge vlan_filtering 1 vlan_default_pvid 0
ip link set br0 up
# The bridge itself takes part in no VLAN – the switch has no IP in any setup
bridge vlan del dev br0 vid 1 self 2>/dev/null || true

for line in "${CONF[@]}"; do
  read -r kind port rest <<<"$line"
  case "$kind" in
    mgmt)
      ip addr add "$rest" dev "$port" && ip link set "$port" up
      log "mgmt $port $rest" ;;
    trunk)
      ip link set "$port" master br0 up
      bridge vlan del dev "$port" vid 1 2>/dev/null || true
      for vlan in $rest; do bridge vlan add dev "$port" vid "$vlan"; done
      log "trunk $port: VLANs ${rest:-none}" ;;
    access)
      read -r vlan dev <<<"$rest"
      ip link set "$port" master br0 up
      sw-port "$port" "$vlan" >/dev/null
      log "access $port -> VLAN $vlan ($dev)" ;;
  esac
done
