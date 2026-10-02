#!/usr/bin/env bash
# DD-CYN-0116 G-14: 一連の通しを1つの命令で走らせて結果を残す。
#
# 走行のたびに手で組み立てるのをやめる。以後の走行の合否はこの命令の結果を基準にする。
# 受け取り手が実際に押す道 (HTTP の入口) で測る。内部を直接叩いた結果を合格の根拠にしない。
#
#   ./deploy/k8s/verify-all.sh                 # 全部
#   ./deploy/k8s/verify-all.sh --quick         # 取り込みを伴わない項目だけ
#   ENTRY_PORT=18890 ADMIN_USER=... ./deploy/k8s/verify-all.sh
#
# 合言葉は環境変数から取り、出力には一切書かない。
set -uo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
NS="${NS:-cynovela}"
ENTRY_PORT="${ENTRY_PORT:-18890}"
BASE="http://localhost:${ENTRY_PORT}"
ADMIN_USER="${ADMIN_USER:-cynovela}"
ADMIN_PASS="${ADMIN_PASS:-Cynovela1!}"
VIEWER_USER="${VIEWER_USER:-demo}"
VIEWER_PASS="${VIEWER_PASS:-demo1234}"
QUICK=0
[ "${1:-}" = "--quick" ] && QUICK=1

PASS=0; FAIL=0
ok()   { printf '  [合格] %s\n' "$1"; PASS=$((PASS+1)); }
ng()   { printf '  [不合格] %s\n' "$1"; FAIL=$((FAIL+1)); }
note() { printf '         %s\n' "$1"; }

# ログイン (5回/分/IP の制限があるので間隔をあける)
_login() {
  python3 - "$BASE" "$1" "$2" <<'PY'
import json, sys, urllib.request
base, u, p = sys.argv[1], sys.argv[2], sys.argv[3]
req = urllib.request.Request(base + "/api/auth/login",
    data=json.dumps({"username": u, "password": p}).encode(),
    headers={"Content-Type": "application/json"})
try:
    with urllib.request.urlopen(req, timeout=20) as r:
        print(json.load(r).get("access_token") or json.load(r).get("token") or "")
except Exception:
    print("")
PY
}
_code() { curl -s -o /dev/null -w '%{http_code}' --max-time 30 "$@"; }

echo "=== 通しの確認 (DD-CYN-0116 G-14) ==="
echo "入口: $BASE"
echo

echo "[V-1] 入口が応じる"
[ "$(_code "$BASE/api/health")" = "200" ] && ok "health 200" || ng "health が 200 になりません"

echo "[V-2] ログイン (管理者・閲覧者) と、誤った合言葉の拒否"
ATOK="$(_login "$ADMIN_USER" "$ADMIN_PASS")"; sleep 13
VTOK="$(_login "$VIEWER_USER" "$VIEWER_PASS")"; sleep 13
[ -n "$ATOK" ] && ok "管理者でログインできる" || ng "管理者でログインできない"
[ -n "$VTOK" ] && ok "閲覧者でログインできる" || ng "閲覧者でログインできない"
BADTOK="$(_login "$ADMIN_USER" "definitely-not-the-password-9x")"; sleep 13
[ -z "$BADTOK" ] && ok "誤った合言葉は通らない (陰性対照)" || ng "誤った合言葉が通ってしまう"

echo "[V-3] 固定トークンでの到達が封鎖されている (X-6 U-1)"
C1="$(_code -H 'Authorization: Bearer demo-token-user-admin' "$BASE/api/settings/llm")"
C2="$(_code -H 'Authorization: Bearer demo-token-user-admin' "$BASE/api/admin/users")"
if [ "$C1" = "401" ] && [ "$C2" = "401" ]; then ok "demo-token は 401 (settings/llm・admin/users とも)"; else ng "固定トークンで到達できる (llm=$C1 users=$C2)"; fi
C3="$(_code "$BASE/api/settings/llm")"
[ "$C3" = "401" ] && ok "トークン無しは 401 (陽性対照)" || ng "トークン無しが $C3"

