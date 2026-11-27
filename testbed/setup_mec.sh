#!/bin/bash
# The three MEC sites as incus VMs (project "default"): 4 vCPU = 2 physical cores,
# 16 GiB, own bridge mecNbr 10.201.N.0/24, replica at 10.201.N.10. Then installs the
# MCP SQL and KV replicas in each VM (mec/setup_vm.sh, 2 SQL workers).
# Run from the repo root: testbed/setup_mec.sh
set -eux
export INCUS_PROJECT=default
declare -A CPUS=([1]="2,3,10,11" [2]="4,5,12,13" [3]="6,7,14,15")
for n in 1 2 3; do
  incus network show mec${n}br >/dev/null 2>&1 ||
    incus network create mec${n}br ipv4.address=10.201.${n}.1/24 ipv4.nat=true ipv6.address=none
  if ! incus info mec${n} >/dev/null 2>&1; then
    incus init images:ubuntu/24.04/cloud mec${n} --vm -c limits.cpu=${CPUS[$n]} \
      -c limits.memory=16GiB -d root,size=30GiB -n mec${n}br
    incus config device override mec${n} eth0 ipv4.address=10.201.${n}.10
  fi
  incus start mec${n} 2>/dev/null || true
done
for n in 1 2 3; do
  for i in $(seq 1 60); do incus exec mec$n -- true 2>/dev/null && break; sleep 5; done
  incus exec mec$n -- mkdir -p /opt/mcpsite
  for f in mec/*.py mec/setup_vm.sh; do incus file push "$f" mec$n/opt/mcpsite/; done
  incus exec mec$n -- bash /opt/mcpsite/setup_vm.sh mec$n 10.201.$n.1:9999 2 > setup_vm_mec$n.log 2>&1 &
done
wait
grep -h VM_SETUP_DONE setup_vm_mec*.log
echo MEC_SETUP_DONE
