#!/usr/bin/env bash
set -Eeuo pipefail
cd "$(dirname "$0")/.."
./scripts/backup.sh
docker compose build --pull
docker compose up -d
docker compose ps
