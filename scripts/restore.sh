#!/usr/bin/env bash
set -Eeuo pipefail
if [[ $# -ne 1 ]]; then echo "Использование: $0 backups/YYYYMMDD_HHMMSS"; exit 1; fi
cd "$(dirname "$0")/.."
SOURCE="$1"
set -a; source .env; set +a
[[ -f "$SOURCE/database.dump" ]] || { echo 'Нет database.dump'; exit 1; }
docker compose stop web nginx || true
docker compose up -d db
cat "$SOURCE/database.dump" | docker compose exec -T db pg_restore -U "$POSTGRES_USER" -d "$POSTGRES_DB" --clean --if-exists --no-owner
if [[ -f "$SOURCE/media.tar.gz" ]]; then rm -rf data/media; mkdir -p data; tar -xzf "$SOURCE/media.tar.gz" -C data; fi
docker compose restart web nginx
printf '[1;32mВосстановление завершено.[0m
'
