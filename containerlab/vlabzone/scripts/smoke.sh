#!/usr/bin/env bash
# Smoke test: deploy topology/smoke.clab.yml, exercise VLAN + VRF + WireGuard
# between two nodes, then destroy the lab again (also on failure).
set -euo pipefail
cd "$(dirname "$0")/.."

TOPO=topology/smoke.clab.yml
# containerlab is setuid; root and members of clab_admins need no sudo
SUDO=sudo
if [[ $(id -u) -eq 0 ]] || id -nG | tr ' ' '\n' | grep -qx clab_admins; then SUDO=; fi
A=clab-vlab-smoke-a
B=clab-vlab-smoke-b

cleanup() { $SUDO containerlab destroy -t "$TOPO" --cleanup >/dev/null 2>&1 || true; }
trap cleanup EXIT

step() { printf '\n>>> %s\n' "$*"; }
x() { local n=$1; shift; docker exec "$n" sh -c "$*"; }

step "deploy smoke lab"
$SUDO containerlab deploy -t "$TOPO" --reconfigure

step "no management interface (network-mode: none)"
if x $A 'ip -o link show eth0' 2>/dev/null; then
  echo "WARN: eth0 exists in node – network-mode none not honoured, nft fallback needed (idea.md §4.4)"
else
  echo "ok: only lo + eth1"
fi

step "underlay on untagged eth1 (default VRF), like corp-fw <-> lab-edge"
x $A "ip link set eth1 up && ip addr add 10.20.99.1/30 dev eth1"
x $B "ip link set eth1 up && ip addr add 10.20.99.2/30 dev eth1"
x $A "ping -c 2 -W 2 10.20.99.2"

step "VLAN 112 subinterfaces inside VRF vrf-s12 on both nodes"
for n in $A $B; do
  idx=$([[ $n == "$A" ]] && echo 1 || echo 2)
  x $n "ip link add vrf-s12 type vrf table 1012 && ip link set vrf-s12 up \
     && ip link add link eth1 name eth1.112 type vlan id 112 \
     && ip link set eth1.112 master vrf-s12 up \
     && ip addr add 192.168.1.$idx/24 dev eth1.112"
done
x $A "ip vrf exec vrf-s12 ping -c 2 -W 2 192.168.1.2"

step "WireGuard interface in vrf-s12, UDP socket in default VRF (edge design)"
KA=$(x $A 'wg genkey'); KB=$(x $B 'wg genkey')
PA=$(echo "$KA" | docker exec -i $A wg pubkey); PB=$(echo "$KB" | docker exec -i $B wg pubkey)
docker exec -i $A sh -c 'cat > /tmp/k' <<<"$KA"
docker exec -i $B sh -c 'cat > /tmp/k' <<<"$KB"
x $A "ip link add wg-s12 type wireguard && ip link set wg-s12 master vrf-s12 \
   && wg set wg-s12 private-key /tmp/k listen-port 51812 peer $PB allowed-ips 10.250.0.2/32 endpoint 10.20.99.2:51812 \
   && ip addr add 10.250.0.1/24 dev wg-s12 && ip link set wg-s12 up"
x $B "ip link add wg-s12 type wireguard && ip link set wg-s12 master vrf-s12 \
   && wg set wg-s12 private-key /tmp/k listen-port 51812 peer $PA allowed-ips 10.250.0.1/32 endpoint 10.20.99.1:51812 \
   && ip addr add 10.250.0.2/24 dev wg-s12 && ip link set wg-s12 up"
x $A "ip vrf exec vrf-s12 ping -c 2 -W 2 10.250.0.2"
HS=$(x $A "wg show wg-s12 latest-handshakes" | awk '{print $2}')
[[ "${HS:-0}" != 0 ]] || { echo "FAIL: no WireGuard handshake"; exit 1; }
echo "ok: WireGuard handshake completed"

printf '\nSMOKE TEST PASSED\n'
