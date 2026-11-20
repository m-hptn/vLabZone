#!/usr/bin/env bash
# wait-ready.sh <lab-name> [timeout] – wait until every node of a deployed lab has
# finished its entrypoint (/run/vlab-ready). Prints the log of nodes that fail.
set -euo pipefail
LAB=${1:?usage: wait-ready.sh <lab> [timeout]}
TIMEOUT=${2:-120}
deadline=$((SECONDS + TIMEOUT))
# A RouterOS edge (kind mikrotik_ros) has no /run/vlab-ready: it is configured over REST
# afterwards (drivers/edge_routeros.py configure), which waits for its API itself.
mapfile -t nodes < <(docker ps --filter "label=containerlab=$LAB" --format '{{.Names}} {{.Label "clab-node-kind"}}' \
                     | awk '$2 != "mikrotik_ros" {print $1}' | sort)
(( ${#nodes[@]} )) || { echo "no running nodes for lab $LAB" >&2; exit 1; }

pending=("${nodes[@]}")
while (( ${#pending[@]} )); do
  still=()
  for n in "${pending[@]}"; do
    if ! docker inspect -f '{{.State.Running}}' "$n" 2>/dev/null | grep -q true; then
      echo "node $n is not running:" >&2; docker logs --tail 20 "$n" >&2; exit 1
    fi
    docker exec "$n" test -f /run/vlab-ready 2>/dev/null || still+=("$n")
  done
  pending=("${still[@]+"${still[@]}"}")
  (( ${#pending[@]} )) || break
  if (( SECONDS >= deadline )); then
    for n in "${pending[@]}"; do echo "--- $n not ready:" >&2; docker logs --tail 20 "$n" >&2; done
    exit 1
  fi
  sleep 1
done
echo "all ${#nodes[@]} nodes of lab $LAB ready"
