#!/bin/bash
# Host part of the testbed: gtp5g v0.10.2, free5GC v4.3.0 (free5gc-compose) with
# UERANSIM, 30 subscribers and 30 UEs, and the client image mcp-steer:0.1.
# Run from the repo root: testbed/setup_core.sh [work dir, default ~/mcp_steer]
set -eux
REPO=$PWD
W=${1:-$HOME/mcp_steer}
mkdir -p "$W"
sudo DEBIAN_FRONTEND=noninteractive apt-get install -y -q build-essential git

cd "$W"
[ -d gtp5g ] || git clone https://github.com/free5gc/gtp5g.git
# UPF v4.3.0 accepts gtp5g from 0.9.3 up to (not including) 0.10.3
(cd gtp5g && git fetch --tags -q && git checkout -q v0.10.2 && make -j8 && sudo make install && sudo modprobe gtp5g)

[ -d free5gc-compose ] || git clone https://github.com/free5gc/free5gc-compose.git
cd free5gc-compose
cp "$REPO/core/docker-compose.override.yaml" .   # core and UERANSIM on CPUs 0,1,8,9
cp "$REPO/core/uecfg.yaml" config/uecfg.yaml      # one PDU session, NSSAI 1/010203 only
docker compose pull -q
docker compose up -d
sleep 15
docker restart smf                                # the SMF must start after the UPF
sleep 5
python3 "$REPO/core/add_subscribers.py" 30
docker exec -d ueransim ./nr-ue -c config/uecfg.yaml -n 30
sleep 10
docker exec ueransim ip -br addr | grep -c uesimtun   # expect 30

docker build -t mcp-steer:0.1 -f "$REPO/core/Dockerfile.client" "$REPO/core"
echo CORE_SETUP_DONE
