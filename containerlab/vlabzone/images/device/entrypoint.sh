#!/usr/bin/env bash
# hwpool/device – simulated lab device (also used for corp-svc).
#
# Env: NAME (dev-12-20), SETUP (12 | corp), IP (192.168.1.20/24), GW (192.168.1.1)
# Every device has a unique identity so tests can tell where a packet ended up:
#   HTTP :80  -> {"setup", "device", "ip", "peer"}
#   SSH  :22  -> banner + prompt with setup number (user lab / password lab)
#   syslog    -> /var/log/auth.log shows the source IP of every SSH login
#   /etc/device.conf contains the device's own IP; iperf3 server on :5201
set -euo pipefail
. /usr/local/lib/vlab/common.sh

NAME=${NAME:?} SETUP=${SETUP:?} IP=${IP:?} GW=${GW:-}
ADDR=${IP%/*}
log "hwpool/device ${VLAB_VERSION:-dev} $NAME setup=$SETUP ip=$IP gw=${GW:-none}"

wait_if 60 eth1
ip addr add "$IP" dev eth1
ip link set eth1 up
# On purpose the setup devices get a gateway that does not exist (192.168.1.1, E04)
[[ -n "$GW" ]] && ip route add default via "$GW"

if [[ "$SETUP" =~ ^[0-9]+$ ]]; then label="setup$SETUP"; else label="$SETUP"; fi
hostname "$label-${NAME##*-}" 2>/dev/null || true
tag="[$label] $NAME ($ADDR)"

cat > /etc/device.conf <<CONF
# device configuration ($NAME)
name=$NAME
setup=$SETUP
ip=$ADDR
CONF
printf 'Setup %s - device %s - %s\n' "$SETUP" "$NAME" "$ADDR" > /etc/issue.net
echo "PS1='$tag \\w\\$ '" > /etc/profile.d/vlab-prompt.sh

rsyslogd
ssh-keygen -A >/dev/null
mkdir -p /run/sshd
/usr/sbin/sshd
iperf3 -s -D >/dev/null

export NAME SETUP ADDR
python3 /usr/local/bin/ident-http.py &
http_pid=$!
# Only report ready once the services actually listen (tests start right after `make up`)
deadline=$((SECONDS + 30))
until ss -ltnH | grep -q ':80 ' && ss -ltnH | grep -q ':22 '; do
  (( SECONDS < deadline )) || die "HTTP/SSH did not start listening"
  kill -0 "$http_pid" 2>/dev/null || die "ident-http exited"
  sleep 0.1
done
mark_ready
wait "$http_pid"
