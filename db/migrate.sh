#!/usr/bin/env bash
# Apply pending migrations in db/migrations/ to $DATABASE_URL (or the URL given as $1).
# Loads .env if present. Each migration from 0007 on records itself in schema_migrations
# inside its own transaction; files whose version is already recorded are skipped.
set -euo pipefail
cd "$(dirname "$0")/.."

if [ -f .env ]; then
    set -a; source .env; set +a
fi
url="${1:-${DATABASE_URL:?DATABASE_URL not set}}"

has_table=$(psql "$url" -Atc "SELECT to_regclass('public.schema_migrations') IS NOT NULL")
for f in db/migrations/*.sql; do
    version=$(basename "$f" | cut -d_ -f1)
    if [ "$has_table" = t ] && [ -n "$(psql "$url" -Atc \
            "SELECT 1 FROM schema_migrations WHERE version = '$version'")" ]; then
        continue
    fi
    echo "applying $f"
    psql "$url" -q -v ON_ERROR_STOP=1 -f "$f"
    has_table=$(psql "$url" -Atc "SELECT to_regclass('public.schema_migrations') IS NOT NULL")
done
