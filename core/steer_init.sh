#!/bin/bash
# Install the steering map in the UPF's network namespace.
# Each MCP server has one anycast address; the map sends every NEW connection
# to one replica (conntrack keeps an open connection on its replica).
#   SQL  10.200.0.10:8000  -> 10.201.<site>.10:8000
#   KV   10.200.0.11:8001  -> 10.201.<site>.10:8001
set -eu
PID=$(docker inspect -f '{{.State.Pid}}' upf)
sudo nsenter -t "$PID" -n nft -f - <<'EOF'
table ip steer
delete table ip steer
table ip steer {
  map svc {
    type ipv4_addr . inet_service : ipv4_addr . inet_service
    elements = { 10.200.0.10 . 8000 : 10.201.1.10 . 8000,
                 10.200.0.11 . 8001 : 10.201.1.10 . 8001 }
  }
  chain pre {
    type nat hook prerouting priority dstnat; policy accept;
    ip daddr 10.200.0.0/24 counter dnat ip addr . port to ip daddr . tcp dport map @svc
  }
}
EOF
# never let an unmapped anycast packet leave the host
ip route show | grep -q '^blackhole 10.200.0.0/24' || sudo ip route add blackhole 10.200.0.0/24
sudo nsenter -t "$PID" -n nft list table ip steer
