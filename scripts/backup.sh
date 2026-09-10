#!/usr/bin/env bash
#
# MongoDB 백업. systemd timer(memo-backup.timer)가 매일 실행한다.
#
# 접속 정보는 앱과 같은 .env 를 읽는다. MongoDB 인증을 켜면 자격 증명이
# MONGO_URI 에 들어가므로 스크립트를 따로 고칠 필요가 없다.
#
# 복구: bash scripts/restore.sh [백업파일]
set -euo pipefail

APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BACKUP_DIR="${BACKUP_DIR:-$HOME/backups}"
KEEP_COUNT="${KEEP_COUNT:-14}"
MIN_FREE_MB="${MIN_FREE_MB:-300}"

# shellcheck disable=SC1091
if [ -f "$APP_DIR/.env" ]; then
    set -a
    . "$APP_DIR/.env"
    set +a
fi
MONGO_URI="${MONGO_URI:-mongodb://localhost:27017}"
DB_NAME="${DB_NAME:-memo}"

# 디스크가 빠듯하면 백업을 시도하지 않는다. 반쯤 쓰다 멈추면 더 나쁘다.
free_mb=$(df --output=avail -m / | tail -1 | tr -dc '0-9')
if [ "$free_mb" -lt "$MIN_FREE_MB" ]; then
    echo "$(date -Is) 백업 중단: 디스크 여유 ${free_mb}MB (최소 ${MIN_FREE_MB}MB)" >&2
    exit 1
fi

mkdir -p "$BACKUP_DIR"
TARGET="$BACKUP_DIR/memo-$(date +%Y%m%d-%H%M).gz"

# URI 에 데이터베이스 경로가 없으면 붙여 준다.
case "$MONGO_URI" in
    */"$DB_NAME"|*/"$DB_NAME"\?*) uri="$MONGO_URI" ;;
    *\?*) uri="${MONGO_URI%%\?*}/$DB_NAME?${MONGO_URI#*\?}" ;;
    */) uri="$MONGO_URI$DB_NAME" ;;
    *) uri="$MONGO_URI/$DB_NAME" ;;
esac

mongodump --quiet --uri="$uri" --gzip --archive="$TARGET"

if [ ! -s "$TARGET" ]; then
    echo "$(date -Is) 백업 실패: $TARGET 이 비어 있습니다" >&2
    rm -f "$TARGET"
    exit 1
fi

# 실제로 복원 가능한 아카이브인지 확인한다. 열리지 않는 파일이 성공으로
# 기록되면 정작 필요할 때 알게 된다.
if ! mongorestore --quiet --uri="$uri" --gzip --archive="$TARGET" --dryRun >/dev/null 2>&1; then
    echo "$(date -Is) 백업 검증 실패: $TARGET 을 복원할 수 없습니다" >&2
    rm -f "$TARGET"
    exit 1
fi

# 날짜가 아니라 개수로 보존한다. 백업이 며칠 실패해도 마지막 성공본이 남는다.
find "$BACKUP_DIR" -maxdepth 1 -type f -name 'memo-*.gz' -printf '%T@\t%p\n' 2>/dev/null \
    | sort -rn | cut -f2- | tail -n "+$((KEEP_COUNT + 1))" | xargs -r rm -f

echo "$(date -Is) 백업 완료: $TARGET ($(du -h "$TARGET" | cut -f1))"
