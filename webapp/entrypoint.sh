#!/bin/sh
set -eu
umask 077
python manage.py migrate --noinput
python manage.py collectstatic --noinput
exec python -m gunicorn config.wsgi:application --bind 0.0.0.0:8000 --workers 1 --threads 4 --timeout 60 --access-logfile - --error-logfile -
