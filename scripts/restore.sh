#!/usr/bin/env bash
#
# MongoDB 복구. 가장 최근 백업 또는 지정한 파일을 되돌린다.
#
#   bash scripts/restore.sh                      # 최신 백업
#   bash scripts/restore.sh ~/backups/memo-x.gz  # 특정 백업
#   FORCE=1 bash scripts/restore.sh              # 확인 없이 진행 (자동화용)
#
# 주의: 대상 DB 의 기존 내용을 지우고 덮어쓴다.
set -euo pipefail

APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BACKUP_DIR="${BACKUP_DIR:-$HOME/backups}"
ARCHIVE="${1:-}"

# shellcheck disable=SC1091
if [ -f "$APP_DIR/.env" ]; then
    set -a
    . "$APP_DIR/.env"
    set +a
fi
MONGO_URI="${MONGO_URI:-mongodb://localhost:27017}"
DB_NAME="${DB_NAME:-memo}"

if [ -z "$ARCHIVE" ]; then
    ARCHIVE="$(find "$BACKUP_DIR" -name 'memo-*.gz' -printf '%T@ %p\n' 2>/dev/null \
        | sort -rn | head -1 | cut -d' ' -f2-)"
fi

if [ -z "$ARCHIVE" ] || [ ! -f "$ARCHIVE" ]; then
    echo "복구할 백업 파일을 찾을 수 없습니다: ${ARCHIVE:-$BACKUP_DIR}" >&2
    exit 1
fi

echo "복구 대상: $ARCHIVE"
echo "대상 DB  : $DB_NAME"

if [ "${FORCE:-0}" != "1" ]; then
    read -r -p "기존 데이터를 덮어씁니다. 계속할까요? [y/N] " answer
    case "$answer" in
        [yY]) ;;
        *) echo "취소했습니다."; exit 0 ;;
    esac
fi

case "$MONGO_URI" in
    */"$DB_NAME"|*/"$DB_NAME"\?*) uri="$MONGO_URI" ;;
    *\?*) uri="${MONGO_URI%%\?*}/$DB_NAME?${MONGO_URI#*\?}" ;;
    */) uri="$MONGO_URI$DB_NAME" ;;
    *) uri="$MONGO_URI/$DB_NAME" ;;
esac

# 복구 중 쓰기가 섞이지 않도록 앱을 잠시 멈춘다.
app_was_running=0
if systemctl is-active --quiet memo 2>/dev/null; then
    app_was_running=1
    echo "앱 정지"
    sudo systemctl stop memo
fi

# 잘못된 아카이브를 골랐을 때 되돌아갈 수 있도록 현재 상태를 먼저 백업한다.
echo "현재 상태 선백업"
bash "$APP_DIR/scripts/backup.sh" || echo "선백업에 실패했지만 복구를 계속합니다." >&2

mongorestore --quiet --uri="$uri" --drop --gzip --archive="$ARCHIVE"

if [ "$app_was_running" = "1" ]; then
    echo "앱 기동"
    sudo systemctl start memo
    sleep 4
    if curl -fsS --max-time 5 http://127.0.0.1:5000/healthz | grep -q '"db":"up"'; then
        echo "헬스 체크 통과."
    else
        echo "복구는 끝났지만 헬스 체크가 실패합니다. 로그를 확인하세요." >&2
        exit 1
    fi
fi

echo "복구 완료."
