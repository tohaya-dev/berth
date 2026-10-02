#!/usr/bin/env bash
# DD-CYN-0116 G-3: 立ち上げの前に前提を機械で検査する。
#
# 狙い: 「立ててから気づく」を無くす。足りないものを名指しで出して、その場で止まる。
# 何も変更しない (読み取りだけ)。rebuild-all.sh の先頭から呼ばれるほか、単独でも走らせられる。
#
# 使い方:
#   ./deploy/k8s/preflight.sh          # 検査だけ
#   PYBIN=... LLM_BASE_URL=... HAN_SOLO_MODELS_DIR=... ./deploy/k8s/preflight.sh
set -uo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
REPO="$(cd "$HERE/../.." && pwd)"
if [ -z "${PYBIN:-}" ]; then
  for cand in \
    "$REPO/.revival-venv/bin/python" \
    "$HOME/miniforge3/envs/cynovela/bin/python" \
    "$(command -v python3 2>/dev/null || true)"; do
    if [ -n "$cand" ] && [ -x "$cand" ]; then
      PYBIN="$cand"
      break
    fi
  done
fi
LLM_BASE_URL="${LLM_BASE_URL:-http://host.containers.internal:1234/v1}"
LLM_PROBE_URL="${LLM_PROBE_URL:-http://127.0.0.1:1234}"

FAIL=0
ok()   { printf '  [OK]   %s\n' "$1"; }
bad()  { printf '  [NG]   %s\n' "$1"; FAIL=$((FAIL+1)); }
warn() { printf '  [注意] %s\n' "$1"; }

echo "=== 立ち上げ前の前提検査 (DD-CYN-0116 G-3) ==="
echo "repo: $REPO"

echo "[1] 道具"
for c in k3d kubectl podman; do
  if command -v "$c" >/dev/null 2>&1; then ok "$c = $(command -v "$c")"; else bad "$c が見つかりません"; fi
done
if command -v k3d >/dev/null 2>&1; then
  ok "k3d $(k3d version 2>/dev/null | head -1)"
fi

echo "[2] Podman"
if ! command -v podman >/dev/null 2>&1; then
  bad "podman が無いので以降の検査を飛ばします"
else
  MACHINE_STATE="$(podman machine inspect --format '{{.State}}' 2>/dev/null | head -1 || true)"
  if [ "$MACHINE_STATE" = "running" ] || podman machine list --format '{{.Name}} {{.LastUp}}' 2>/dev/null | grep -qi 'currently running'; then
    ok "podman machine が起動している"
  else
    bad "podman machine が起動していません (state=${MACHINE_STATE:-unknown}) → podman machine start"
  fi
  # k3d は docker の口を探す。podman の口を指す環境変数が要る。
  # 記号リンク (/var/run/docker.sock) に頼らない: 前走行はこれが宙吊りで初動に失敗した。
  SOCK="$(podman machine inspect --format '{{.ConnectionInfo.PodmanSocket.Path}}' 2>/dev/null || true)"
  if [ -n "$SOCK" ] && [ -S "$SOCK" ]; then
    ok "podman の口 = $SOCK"
    echo "         export DOCKER_HOST=\"unix://$SOCK\""
  else
    bad "podman の口が見つかりません (podman machine inspect が空)"
  fi
  if [ -L /var/run/docker.sock ] && [ ! -e /var/run/docker.sock ]; then
    warn "/var/run/docker.sock が宙吊りです (-> $(readlink /var/run/docker.sock))。DOCKER_HOST を使うので実害はありません"
  fi
  # Podman の既定のネットワークは名前解決が無効。クラスタ用には有効なものが要る。
  if podman network inspect podman --format '{{.DNSEnabled}}' 2>/dev/null | grep -qi true; then
    ok "既定ネットワークの名前解決が有効"
  else
    warn "既定ネットワーク 'podman' は名前解決が無効です。k3d は自前のネットワークを作るので通常は問題になりません"
  fi
fi

echo "[3] 鍵 (これを取り違えると既存の本文が二度と読めない)"
if [ -f "$REPO/store/secret.key" ]; then
  ok "store/secret.key あり (sha256 $(shasum -a 256 "$REPO/store/secret.key" | cut -c1-16)…)"
else
  bad "store/secret.key がありません。作り直しの手順は鍵を新造しません (CYNOVELA_ALLOW_NEW_KEY=1 が明示の逃げ道)"
fi

echo "[4] モデルの実体"
MODELS="${HAN_SOLO_MODELS_DIR:-$REPO/store/models}"
if [ -d "$MODELS" ]; then
  SZ="$(du -sk "$MODELS" 2>/dev/null | cut -f1)"
  if [ "${SZ:-0}" -gt 1000000 ]; then ok "models directory あり: $MODELS ($(( SZ / 1024 )) MB)"; else bad "models directory が小さすぎます: $MODELS ($(( ${SZ:-0} / 1024 )) MB)。実体が入っていない可能性"; fi
  if [ -n "$(find "$MODELS" -maxdepth 3 -iname '*bge-m3*' -print -quit 2>/dev/null)" ]; then ok "bge-m3 あり"; else bad "bge-m3 が見つかりません"; fi
else
  bad "models directory がありません: $MODELS"
fi

echo "[5] python (yaml を持つもの)"
if [ -x "$PYBIN" ]; then
  if "$PYBIN" -c 'import yaml' 2>/dev/null; then
    ok "PYBIN=$PYBIN (yaml あり)"
  else
    bad "PYBIN=$PYBIN に yaml がありません"
  fi
  if [ ! -f "$REPO/store/secret.key" ] && [ "${CYNOVELA_ALLOW_NEW_KEY:-}" = "1" ]; then
    if "$PYBIN" -c 'import cryptography' 2>/dev/null; then
      ok "cryptography あり (新規鍵生成を明示した場合のみ使用)"
    else
      bad "CYNOVELA_ALLOW_NEW_KEY=1 ですが cryptography がありません"
    fi
  fi
else
  bad "PYBIN=$PYBIN が実行できません (PYBIN=... で指定できます)"
fi

echo "[6] 推論サーバへの到達"
if curl -s -o /dev/null --max-time 3 "$LLM_PROBE_URL/v1/models" 2>/dev/null; then
  ok "推論サーバ $LLM_PROBE_URL に届く (Pod からは $LLM_BASE_URL 経由)"
else
  warn "推論サーバ $LLM_PROBE_URL に届きません。取り込みと検索は動きますが、回答は出ません"
fi

echo "[7] 空き容量"
AVAIL_K="$(df -k "$HOME" | tail -1 | awk '{print $4}')"
if [ "${AVAIL_K:-0}" -gt 20971520 ]; then ok "起動ディスクの空き $(( AVAIL_K / 1048576 )) GB"; else warn "起動ディスクの空きが $(( ${AVAIL_K:-0} / 1048576 )) GB しかありません (20GB 以上を推奨)"; fi

echo
if [ "$FAIL" -gt 0 ]; then
  echo "=== 検査 不合格: $FAIL 件。上の [NG] を直してから作り直してください。 ==="
  exit 1
fi
echo "=== 検査 合格。作り直せます。 ==="
exit 0
