#!/bin/sh
set -eu

: "${POSTGRES_HOST:=postgres}"
: "${POSTGRES_PORT:=5432}"
: "${POSTGRES_DB:=unicrawler}"
: "${POSTGRES_USER:=unicrawler}"
: "${POSTGRES_PASSWORD:=unicrawler}"

export PGPASSWORD="$POSTGRES_PASSWORD"
until pg_isready -h "$POSTGRES_HOST" -p "$POSTGRES_PORT" -U "$POSTGRES_USER" -d "$POSTGRES_DB"; do
  sleep 1
done

GOOSE_DRIVER=postgres \
GOOSE_DBSTRING="postgres://${POSTGRES_USER}:${POSTGRES_PASSWORD}@${POSTGRES_HOST}:${POSTGRES_PORT}/${POSTGRES_DB}?sslmode=disable" \
goose -dir /db/migrations up
