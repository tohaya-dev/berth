#!/usr/bin/env bash
# DD-CYN-0116 G-4: 旧いクラスタ名の残骸を掃除する。
#
# 作り直しのたびにネットワーク・ボリューム・接続設定が残り、次に同じ名前で作るとき衝突しうる
# ことが実測されている。ここで名指しで落とす。
#
#   ./deploy/k8s/cleanup-stale.sh            # 何が残っているかを出すだけ (既定)
#   ./deploy/k8s/cleanup-stale.sh --apply    # 実際に落とす
#
# 稼働中のクラスタは触らない。KEEP に挙げた名前も触らない。
set -uo pipefail
APPLY=0
[ "${1:-}" = "--apply" ] && APPLY=1

# いま使うクラスタ (これは絶対に消さない)。環境変数で上書きできる。
KEEP="${CYNOVELA_CLUSTER:-cynovela-hansolo-dd0115}"
# GA scratch deploy では scratch クラスタを作りながら現行 Berth も同時に守る。
# 空白区切りで追加の保護対象を渡せるようにする。
EXTRA_KEEP="${CYNOVELA_EXTRA_KEEP:-${HAN_SOLO_CLUSTER:-}}"

is_kept_cluster() {
  local name="$1"
  [ "$name" = "$KEEP" ] && return 0
  for extra in $EXTRA_KEEP; do
    [ "$name" = "$extra" ] && return 0
  done
  return 1
}

if [ -z "${DOCKER_HOST:-}" ]; then
  SOCK="$(podman machine inspect --format '{{.ConnectionInfo.PodmanSocket.Path}}' 2>/dev/null || true)"
  [ -n "$SOCK" ] && export DOCKER_HOST="unix://$SOCK"
fi

echo "=== 残骸の掃除 (DD-CYN-0116 G-4) ==="
echo "残すクラスタ: $KEEP"
[ -n "$EXTRA_KEEP" ] && echo "追加で残すクラスタ: $EXTRA_KEEP"
[ "$APPLY" = "1" ] && echo "→ 実行します" || echo "→ 出すだけ (落とすには --apply)"
echo

echo "[1] k3d クラスタ"
LIVE="$(k3d cluster list --no-headers 2>/dev/null | awk '{print $1}')"
for c in $LIVE; do
  if is_kept_cluster "$c"; then
    echo "  残す   $c"
  else
    echo "  対象   $c"
    [ "$APPLY" = "1" ] && k3d cluster delete "$c"
  fi
done
[ -z "$LIVE" ] && echo "  (クラスタ無し)"

echo "[2] 使われていない podman ネットワーク (k3d-*)"
for n in $(podman network ls --format '{{.Name}}' 2>/dev/null | grep '^k3d-' || true); do
  CNAME="${n#k3d-}"
  if is_kept_cluster "$CNAME"; then echo "  残す   $n"; continue; fi
  if echo "$LIVE" | grep -qx "$CNAME"; then echo "  残す   $n (クラスタ稼働中)"; continue; fi
  echo "  対象   $n"
  [ "$APPLY" = "1" ] && podman network rm "$n" 2>/dev/null || true
done

echo "[3] kubeconfig の残った context / cluster / user"
for ctx in $(kubectl config get-contexts -o name 2>/dev/null | grep '^k3d-' || true); do
  CNAME="${ctx#k3d-}"
  if is_kept_cluster "$CNAME"; then echo "  残す   $ctx"; continue; fi
  if echo "$LIVE" | grep -qx "$CNAME"; then echo "  残す   $ctx (クラスタ稼働中)"; continue; fi
  echo "  対象   $ctx"
  if [ "$APPLY" = "1" ]; then
    kubectl config delete-context "$ctx" >/dev/null 2>&1 || true
    kubectl config delete-cluster "$ctx" >/dev/null 2>&1 || true
    kubectl config unset "users.admin@$ctx" >/dev/null 2>&1 || true
  fi
done

echo "[4] 止まったままの入れ物 (k3d-*)"
for c in $(podman ps -a --format '{{.Names}}\t{{.Status}}' 2>/dev/null | grep '^k3d-' | grep -i 'exited' | cut -f1 || true); do
  case "$c" in
    k3d-"$KEEP"-*) echo "  残す   $c"; continue;;
  esac
  for extra in $EXTRA_KEEP; do
    case "$c" in
      k3d-"$extra"-*) echo "  残す   $c"; continue 2;;
    esac
  done
  echo "  対象   $c"
  [ "$APPLY" = "1" ] && podman rm "$c" >/dev/null 2>&1 || true
done

echo
echo "=== 完了 ==="
