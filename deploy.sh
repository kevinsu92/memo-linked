#!/usr/bin/env bash
#
# EC2(Ubuntu) 배포 스크립트.
#
#   인터넷 -> nginx(80/443) -> gunicorn(127.0.0.1:5000) -> MongoDB(127.0.0.1:27017)
#
# 사용법 (서버에 접속한 뒤 저장소 루트에서):
#   DOMAIN=example.com bash deploy.sh
#
# 환경 변수
#   DOMAIN      인증서를 발급할 도메인. 필수.
#   SKIP_APT=1  패키지 설치를 건너뛴다 (재배포 시 빠름).
#   NO_ROLLBACK=1  헬스 체크 실패 시 자동 롤백을 하지 않는다.
set -euo pipefail

APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DOMAIN="${DOMAIN:-}"
HEALTH_URL="http://127.0.0.1:5000/healthz"

log() { printf '\n==> %s\n' "$1"; }
fail() { printf '\n!! %s\n' "$1" >&2; exit 1; }

[ -n "$DOMAIN" ] || fail "DOMAIN 을 지정하세요. 예: DOMAIN=example.com bash deploy.sh"

# 필요한 파일이 모두 있는지 먼저 확인한다. 중간에 멈춰 반쯤 배포되는 것을 막는다.
for f in wsgi.py requirements.txt deploy/memo.service deploy/nginx-memo.conf \
         deploy/nginx-limits.conf deploy/nginx-headers.conf; do
    [ -f "$APP_DIR/$f" ] || fail "필수 파일이 없습니다: $f"
done

# 디스크가 빠듯하면 배포를 시작하지 않는다. 중간에 멈추면 더 나쁘다.
free_mb=$(df --output=avail -m / | tail -1 | tr -dc '0-9')
if [ "${free_mb:-0}" -lt "${MIN_FREE_MB:-400}" ]; then
    fail "디스크 여유가 ${free_mb}MB 뿐입니다 (최소 ${MIN_FREE_MB:-400}MB)"
fi

# 롤백 대상 리비전을 pull 하기 '전에' 기록해야 의미가 있다. 바깥에서 git pull 을
# 하면 이미 새 커밋이 HEAD 라 롤백할 곳이 없으므로 코드 갱신도 여기서 처리한다.
PREV_REV=""
if git -C "$APP_DIR" rev-parse HEAD >/dev/null 2>&1; then
    PREV_REV="$(git -C "$APP_DIR" rev-parse HEAD)"
    log "배포 전 리비전: $PREV_REV"

    if [ "${PULL:-1}" = "1" ] && git -C "$APP_DIR" remote get-url origin >/dev/null 2>&1; then
        log "코드 갱신"
        git -C "$APP_DIR" fetch --prune origin
        git -C "$APP_DIR" checkout -- . 2>/dev/null || true
        git -C "$APP_DIR" merge --ff-only "origin/$(git -C "$APP_DIR" rev-parse --abbrev-ref HEAD)"
    fi
fi
NEW_REV="$(git -C "$APP_DIR" rev-parse --short HEAD 2>/dev/null || echo unknown)"

# --------------------------------------------------------------- 패키지 설치
if [ "${SKIP_APT:-0}" != "1" ]; then
    log "패키지 설치"
    sudo apt-get update -y
    sudo apt-get install -y python3-venv nginx certbot mongodb-database-tools
fi

# --------------------------------------------------------------- 파이썬 환경
log "가상환경 및 의존성"
[ -d "$APP_DIR/.venv" ] || python3 -m venv "$APP_DIR/.venv"
"$APP_DIR/.venv/bin/pip" install --quiet --upgrade pip
"$APP_DIR/.venv/bin/pip" install --quiet -r "$APP_DIR/requirements.txt"

if [ ! -f "$APP_DIR/.env" ]; then
    log ".env 생성"
    cat > "$APP_DIR/.env" <<EOF
# MongoDB 인증을 켰다면 자격 증명을 포함한 URI 로 바꾸세요.
#   mongodb://memo_app:<암호>@127.0.0.1:27017/memo?authSource=memo
MONGO_URI=mongodb://localhost:27017
DB_NAME=memo
EOF
fi

# 배포마다 확인해야 하는 값들. 없으면 추가하고, 다르면 갱신한다.
# .env 를 한 번 만들고 다시 안 보면 새로 생긴 설정이 조용히 빠진 채로 돈다.
set_env() {
    local key="$1" value="$2"
    if grep -q "^${key}=" "$APP_DIR/.env"; then
        sed -i "s|^${key}=.*|${key}=${value}|" "$APP_DIR/.env"
    else
        echo "${key}=${value}" >> "$APP_DIR/.env"
    fi
}
set_env APP_ENV production
set_env APP_REVISION "$NEW_REV"
set_env HOST 127.0.0.1
set_env PORT 5000
set_env FLASK_DEBUG 0
set_env LOG_LEVEL INFO
set_env SITE_ORIGIN "https://$DOMAIN"
set_env RATELIMIT_ENABLED 1

if ! grep -q '^ADMIN_TOKEN=' "$APP_DIR/.env"; then
    admin_token="$(python3 -c 'import secrets; print(secrets.token_urlsafe(24))')"
    echo "ADMIN_TOKEN=${admin_token}" >> "$APP_DIR/.env"
    log "관리자 토큰을 새로 만들었습니다. 소유자 없는 예전 메모를 고칠 때 씁니다:"
    echo "    $admin_token"
fi

# 권한은 조건 없이 매번 맞춘다.
chmod 600 "$APP_DIR/.env"

