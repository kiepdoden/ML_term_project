#!/bin/bash
# GPU machine for the agent recording (one g6.xlarge, 1x L4), set in harness/gpu.env:
#   GPU_INSTANCE_ID=i-...   GPU_KEY=~/.ssh/<key>.pem   (gpu.env is not in git)
# Only this machine is started and stopped.
#   harness/gpu_teardown.sh up    start it, start vLLM, open the tunnel on :18000
#   harness/gpu_teardown.sh down  close the tunnel, stop the machine
set -u
[ -f "$(dirname "$0")/gpu.env" ] && . "$(dirname "$0")/gpu.env"
R=${GPU_REGION:-ap-northeast-2}; ID=${GPU_INSTANCE_ID:?set GPU_INSTANCE_ID in harness/gpu.env}; KEY=${GPU_KEY:?set GPU_KEY}
ip() { aws ec2 describe-instances --region $R --instance-ids $ID \
         --query "Reservations[0].Instances[0].PublicIpAddress" --output text; }
case "${1:-}" in
  up)
    aws ec2 start-instances --region $R --instance-ids $ID >/dev/null
    aws ec2 wait instance-running --region $R --instance-ids $ID
    H=$(ip)
    for i in $(seq 1 30); do ssh -o BatchMode=yes -o ConnectTimeout=5 -o StrictHostKeyChecking=accept-new \
        -i $KEY ubuntu@$H true 2>/dev/null && break; sleep 6; done
    ssh -o BatchMode=yes -i $KEY ubuntu@$H "docker start mcp-steer-vllm >/dev/null"
    ssh -f -N -o ServerAliveInterval=30 -o ExitOnForwardFailure=yes \
        -L 127.0.0.1:18000:127.0.0.1:8000 -i $KEY ubuntu@$H
    until curl -s -m3 http://127.0.0.1:18000/v1/models | grep -q qwen3-8b; do sleep 10; done
    echo "GPU up at $H, LLM on 127.0.0.1:18000";;
  down)
    pkill -f "[E]xitOnForwardFailure=yes -L 127.0.0.1:18000" || true
    aws ec2 stop-instances --region $R --instance-ids $ID >/dev/null
    aws ec2 wait instance-stopped --region $R --instance-ids $ID && echo "GPU machine stopped";;
  *) echo "usage: $0 up|down"; exit 1;;
esac