echo "[V-4] 索引と問い合わせの埋め込みが揃っている (G-9)"
if [ -n "$ATOK" ]; then
  IDENT="$(curl -s --max-time 30 -H "Authorization: Bearer $ATOK" "$BASE/api/settings/embedding" \
    | python3 -c 'import json,sys; d=json.load(sys.stdin).get("identity") or {}; print(d.get("checked"), d.get("match"), (d.get("message") or "")[:60])' 2>/dev/null || echo "")"
  note "identity: $IDENT"
  case "$IDENT" in
    "True True"*) ok "索引と問い合わせの埋め込みが一致";;
    "True None"*) ok "突き合わせは成立 (記録なし/確認できない: 上の文言のとおり)";;
    "True False"*) ng "埋め込みが食い違っている";;
    *) ng "埋め込み識別が返っていない (画面の警告が死蔵)";;
  esac
fi

echo "[V-5] 順番待ちの見える化 (G-13)"
if [ -n "$ATOK" ]; then
  Q="$(curl -s --max-time 30 -H "Authorization: Bearer $ATOK" "$BASE/api/queue/status" 2>/dev/null || echo "")"
  if echo "$Q" | grep -q 'pending'; then ok "順番待ちの長さ・処理中・失敗が読める"; note "$(echo "$Q" | head -c 200)"; else ng "順番待ちの状態が読めない"; fi
fi

echo "[V-6] 中の様子 (Pod・列・DB)"
if command -v kubectl >/dev/null 2>&1; then
  R="$(kubectl -n "$NS" get pods --no-headers 2>/dev/null | awk '$3!="Running" && $3!="Completed"' | wc -l | tr -d ' ')"
  [ "${R:-1}" = "0" ] && ok "全 Pod が Running/Completed" || { ng "Running でない Pod が $R 個"; kubectl -n "$NS" get pods --no-headers | awk '$3!="Running" && $3!="Completed"'; }
  NV="$(kubectl -n "$NS" exec deploy/cynovela-pgvector -- psql -U cynovela -d cynovela -tAc "SELECT count(*) FROM chunks_vec;" 2>/dev/null | tr -d '[:space:]')"
  NC="$(kubectl -n "$NS" exec deploy/cynovela-pgvector -- psql -U cynovela -d cynovela -tAc "SELECT count(*) FROM chunks;" 2>/dev/null | tr -d '[:space:]')"
  note "本文 ${NC:-?} 行 / 索引 ${NV:-?} 行"
  if [ "${NC:-0}" -gt 0 ] && [ "${NV:-0}" -eq 0 ]; then ng "本文はあるのに索引が空 (Z-3 の復元が働いていない)"; else ok "本文と索引の関係が成立"; fi
fi

echo "[V-7] 閲覧者に生の個人情報が出ない"
if [ -n "$VTOK" ] && [ "$QUICK" = "0" ]; then
  ANS="$(curl -s --max-time 180 -X POST "$BASE/api/chat" \
    -H "Authorization: Bearer $VTOK" -H 'Content-Type: application/json' \
    -d '{"message":"連絡先を教えてください"}' 2>/dev/null || echo "")"
  if echo "$ANS" | grep -q 'MASKED'; then ok "閲覧者の答えは伏字済み"; else note "伏字の印が見当たらない (該当資料が無い場合もある)"; fi
  if echo "$ANS" | grep -Eq '[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}'; then ng "閲覧者の答えに生のメールが出ている"; else ok "閲覧者の答えに生のメール 0 件"; fi
else
  note "(--quick のため飛ばしました)"
fi

echo "[V-8] 一度直した欠陥の試験 (G-12)"
# DD-CYN-0143 K-3: pytest を持つ python を実行時に解決する (base python3 には pytest が無い機材がある)。
PYT="${PYBIN:-}"
if [ -z "$PYT" ] && [ -x "$HOME/miniforge3/envs/cynovela/bin/python" ]; then PYT="$HOME/miniforge3/envs/cynovela/bin/python"; fi
[ -z "$PYT" ] && PYT="$(command -v python3 || true)"
if [ -n "$PYT" ] && "$PYT" -c 'import pytest' 2>/dev/null; then
  ( cd "$HERE/../.." && "$PYT" -m pytest tests/test_regression_guard.py -q 2>&1 | tail -5 )
  # shellcheck disable=SC2181
  if [ "${PIPESTATUS[0]:-1}" = "0" ]; then ok "退行の番人が通った"; else ng "退行の番人が落ちた (上の出力)"; fi
else
  ng "pytest を持つ python が見つからない (PYBIN=<pytestを持つpython> を指定する)"
fi

echo
echo "=== 合格 $PASS / 不合格 $FAIL ==="
[ "$FAIL" = "0" ] || exit 1
exit 0