# --------------------------------------------------------------- 인증서
log "TLS 인증서"
sudo mkdir -p /var/www/certbot
if [ ! -d "/etc/letsencrypt/live/$DOMAIN" ]; then
    # 최초 발급: nginx 를 잠시 멈추고 standalone 으로 받는다.
    sudo systemctl stop nginx 2>/dev/null || true
    sudo certbot certonly --standalone -d "$DOMAIN" \
        --non-interactive --agree-tos --register-unsafely-without-email
else
    echo "이미 발급된 인증서를 사용합니다."
fi

# --------------------------------------------------------------- nginx
log "nginx 설정"
sudo mkdir -p /etc/nginx/snippets
sudo cp "$APP_DIR/deploy/nginx-headers.conf" /etc/nginx/snippets/memo-headers.conf
sudo cp "$APP_DIR/deploy/nginx-limits.conf" /etc/nginx/conf.d/memo-limits.conf
sed "s/DOMAIN_PLACEHOLDER/$DOMAIN/g" "$APP_DIR/deploy/nginx-memo.conf" \
    | sudo tee /etc/nginx/sites-available/memo > /dev/null
sudo ln -sf /etc/nginx/sites-available/memo /etc/nginx/sites-enabled/memo
sudo rm -f /etc/nginx/sites-enabled/default

# nginx(www-data)가 정적 파일까지 내려갈 수 있어야 한다. 홈 디렉터리가
# 700 이면 하위 경로를 읽지 못해 CSS/JS 가 전부 404 가 된다.
chmod o+x "$HOME" "$APP_DIR"
chmod -R o+rX "$APP_DIR/static"

sudo nginx -t || fail "nginx 설정 검증 실패"

# 갱신 후 nginx 가 새 인증서를 읽도록 훅을 건다.
sudo mkdir -p /etc/letsencrypt/renewal-hooks/deploy
printf '#!/bin/sh\nsystemctl reload nginx\n' \
    | sudo tee /etc/letsencrypt/renewal-hooks/deploy/reload-nginx.sh > /dev/null
sudo chmod +x /etc/letsencrypt/renewal-hooks/deploy/reload-nginx.sh

# --------------------------------------------------------------- 마이그레이션
log "인덱스 및 데이터 보정"
# 앱 기동 경로가 아니라 여기서 1회만 실행한다.
# shellcheck disable=SC1091
(cd "$APP_DIR" && set -a && . ./.env && set +a && \
    "$APP_DIR/.venv/bin/flask" --app wsgi init-db)

# --------------------------------------------------------------- 서비스 기동
log "systemd 서비스"
sudo cp "$APP_DIR/deploy/memo.service" /etc/systemd/system/memo.service
sudo systemctl daemon-reload
sudo systemctl enable memo nginx
if systemctl is-active --quiet memo; then
    sudo systemctl reload-or-restart memo
else
    sudo systemctl start memo
fi
sudo systemctl restart nginx

# --------------------------------------------------------------- 헬스 체크
log "헬스 체크"
healthy=0
for _ in $(seq 1 12); do
    if curl -fsS --max-time 5 "$HEALTH_URL" | grep -q '"db":"up"'; then
        healthy=1
        break
    fi
    sleep 2
done

if [ "$healthy" != "1" ]; then
    echo "헬스 체크 실패." >&2
    sudo journalctl -u memo -n 30 --no-pager >&2 || true

    if [ -n "$PREV_REV" ] && [ "${NO_ROLLBACK:-0}" != "1" ]; then
        log "이전 리비전으로 롤백: $PREV_REV"
        git -C "$APP_DIR" reset --hard "$PREV_REV"
        "$APP_DIR/.venv/bin/pip" install --quiet -r "$APP_DIR/requirements.txt"
        # 코드만 되돌리면 nginx 설정과 유닛 파일이 새 버전으로 남는다.
        sudo cp "$APP_DIR/deploy/memo.service" /etc/systemd/system/memo.service
        sudo cp "$APP_DIR/deploy/nginx-headers.conf" /etc/nginx/snippets/memo-headers.conf
        sed "s/DOMAIN_PLACEHOLDER/$DOMAIN/g" "$APP_DIR/deploy/nginx-memo.conf"             | sudo tee /etc/nginx/sites-available/memo > /dev/null
        sudo systemctl daemon-reload
        sudo nginx -t && sudo systemctl reload nginx
        sudo systemctl restart memo
        sleep 5
        if curl -fsS --max-time 5 "$HEALTH_URL" | grep -q '"db":"up"'; then
            fail "새 코드가 실패해 이전 리비전으로 되돌렸습니다. 서비스는 정상입니다."
        fi
        fail "롤백 후에도 헬스 체크가 실패합니다. 수동 확인이 필요합니다."
    fi
    fail "배포 실패"
fi

# 배포된 코드가 실제로 교체됐는지 확인한다.
served="$(curl -fsS --max-time 5 "$HEALTH_URL" \
    | python3 -c 'import json,sys; print(json.load(sys.stdin).get("revision", "unknown"))')"
if [ "$served" != "$NEW_REV" ]; then
    echo "경고: 서비스 중인 리비전($served)이 배포 대상($NEW_REV)과 다릅니다." >&2
fi

# 바깥에서 본 모습까지 확인한다. nginx 설정 오류와 TLS 문제를 여기서 잡는다.
if ! curl -fsS --max-time 10 -o /dev/null "https://$DOMAIN/"; then
    fail "외부에서 https://$DOMAIN 접속에 실패했습니다."
fi

log "완료: https://$DOMAIN (리비전 $NEW_REV)"
systemctl is-active memo nginx
