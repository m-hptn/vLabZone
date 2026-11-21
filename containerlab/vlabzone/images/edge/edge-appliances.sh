# Shared access appliances (docs/consoles-usb.md) – in every edge mode.
#
#   eth2.197   appliance VLAN 10.197.0.0/24 (edge .1): serial device server nport1 (Moxa NPort 5600)
#              and USB hub ausb1 (Digi AnywhereUSB 24 Plus), shared by the devices of ALL setups
#
# No NAT towards the appliances: they see the tunnel address 10.250.<setup>.<user>, so the setup is
# visible in the source address (docs/tunnel-addresses.md).
#
# Who may reach what (nft chain `appliances`, one set per setup):
#   serial  network-enforced: the NPort has one TCP port per serial port. Set console_sN holds
#           <server ip> . <tcp port> of setup N's devices; only 10.250.N.0/24 may reach them.
#           Regrouping a device changes the set (edge-ctl console allow|deny).
#   USB     appliance-enforced: the AnywhereUSB has ONE TCP port for all hub ports. Every setup may
#           reach it; which hub ports a client gets is decided by the hub's groups (hub-ctl).
#
# Routing per mode (the appliance VLAN is in the default VRF):
#   vrf     route leak 10.197.0.0/24 dev eth2.197 into every setup table, and the setup's tunnel
#           prefix 10.250.N.0/24 dev wg-sN in main for the replies (possible because every setup
#           has its own /24 – with 10.250.0.0/16 on every wg-sN the main table couldn't choose)
#   hybrid  route leak into every setup table; replies via 10.250.0.0/16 dev wg0 (main)
#   pbr     ip rule to 10.197.0.0/24 iif wg0 lookup main, before the booking rules
# shellcheck shell=bash

read -r APP_VLAN APP_NET APP_IP < <(vlab-cfg appliances)
APP_VIF=eth2.$APP_VLAN
ip link add link eth2 name "$APP_VIF" type vlan id "$APP_VLAN"
ip addr add "$APP_IP/${APP_NET#*/}" dev "$APP_VIF"
ip link set "$APP_VIF" up

# tunnel side of a setup, as nft sees it in the forward hook
tunnel_match() {
  if [[ $EDGE_MODE == vrf ]]; then echo "meta sdifname \"wg-s$1\""; else echo "iifname \"wg0\""; fi
}

nft -f - <<NFT
table inet edge {
  chain appliances { }
  set usb_hubs { type ipv4_addr . inet_service; }
}
NFT
# before the per-mode rules; the chain only accepts, everything else continues as before
nft insert rule inet edge forward jump appliances

while read -r _n hub_ip port; do
  nft add element inet edge usb_hubs "{ $hub_ip . $port }"
done < <(vlab-cfg usb-hubs)

if [[ $EDGE_MODE == pbr ]]; then
  ip rule add to "$APP_NET" iif wg0 lookup main pref 9000
fi

while read -r id _vlan _net _gw; do
  prefix_n=$(vlab-cfg setup-prefix "$id")
  match=$(tunnel_match "$id")
  case "$EDGE_MODE" in
    vrf)    ip route add "$APP_NET" dev "$APP_VIF" table $((1000 + id))
            ip route add "$prefix_n" dev "wg-s$id" ;;
    hybrid) ip route add "$APP_NET" dev "$APP_VIF" table $((1000 + id)) ;;
  esac
  nft add set inet edge "console_s$id" "{ type ipv4_addr . inet_service; }"
  elems=$(vlab-cfg consoles "$id" | awk '{printf "%s%s . %s", (NR>1 ? ", " : ""), $3, $4}')
  [[ -n "$elems" ]] && nft add element inet edge "console_s$id" "{ $elems }"
  nft add rule inet edge appliances "$match" oifname "$APP_VIF" ip saddr "$prefix_n" \
    ip daddr . tcp dport "@console_s$id" accept comment "\"setup $id consoles\""
  nft add rule inet edge appliances "$match" oifname "$APP_VIF" ip saddr "$prefix_n" \
    ip daddr . tcp dport @usb_hubs accept comment "\"setup $id usb hubs\""
  log "setup $id: consoles ${elems:-none}"
done < <(vlab-cfg setups)
log "appliances on $APP_VIF $APP_IP ($APP_NET)"
