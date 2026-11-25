#!/bin/bash
# Install the steering maps in the UPF's network namespace.
# Each MCP server has one anycast address. Every NEW connection draws a random
# bucket 0-99 and the split map sends that bucket to one replica (conntrack
# keeps an open connection on its replica). The controller rewrites the maps.
#   SQL  10.200.0.10:8000  -> 10.201.<site>.10:8000
#   KV   10.200.0.11:8001  -> 10.201.<site>.10:8001
set -eu
PID=$(docker inspect -f '{{.State.Pid}}' upf)
sudo nsenter -t "$PID" -n nft -f - <<'EOF'
table ip steer
delete table ip steer
table ip steer {
  map sql_split {
    type mark : ipv4_addr
    flags interval
    elements = { 0-99 : 10.201.1.10 }
  }
  map kv_split {
    type mark : ipv4_addr
    flags interval
    elements = { 0-99 : 10.201.1.10 }
  }
  chain pre {
    type nat hook prerouting priority dstnat; policy accept;
    ip daddr 10.200.0.10 tcp dport 8000 counter dnat to numgen random mod 100 map @sql_split
    ip daddr 10.200.0.11 tcp dport 8001 counter dnat to numgen random mod 100 map @kv_split
  }
}
EOF
# never let an unmapped anycast packet leave the host
ip route show | grep -q '^blackhole 10.200.0.0/24' || sudo ip route add blackhole 10.200.0.0/24
sudo nsenter -t "$PID" -n nft list table ip steer
