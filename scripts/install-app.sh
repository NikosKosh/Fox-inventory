#!/usr/bin/env bash
set -Eeuo pipefail

APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$APP_DIR"

section() {
  printf '\n\033[1;36m========== %s ==========\033[0m\n' "$1"
}

fail() {
  printf '\n\033[1;31mERROR: %s\033[0m\n' "$1" >&2
  exit 1
}

section "FOX INVENTORY INSTALLATION"
command -v docker >/dev/null 2>&1 || fail "Docker is not installed. Run scripts/install-docker-ubuntu.sh first."
docker compose version >/dev/null 2>&1 || fail "Docker Compose plugin is not available."
command -v openssl >/dev/null 2>&1 || fail "OpenSSL is not installed."
command -v python3 >/dev/null 2>&1 || fail "Python 3 is not installed."

mkdir -p data/media data/static backups
chmod 750 data backups

if [[ ! -f .env ]]; then
  read -rp 'Application port [8088]: ' APP_PORT
  APP_PORT=${APP_PORT:-8088}

  read -rp 'Administrator login [admin]: ' ADMIN_USERNAME
  ADMIN_USERNAME=${ADMIN_USERNAME:-admin}

  while true; do
    read -rsp 'Administrator password (minimum 10 characters): ' ADMIN_PASSWORD
    printf '\n'
    if [[ ${#ADMIN_PASSWORD} -lt 10 ]]; then
      printf 'Password is too short.\n'
      continue
    fi
    break
  done

  SERVER_IP=$(hostname -I | awk '{print $1}')
  SECRET=$(openssl rand -hex 48)
  DBPASS=$(openssl rand -base64 36 | tr -d '/+=' | head -c 36)
  FERNET=$(python3 - <<'PY'
import base64
import os
print(base64.urlsafe_b64encode(os.urandom(32)).decode())
PY
)

  cat > .env <<EOF_ENV
APP_PORT=${APP_PORT}
DJANGO_SECRET_KEY=${SECRET}
DJANGO_DEBUG=0
DJANGO_ALLOWED_HOSTS=localhost,127.0.0.1,${SERVER_IP}
CSRF_TRUSTED_ORIGINS=
POSTGRES_DB=fox_inventory
POSTGRES_USER=fox_inventory
POSTGRES_PASSWORD=${DBPASS}
DB_HOST=db
DB_PORT=5432
ADMIN_USERNAME=${ADMIN_USERNAME}
ADMIN_PASSWORD=${ADMIN_PASSWORD}
ADMIN_EMAIL=
FIELD_ENCRYPTION_KEY=${FERNET}
TIME_ZONE=Europe/Moscow
MAX_UPLOAD_MB=25
ACT_DEFAULT_CITY=
ACT_ISSUE_REPRESENTATIVE_POSITION=
ACT_ISSUE_REPRESENTATIVE_NAME=
ACT_RETURN_REPRESENTATIVE_POSITION=
ACT_RETURN_REPRESENTATIVE_NAME=
TRUST_X_FORWARDED_PROTO=0
SECURE_SSL_REDIRECT=0
SESSION_COOKIE_SECURE=0
CSRF_COOKIE_SECURE=0
SECURE_HSTS_SECONDS=0
SECURE_HSTS_INCLUDE_SUBDOMAINS=0
SECURE_HSTS_PRELOAD=0
EOF_ENV
  chmod 600 .env
fi

section "BUILD AND START"
docker compose build --pull
docker compose up -d

section "HEALTH CHECK"
PORT=$(grep '^APP_PORT=' .env | cut -d= -f2)
HEALTH_OK=0
for _ in {1..45}; do
  if curl -fsS "http://127.0.0.1:${PORT}/health/" >/dev/null; then
    HEALTH_OK=1
    break
  fi
  sleep 2
done

if [[ $HEALTH_OK -ne 1 ]]; then
  docker compose ps
  docker compose logs --tail=200 web nginx
  fail "Application health check failed."
fi

docker compose ps
IP=$(hostname -I | awk '{print $1}')
section "INSTALLATION COMPLETE"
printf 'URL: http://%s:%s\n' "$IP" "$PORT"
printf 'Administrator credentials are stored in .env.\n'
