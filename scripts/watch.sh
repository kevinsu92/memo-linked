#!/usr/bin/env bash
#
# 상태 감시. systemd timer 로 5분마다 돌린다.
# 문제를 찾으면 journald 에 error 로 남기고, ALERT_WEBHOOK 이 있으면 알림도 보낸다.
#
#   설치: scripts/install-watch.sh
#   확인: journalctl -t memo-watch -p err -n 20
set -uo pipefail

DOMAIN="${DOMAIN:-}"
DISK_LIMIT="${DISK_LIMIT:-85}"
CERT_DAYS_LIMIT="${CERT_DAYS_LIMIT:-20}"
WEBHOOK="${ALERT_WEBHOOK:-}"

problems=()

note() {
    problems+=("$1")
    logger -t memo-watch -p user.err "$1"
}

# 1) 앱과 DB
if ! curl -fsS --max-time 5 http://127.0.0.1:5000/healthz | grep -q '"db":"up"'; then
    note "헬스 체크 실패: 앱 또는 MongoDB 이상"
fi

# 2) 서비스 상태
for unit in memo nginx mongod; do
    systemctl is-active --quiet "$unit" || note "서비스 정지: $unit"
done

# 3) 디스크
used=$(df --output=pcent / | tail -1 | tr -dc '0-9')
if [ -n "$used" ] && [ "$used" -ge "$DISK_LIMIT" ]; then
    note "디스크 사용률 ${used}% (기준 ${DISK_LIMIT}%)"
fi

# 4) 가용 메모리
avail=$(free -m | awk '/^Mem:/ {print $7}')
if [ -n "$avail" ] && [ "$avail" -lt 80 ]; then
    note "가용 메모리 ${avail}MB"
fi

# 5) 외부에서 본 사이트 상태 (TLS, nginx, 보안 그룹까지 한 번에 확인)
if [ -z "$DOMAIN" ]; then
    note "DOMAIN 이 지정되지 않아 외부 점검과 인증서 점검을 못 합니다"
else
    if ! curl -fsS --max-time 10 -o /dev/null "https://$DOMAIN/"; then
        note "외부에서 https://$DOMAIN 접속 실패"
    fi

    # 인증서는 파일 대신 TLS 연결에서 읽는다.
    # /etc/letsencrypt/live 는 root 전용이라 일반 사용자가 못 읽고, 무엇보다
    # 이 방식이라야 nginx 가 '실제로 내보내는' 인증서를 확인할 수 있다.
    end=$(echo | openssl s_client -connect "127.0.0.1:443" -servername "$DOMAIN" 2>/dev/null         | openssl x509 -enddate -noout 2>/dev/null | cut -d= -f2)
    if [ -z "$end" ]; then
        note "인증서 만료일을 확인하지 못했습니다"
    else
        left=$(( ( $(date -d "$end" +%s) - $(date +%s) ) / 86400 ))
        [ "$left" -gt "$CERT_DAYS_LIMIT" ] || note "인증서 만료까지 ${left}일"
    fi

    # 자동 갱신 타이머가 살아 있는지도 본다.
    systemctl is-active --quiet certbot.timer || note "certbot.timer 가 꺼져 있습니다"
fi

# 6) 최근 백업
newest=$(find "$HOME/backups" -name 'memo-*.gz' -mtime -2 2>/dev/null | head -1)
[ -n "$newest" ] || note "최근 2일 내 백업이 없습니다"

if [ ${#problems[@]} -eq 0 ]; then
    logger -t memo-watch -p user.info "정상"
    exit 0
fi

if [ -n "$WEBHOOK" ]; then
    text=$(printf '메모장 서버 점검 알림:\n%s\n' "$(printf '  - %s\n' "${problems[@]}")")
    curl -fsS -m 10 -X POST -H 'Content-Type: application/json' \
        -d "$(printf '{"text": %s}' "$(printf '%s' "$text" | python3 -c 'import json,sys; print(json.dumps(sys.stdin.read()))')")" \
        "$WEBHOOK" >/dev/null 2>&1 || logger -t memo-watch -p user.err "웹훅 전송 실패"
fi

exit 1
