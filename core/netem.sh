#!/bin/bash
# Emulate site distance from the UPF: half of the RTT on each direction.
# MEC-1 same site as the UPF (no added delay), MEC-2 nearby (RTT 10 ms),
# MEC-3 far (RTT 28 ms). Re-run after a VM restart (tap names change).
set -eu
export INCUS_PROJECT=default   # the MEC VMs live in the default project, whatever the shell's current one
declare -A HALF=([1]=0 [2]=5 [3]=14)
for n in 1 2 3; do
  tap=$(incus config get mec$n volatile.eth0.host_name)
  h=${HALF[$n]}
  if [ "$h" = "0" ]; then
    sudo tc qdisc del dev "$tap" root 2>/dev/null || true
    incus exec mec$n -- tc qdisc del dev enp5s0 root 2>/dev/null || true
  else
    sudo tc qdisc replace dev "$tap" root netem delay ${h}ms limit 100000
    incus exec mec$n -- tc qdisc replace dev enp5s0 root netem delay ${h}ms limit 100000
  fi
  echo "mec$n tap=$tap one-way=${h}ms"
done
