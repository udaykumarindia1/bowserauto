#!/usr/bin/env bash
# Consistent, encrypted backup of PostgreSQL plus persistent application files.
# Requires Docker Compose v2 and a GPG public key configured for BACKUP_GPG_RECIPIENT.
set -Eeuo pipefail
umask 077

BACKUP_DIR="${BACKUP_DIR:-${HOME}/bowserauto-backups}"
BACKUP_RETENTION_DAYS="${BACKUP_RETENTION_DAYS:-14}"
BACKUP_GPG_RECIPIENT="${BACKUP_GPG_RECIPIENT:-}"

if [[ -z "$BACKUP_GPG_RECIPIENT" ]]; then
  echo "Set BACKUP_GPG_RECIPIENT to a GPG public-key recipient; browser profiles are sensitive." >&2
  exit 2
fi
if ! command -v docker >/dev/null 2>&1 || ! docker compose version >/dev/null 2>&1; then
  echo "Docker Compose v2 is required." >&2
  exit 2
fi
if ! command -v gpg >/dev/null 2>&1; then
  echo "GPG is required to encrypt the database and browser-profile backups." >&2
  exit 2
fi
if ! [[ "$BACKUP_RETENTION_DAYS" =~ ^[0-9]+$ ]] || (( BACKUP_RETENTION_DAYS < 1 )); then
  echo "BACKUP_RETENTION_DAYS must be a positive integer." >&2
  exit 2
fi

stamp="$(date -u +%Y%m%dT%H%M%SZ)"
backup_path="$BACKUP_DIR/$stamp"
mkdir -p "$BACKUP_DIR"
mkdir "$backup_path"

services=(frontend backend worker browser-worker)
restore_services() {
  local original_status=$?
  local restore_status=0
  docker compose start "${services[@]}" >/dev/null 2>&1 || restore_status=$?
  if (( original_status != 0 )); then
    # Never leave a partial backup that could be mistaken for a usable snapshot.
    rm -rf -- "$backup_path"
  fi
  if (( restore_status != 0 )); then
    echo "Warning: failed to restart application services after the backup." >&2
    (( original_status != 0 )) || original_status=$restore_status
  fi
  return "$original_status"
}
trap restore_services EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

# Stop all application writers while the DB and file volume are captured. Running tasks are
# returned to the persistent queue by the worker shutdown path and resume after services start.
docker compose stop "${services[@]}"

docker compose exec -T postgres sh -c 'exec pg_dump -U "$POSTGRES_USER" "$POSTGRES_DB"' \
  | gpg --batch --yes --trust-model always --recipient "$BACKUP_GPG_RECIPIENT" \
      --output "$backup_path/database.sql.gpg" --encrypt

docker compose run --rm --no-deps -T --entrypoint tar backend -czf - -C /data . \
  | gpg --batch --yes --trust-model always --recipient "$BACKUP_GPG_RECIPIENT" \
      --output "$backup_path/app-data.tar.gz.gpg" --encrypt

{
  printf 'created_at_utc=%s\n' "$stamp"
  printf 'git_revision=%s\n' "$(git rev-parse HEAD 2>/dev/null || echo unknown)"
  printf 'gpg_recipient=%s\n' "$BACKUP_GPG_RECIPIENT"
  printf 'contents=postgresql-dump,app-data-volume\n'
  printf 'note=Keep the GPG private key and ENCRYPTION_KEY outside this backup.\n'
} > "$backup_path/manifest.txt"

# Retain only recent backup files; the encrypted files cannot be inspected by the script.
find "$BACKUP_DIR" -mindepth 2 -type f -mtime "+$BACKUP_RETENTION_DAYS" -delete
find "$BACKUP_DIR" -mindepth 1 -type d -empty -delete

printf 'Encrypted backup created at %s\n' "$backup_path"
printf 'Copy it to an access-controlled offsite location and test restore procedures regularly.\n'
