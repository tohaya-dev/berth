#!/usr/bin/env bash
# Backup and transactionally restore PostgreSQL; every verification error is fatal.
set -euo pipefail
umask 077
HERE="$(cd "$(dirname "$0")" && pwd)"
REPO="$(cd "$HERE/../.." && pwd)"
NS="${NS:-cynovela}"
DEST="${PG_BACKUP_DIR:-$HOME/dt-backups/k8s-pg}"
# BERTH_* is the primary name; HAN_SOLO_* is still honoured as a deprecated alias (BERTH_* wins when both are set).
KEY="${BERTH_SECRET_FILE:-${HAN_SOLO_SECRET_FILE:-$REPO/store/secret.key}}"
CONTEXT="${BERTH_CONTEXT:-${HAN_SOLO_CONTEXT:-}}"
PGUSER=cynovela
PGDB=cynovela
k() {
  if [ -n "$CONTEXT" ]; then
    kubectl --context "$CONTEXT" -n "$NS" "$@"
  else
    kubectl -n "$NS" "$@"
  fi
}
_pgpod() {
  k get pod -l app=cynovela-pgvector -o jsonpath='{.items[0].metadata.name}'
}
checksum() {
  python3 - "$1" <<'PY'
import hashlib,pathlib,sys
p=pathlib.Path(sys.argv[1]).resolve()
side=p.with_suffix(".sha256")
if not p.is_file() or p.stat().st_size==0 or not side.is_file():
    sys.exit("FAIL: nonempty dump and checksum sidecar required")
entries=[]
for line in side.read_text().splitlines():
    fields=line.split(maxsplit=1)
    if len(fields)==2 and fields[1].lstrip("*")==p.name:
        entries.append(fields[0])
if len(entries)!=1 or len(entries[0])!=64:
    sys.exit("FAIL: dump checksum entry missing or ambiguous")
h=hashlib.sha256()
with p.open("rb") as stream:
    for data in iter(lambda:stream.read(1024*1024),b""): h.update(data)
if h.hexdigest()!=entries[0]: sys.exit("FAIL: dump checksum mismatch")
PY
}
verify_dump() {
  local file="$1" pod vdb tables
  checksum "$file"
  pod="$(_pgpod)"
  [ -n "$pod" ] || { echo "FAIL: PostgreSQL pod missing" >&2; return 1; }
  vdb="cynovela_verify_$$_$RANDOM"
  k exec "$pod" -- psql -v ON_ERROR_STOP=1 -U "$PGUSER" -d postgres -c "CREATE DATABASE $vdb;" >/dev/null
  # Subshell keeps cleanup scoped to this temporary database and all failures fatal.
  (
    trap 'k exec "$pod" -- psql -v ON_ERROR_STOP=1 -U "$PGUSER" -d postgres -c "DROP DATABASE IF EXISTS $vdb;" >/dev/null' EXIT
    k exec -i "$pod" -- pg_restore --exit-on-error --single-transaction -U "$PGUSER" -d "$vdb" < "$file"
    tables="$(k exec "$pod" -- psql -v ON_ERROR_STOP=1 -U "$PGUSER" -d "$vdb" -tAc "SELECT count(*) FROM information_schema.tables WHERE table_schema='public' AND table_name IN ('users','workspaces','collections','chunks','chunks_vec');" | tr -d '[:space:]')"
    [ "$tables" = 5 ] || { echo "FAIL: required tables missing" >&2; exit 1; }
    [ "$(k exec "$pod" -- psql -v ON_ERROR_STOP=1 -U "$PGUSER" -d "$vdb" -tAc "SELECT count(*) FROM pg_extension WHERE extname='vector';" | tr -d '[:space:]')" = 1 ] || { echo "FAIL: pgvector missing" >&2; exit 1; }
    k exec "$pod" -- psql -v ON_ERROR_STOP=1 -U "$PGUSER" -d "$vdb" -tAc "SELECT count(*) FROM chunks; SELECT count(*) FROM chunks_vec;" >/dev/null
    echo "verify: checksum, transactional restore, required tables and pgvector PASS"
  )
}
case "${1:-}" in
  backup)
    [ -s "$KEY" ] || { echo "FAIL: paired encryption key required" >&2; exit 1; }
    mkdir -p "$DEST"
    POD="$(_pgpod)"
    [ -n "$POD" ] || { echo "FAIL: PostgreSQL pod missing" >&2; exit 1; }
    TS="$(date +%Y%m%d-%H%M%S)-$$"
    OUT="$DEST/pgdump-$TS.dump"
    k exec "$POD" -- pg_dump -U "$PGUSER" -d "$PGDB" -Fc > "$OUT.partial"
    [ -s "$OUT.partial" ]
    mv "$OUT.partial" "$OUT"
    cp "$KEY" "$DEST/secret.key-$TS"
    (cd "$DEST" && shasum -a 256 "pgdump-$TS.dump" "secret.key-$TS" > "pgdump-$TS.sha256")
    verify_dump "$OUT"
    echo "backup: $OUT"
    ;;
  verify)
    [ -n "${2:-}" ] || { echo "Usage: pg-backup.sh verify DUMP" >&2; exit 2; }
    verify_dump "$2"
    ;;
  restore)
    [ -n "${2:-}" ] && [ "${3:-}" = --yes ] || { echo "Usage: pg-backup.sh restore DUMP --yes" >&2; exit 2; }
    [ -s "$KEY" ] || { echo "FAIL: restore the paired key first; never regenerate it" >&2; exit 1; }
    python3 - "$2" "$KEY" <<'PYKEY'
import hashlib,pathlib,sys
dump,key=map(pathlib.Path,sys.argv[1:])
paired=dump.parent/("secret.key-"+dump.stem.removeprefix("pgdump-"))
side=dump.with_suffix(".sha256")
if not paired.is_file() or not side.is_file():
    sys.exit("FAIL: paired backup key and checksum required")
entries=[line.split(maxsplit=1) for line in side.read_text().splitlines()]
expected=[h for h,n in entries if n.lstrip("*")==paired.name]
if len(expected)!=1 or hashlib.sha256(paired.read_bytes()).hexdigest()!=expected[0]:
    sys.exit("FAIL: paired backup key checksum mismatch")
if key.read_bytes()!=paired.read_bytes():
    sys.exit("FAIL: destination encryption key does not match backup")
PYKEY
    # Complete scratch validation BEFORE modifying destination.
    verify_dump "$2"
    POD="$(_pgpod)"
    k exec -i "$POD" -- pg_restore --exit-on-error --single-transaction --clean --if-exists -U "$PGUSER" -d "$PGDB" < "$2"
    echo "restore: PASS"
    ;;
  *) echo "Usage: pg-backup.sh backup | verify DUMP | restore DUMP --yes" >&2; exit 2 ;;
esac
