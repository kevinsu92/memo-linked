#!/usr/bin/env bash
#
# 감시 스크립트와 백업을 systemd timer 로 등록한다.
#
#   DOMAIN=example.com bash scripts/install-watch.sh
set -euo pipefail

DOMAIN="${DOMAIN:-}"
ALERT_WEBHOOK="${ALERT_WEBHOOK:-}"
APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
USER_NAME="$(whoami)"

if [ -z "$DOMAIN" ]; then
    echo "DOMAIN 을 지정하세요. 없으면 외부 점검과 인증서 감시가 꺼집니다." >&2
    echo "  DOMAIN=example.com bash scripts/install-watch.sh" >&2
    exit 1
fi

# 웹훅 주소는 유닛 파일이 아니라 별도 파일에 둔다. 유닛 파일은 world-readable 이다.
sudo install -m 600 /dev/null /etc/memo-watch.env
{
    echo "DOMAIN=${DOMAIN}"
    echo "ALERT_WEBHOOK=${ALERT_WEBHOOK}"
} | sudo tee /etc/memo-watch.env > /dev/null

sudo tee /etc/systemd/system/memo-watch.service > /dev/null <<EOF
[Unit]
Description=메모장 상태 감시

[Service]
Type=oneshot
User=$USER_NAME
EnvironmentFile=/etc/memo-watch.env
ExecStart=$APP_DIR/scripts/watch.sh
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ProtectHome=read-only
ProtectKernelTunables=true
RestrictNamespaces=true
EOF

sudo tee /etc/systemd/system/memo-watch.timer > /dev/null <<'EOF'
[Unit]
Description=메모장 상태 감시 (5분마다)

[Timer]
OnBootSec=3min
OnUnitActiveSec=5min

[Install]
WantedBy=timers.target
EOF

sudo tee /etc/systemd/system/memo-backup.service > /dev/null <<EOF
[Unit]
Description=메모장 MongoDB 백업

[Service]
Type=oneshot
User=$USER_NAME
ExecStart=$APP_DIR/scripts/backup.sh
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ReadWritePaths=$HOME/backups
ProtectKernelTunables=true
RestrictNamespaces=true
OnFailure=memo-watch.service
EOF

sudo tee /etc/systemd/system/memo-backup.timer > /dev/null <<'EOF'
[Unit]
Description=메모장 백업 (매일 03:00)

[Timer]
OnCalendar=*-*-* 03:00:00
Persistent=true

[Install]
WantedBy=timers.target
EOF

chmod +x "$APP_DIR/scripts/"*.sh
sudo systemctl daemon-reload
sudo systemctl enable --now memo-watch.timer memo-backup.timer
systemctl list-timers 'memo-*' --no-pager
