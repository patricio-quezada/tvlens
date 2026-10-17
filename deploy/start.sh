#!/bin/sh
# Runs on every machine start. Migrations first, so code never meets an older
# schema; they are a no-op when there is nothing new.
set -e

# Restoring or replacing the database: upload the file as /data/db.upload and
# restart the machine. It is swapped in here, before anything opens the
# database, and the old WAL sidecars go with the old file; left behind, SQLite
# would replay them into the new one.
if [ -f /data/db.upload ]; then
    rm -f "$DB_NAME-wal" "$DB_NAME-shm"
    mv /data/db.upload "$DB_NAME"
    echo "Swapped in /data/db.upload as $DB_NAME"
fi

python manage.py migrate --noinput
# Two workers fit the 512 MB machine. SQLite under WAL takes one writer at a
# time, so more workers would mostly wait on each other.
exec gunicorn tvlens.wsgi:application --bind 0.0.0.0:8000 --workers 2 --access-logfile -
