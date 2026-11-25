#!/bin/bash
# Runs inside one MEC VM as root. Usage: setup_vm.sh <site> <controller host:port> <sql workers>
set -euxo pipefail
SITE=${1:?site}; CTRL=${2:?controller}; WORKERS=${3:-2}
export DEBIAN_FRONTEND=noninteractive
apt-get update -q
apt-get install -y -q python3-venv iproute2 jq libgomp1
[ -x /opt/mcp/bin/python ] || python3 -m venv /opt/mcp
/opt/mcp/bin/pip install -q duckdb==1.4.1 "mcp[cli]==1.20.0" "pydantic==2.11.9" uvicorn numpy pandas scikit-learn lightgbm
mkdir -p /data /var/log/mcp
/opt/mcp/bin/python /opt/mcpsite/gen_data.py --out /data --workers "$WORKERS"

cat > /etc/systemd/system/mcp-sql.service <<EOF
[Unit]
Description=MCP SQL replica ($SITE)
After=network-online.target
[Service]
WorkingDirectory=/opt/mcpsite
Environment=SITE=$SITE CONTROLLER=$CTRL WORKERS=$WORKERS
ExecStart=/opt/mcp/bin/python /opt/mcpsite/sql_server.py
Restart=always
[Install]
WantedBy=multi-user.target
EOF
cat > /etc/systemd/system/mcp-kv.service <<EOF
[Unit]
Description=MCP KV replica ($SITE)
After=network-online.target
[Service]
WorkingDirectory=/opt/mcpsite
Environment=SITE=$SITE CONTROLLER=$CTRL
ExecStart=/opt/mcp/bin/python /opt/mcpsite/kv_server.py
Restart=always
[Install]
WantedBy=multi-user.target
EOF
systemctl daemon-reload
systemctl enable --now mcp-sql mcp-kv
sleep 3
systemctl --no-pager --lines=5 status mcp-sql mcp-kv | head -30
echo VM_SETUP_DONE
