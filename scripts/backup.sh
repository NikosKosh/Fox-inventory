#!/usr/bin/env bash
set -Eeuo pipefail
cd "$(dirname "$0")/.."
set -a; source .env; set +a
STAMP=$(date +%Y%m%d_%H%M%S)
TARGET="backups/${STAMP}"
mkdir -p "$TARGET"
docker compose exec -T db pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Fc > "$TARGET/database.dump"
tar -czf "$TARGET/media.tar.gz" -C data media
cp .env "$TARGET/env.backup"
chmod 600 "$TARGET/env.backup"
printf '[1;32mРезервная копия создана: %s[0m
' "$TARGET"
