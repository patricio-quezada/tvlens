#!/bin/sh
# Runs on every machine start. Migrations first, so code never meets an older
# schema; they are a no-op when there is nothing new.
set -e
python manage.py migrate --noinput
# Two workers fit the 512 MB machine. SQLite under WAL takes one writer at a
# time, so more workers would mostly wait on each other.
exec gunicorn tvlens.wsgi:application --bind 0.0.0.0:8000 --workers 2 --access-logfile -
