#!/usr/bin/env bash
# settlement-allforms — build & run Cynovela container (podman). Reference deliverable.
# Usage: ./run-container.sh [MODE]   (MODE: full|text|lite|lite-en|minimal, default text)
#
# entry-unify-20260802 (DD-CYN-0020 S-1):
#   この系統の入れ物 (コンテナ) は、この入口を1本に寄せる作業の対象外です。理由は、
#   イメージの ENTRYPOINT に --demo が焼き込まれており「引数なし=本番 / --demo=デモ」の
#   2通りへ揃えるには Containerfile を書き換える必要があるためです (今回は触れません)。
#   ここで直したのは、消してはいけないものを消しうる箇所だけです:
#     ・IMG / NAME を環境変数で指定できるようにした (従来は固定値だった)
#     ・同じ名前の入れ物を無条件に `podman rm -f` していたのをやめた
#       (固定の既定名だったため、同じ名前の入れ物を持っている人が文書どおりに実行しただけで
#        確認も無く消えた。2>/dev/null || true が付いており、消したことすら見えなかった)
set -euo pipefail
MODE="${1:-text}"
REPO="$(cd "$(dirname "$0")/../.." && pwd)"
IMG="${IMG:-cynovela-settle:latest}"
NAME="${NAME:-cynovela-settle}"
HOSTPORT="${HOSTPORT:-8801}"
DB_VOLUME="${DB_VOLUME:-cyn-db}"
VECTOR_VOLUME="${VECTOR_VOLUME:-cyn-vec}"
BACKUP_VOLUME="${BACKUP_VOLUME:-cyn-bk}"
SOURCE_REVISION="$(git -C "$REPO" rev-parse --verify HEAD 2>/dev/null || printf unknown)"
SOURCE_REPOSITORY="$(git -C "$REPO" config --get remote.origin.url 2>/dev/null || true)"
case "$SOURCE_REPOSITORY" in *://*@*) SOURCE_REPOSITORY=unknown ;; esac
[ -n "$SOURCE_REPOSITORY" ] || SOURCE_REPOSITORY=unknown
BUILD_TIME="$(date -u +'%Y-%m-%dT%H:%M:%SZ')"
VERSION="$(awk -F': *' '$1=="version"{print $2}' "$REPO/VERSION" 2>/dev/null || true)"
[ -n "$VERSION" ] || VERSION=unknown

# V3.5.0 Stage C: demo.db build 前 checkpoint(文書化)。
#   .containerignore が WAL/SHM を除外するため image には本体 demo.db のみ焼かれる。
#   稼働中サーバが書込中だと本体に未反映の WAL が残り得る。一貫スナップショットを焼くには
#   ビルド前に WAL を本体へ畳む(冪等・任意・既定では実行しない=挙動不変):
#     sqlite3 "$REPO/store/db/demo.db" 'PRAGMA wal_checkpoint(TRUNCATE);' 2>/dev/null || true
echo "[build] context=$REPO"
podman build \
  --build-arg "BERTH_SOURCE_REVISION=$SOURCE_REVISION" \
  --build-arg "BERTH_SOURCE_REPOSITORY=$SOURCE_REPOSITORY" \
  --build-arg "BERTH_BUILD_TIME=$BUILD_TIME" \
  --build-arg "BERTH_VERSION=$VERSION" \
  -t "$IMG" -f "$REPO/deploy/container/Containerfile" "$REPO"

echo "[run] mode=$MODE port=$HOSTPORT"
# 受け取り手の持ち物を、確認も無く消さない。同じ名前の入れ物が在るときは消さずに止める。
if podman container exists "$NAME" 2>/dev/null; then
  echo "エラー: '$NAME' という名前の入れ物が既にあります。" >&2
  echo "       中身が分からないため、消さずに止めました。" >&2
  echo "       別の名前で起動するには: NAME=好きな名前 IMG=好きなイメージ名 $0 $*" >&2
  echo "       その入れ物が不要な場合は、ご自身で podman rm -f '$NAME' を実行してください。" >&2
  exit 2
fi
# DD-CYN-0115 M-10 (falcon DD-CYN-0086 と同じ振る舞い): この機材の時間帯をそのまま入れ物へ渡す。
# 渡さなければ入れ物の中は世界標準時になり、入れ物が自分の時計で文字にした時刻だけがずれる。
# 読み出せなかったときは渡さない (世界標準時のまま動く。止まらない)。
TZ_ARGS=()
_host_tz="$(readlink /etc/localtime 2>/dev/null | sed -n 's|.*/zoneinfo/||p')"
if [ -n "${_host_tz}" ]; then
  TZ_ARGS=(-e "TZ=${_host_tz}")
  echo "[tz] この機材の時間帯を入れ物へ渡します: ${_host_tz}"
else
  echo "[tz] 時間帯を読み出せませんでした。渡さずに起こします"
fi
# models mounted read-only (9GB, not in image); data dirs are NAMED volumes (VM fs => SQLite WAL OK, NOT bind mounts)
podman run -d --name "$NAME" \
  -p "${HOSTPORT}:8765" \
  ${TZ_ARGS[@]+"${TZ_ARGS[@]}"} \
  -e MODE="$MODE" \
  -v "$REPO/store/models:/app/store/models:ro" \
  -v "$DB_VOLUME:/app/store/db" \
  -v "$VECTOR_VOLUME:/app/store/vector" \
  -v "$BACKUP_VOLUME:/app/store/backups" \
  "$IMG"

echo "[wait] http://127.0.0.1:${HOSTPORT}/"
for i in $(seq 1 60); do
  code=$(curl -s -o /dev/null -w "%{http_code}" --max-time 4 "http://127.0.0.1:${HOSTPORT}/" 2>/dev/null || true)
  [ "$code" = "200" ] && { echo "ready ($((i*3))s)"; break; }
  sleep 3
done

# LLM wiring: container reaches host LLM via host.containers.internal. non-localhost => dummy api_key required.
# Ollama must serve on 0.0.0.0 (OLLAMA_HOST=0.0.0.0); LM Studio must "Serve on Local Network".
echo "[hint] set LLM via:"
# DD-CYN-0116 X-6 U-1: 固定トークンは受け口で封鎖済み。ログインで発行された値を使う。
echo "  # TOKEN=<画面のログインで発行されたトークン>"
echo "  curl -XPOST -H \"Authorization: Bearer \$TOKEN\" -H 'Content-Type: application/json' \\"
echo "    -d '{\"provider\":\"ollama\",\"base_url\":\"http://host.containers.internal:11434/v1\",\"model\":\"qwen2.5:7b\",\"api_key\":\"dummy\"}' \\"
echo "    http://127.0.0.1:${HOSTPORT}/api/settings/llm"
