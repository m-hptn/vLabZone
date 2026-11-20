# Alias mode (idea.md §5.6, E13) – in every edge mode.
#
# Setup N is also reachable as 10.200.N.0/24: the edge maps 10.200.N.x -> 192.168.1.x
# (DNAT, whole prefix) for traffic from setup N's own tunnels (10.250.N.0/24). A CI
# workspace can then hold contexts to several setups with the same network at the same
# time, in one namespace, without overlapping routes. Replies are mapped back by conntrack.
#
# Only a setup's own tunnel prefix is translated: 10.250.12.x -> 10.200.7.y stays
# untranslated and has no route, so the alias addresses don't open a path between setups.
# Known limit: protocols that carry IP addresses in the payload (active FTP, SIP) see the
# real 192.168.1.x addresses.
# shellcheck shell=bash

nft -f - <<NFT
table ip alias {
  chain prerouting {
    type nat hook prerouting priority dstnat; policy accept;
  }
}
NFT
while read -r id _vlan net _gw; do
  alias=$(vlab-cfg alias-prefix "$id")
  nft add rule ip alias prerouting ip saddr "$(vlab-cfg setup-prefix "$id")" ip daddr "$alias" \
    dnat ip prefix to "$net" comment "\"setup $id alias\""
  log "setup $id: alias $alias -> $net"
done < <(vlab-cfg setups)
