#!/usr/bin/env sh
set -eu
python manage.py migrate --noinput
python manage.py collectstatic --noinput
python manage.py ensure_admin
exec gunicorn config.wsgi:application --bind 0.0.0.0:8000 --workers 3 --threads 2 --timeout 120 --access-logfile - --error-logfile -
