#!/usr/bin/env bash
# Add an ingest folder to a running Berth (Linux / WSL2 Kubernetes path).
#
# The ingest area is a hostPath: "$BERTH_DATA_DIR/ingest" on the node is mounted read-only into the API and
# worker pods as /app/ingest (deploy/k8s/linux/render.py). A new subfolder is therefore visible to the pods at
# once. This script only creates a host folder and talks to the existing HTTP API; it never changes Kubernetes
# objects (no PVC, no Deployment change, no rollout).
set -euo pipefail
umask 022   # ingest content is plain documents the owner manages; secrets are never written by this script

# BERTH_* is the primary name; HAN_SOLO_* is still honoured as a deprecated alias (BERTH_* wins when both are set).
DATA="${BERTH_DATA_DIR:-${HAN_SOLO_DATA_DIR:-$HOME/.local/share/berth}}"
# Default resolution (only when neither variable is set): the default for new installs is
# ~/.local/share/berth. An install that relied on the former default ~/.local/share/hansolo and has no
# ~/.local/share/berth keeps finding its data; set BERTH_DATA_DIR explicitly to silence the notice.
if [[ -z "${BERTH_DATA_DIR:-}${HAN_SOLO_DATA_DIR:-}" && ! -d "$DATA" && -d "$HOME/.local/share/hansolo" ]]; then
  DATA="$HOME/.local/share/hansolo"
  echo "notice: using legacy default data directory $DATA (set BERTH_DATA_DIR to choose explicitly)" >&2
fi
NS="${BERTH_NAMESPACE:-${HAN_SOLO_NAMESPACE:-berth}}"
CONTEXT="${BERTH_CONTEXT:-${HAN_SOLO_CONTEXT:-}}"
PORT="${BERTH_ENTRY_PORT:-${HAN_SOLO_ENTRY_PORT:-18765}}"
SCAN_TIMEOUT="${BERTH_SCAN_TIMEOUT:-${HAN_SOLO_SCAN_TIMEOUT:-300}}"
PYTHON="${PYBIN:-python3}"
POD_INGEST="/app/ingest"

usage() {
  cat <<'EOF'
Usage: scripts/add-ingest-folder.sh <folder-name> [--from <source-dir>] [--name <source display name>]
                                    [--workspace <workspace-id>] [--no-register]

Creates "$BERTH_DATA_DIR/ingest/<folder-name>" on the host. The pods see it immediately as
/app/ingest/<folder-name>; no restart, rollout, PVC or Deployment change is involved.

  <folder-name>       one path component: ^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$
  --from <dir>        copy the content of <dir> into the folder; existing files are never overwritten,
                      symlinks that point outside <dir> are skipped (the folder picker ignores symlinks anyway).
                      Copied files are made world-readable (o+r) and folders o+rx: the pods run as uid 10001.
  --name <name>       display name of the source (default: <folder-name>)
  --workspace <id>    also link the source to this workspace (the current source list is kept and merged)
  --no-register       only create/copy the folder; do not call the API
  -h, --help          show this help

Unless --no-register is given, the script logs in as the admin user, registers /app/ingest/<folder-name> as a
source (auto_scan off), starts a scan and waits for it (default 300 s).

Environment (same as ops/linux.sh; the HAN_SOLO_* names are accepted as deprecated aliases, BERTH_* wins):
  BERTH_DATA_DIR           data directory        (default: ~/.local/share/berth; an existing
                           ~/.local/share/hansolo from an older install is used when the berth dir is absent)
  BERTH_ENTRY_PORT         local entry port      (default: 18765; needs './ops/linux.sh connect' or 'serve')
  BERTH_CONTEXT / BERTH_NAMESPACE   only used to print read-only Deployment generations as evidence
  CYNOVELA_ADMIN_USERNAME  admin user            (default: cynovela); password is read from
                           "$BERTH_DATA_DIR/secrets/admin_password"
  BERTH_SCAN_TIMEOUT       seconds to wait for the scan (default: 300)

Exit codes: 0 ok, 1 runtime failure, 2 usage error.
EOF
}

die_usage() { echo "Error: $1" >&2; echo "Try: scripts/add-ingest-folder.sh --help" >&2; exit 2; }

FOLDER=""; FROM=""; NAME=""; WORKSPACE=""; REGISTER=1
while [[ $# -gt 0 ]]; do
  case "$1" in
    -h|--help) usage; exit 0 ;;
    --from) [[ $# -ge 2 ]] || die_usage "--from needs a directory"; FROM="$2"; shift 2 ;;
    --name) [[ $# -ge 2 ]] || die_usage "--name needs a value"; NAME="$2"; shift 2 ;;
    --workspace) [[ $# -ge 2 ]] || die_usage "--workspace needs a workspace id"; WORKSPACE="$2"; shift 2 ;;
    --no-register) REGISTER=0; shift ;;
    --) shift; [[ $# -eq 0 ]] || { [[ -z "$FOLDER" ]] || die_usage "only one folder name is allowed"; FOLDER="$1"; shift; } ;;
    -*) die_usage "unknown option: $1" ;;
    *) [[ -z "$FOLDER" ]] || die_usage "only one folder name is allowed"; FOLDER="$1"; shift ;;
  esac
done

[[ -n "$FOLDER" ]] || die_usage "<folder-name> is required"
[[ "$FOLDER" =~ ^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$ ]] ||
  die_usage "invalid folder name '$FOLDER' (one path component, ^[A-Za-z0-9][A-Za-z0-9._-]{0,63}\$; no '/', no '..', no leading dot)"
[[ "$FOLDER" != *..* ]] || die_usage "invalid folder name '$FOLDER' ('..' is not allowed)"
[[ -z "$FROM" || -d "$FROM" ]] || die_usage "--from '$FROM' is not an existing directory"
[[ -n "$NAME" ]] || NAME="$FOLDER"
[[ -z "$WORKSPACE" || "$WORKSPACE" =~ ^[A-Za-z0-9_-]{1,128}$ ]] || die_usage "invalid workspace id '$WORKSPACE'"
[[ "$PORT" =~ ^[0-9]{1,5}$ ]] || die_usage "invalid BERTH_ENTRY_PORT '$PORT'"

INGEST="$DATA/ingest"
[[ -d "$INGEST" ]] || {
  echo "Error: $INGEST does not exist. Install the runtime first (./ops/linux.sh install) or set BERTH_DATA_DIR." >&2
  exit 1
}
command -v "$PYTHON" >/dev/null 2>&1 || { echo "Error: $PYTHON not found (set PYBIN)" >&2; exit 1; }
TARGET="$INGEST/$FOLDER"
[[ ! -e "$TARGET" || -d "$TARGET" ]] || { echo "Error: $TARGET exists and is not a directory" >&2; exit 1; }
[[ ! -L "$TARGET" ]] || { echo "Error: $TARGET is a symlink; refusing to use it" >&2; exit 1; }

# Read-only evidence that nothing in Kubernetes changes: Deployment generations before and after.
generations() {
  kubectl --context "$CONTEXT" -n "$NS" get deployment \
    -o jsonpath='{range .items[*]}{.metadata.name}={.metadata.generation}{"\n"}{end}' 2>/dev/null
}
GEN_BEFORE=""; GEN_CHECK=0
if [[ -n "$CONTEXT" ]] && command -v kubectl >/dev/null 2>&1; then
  if GEN_BEFORE="$(generations)" && [[ -n "$GEN_BEFORE" ]]; then GEN_CHECK=1; fi
fi
if [[ "$GEN_CHECK" -eq 1 ]]; then
  echo "Deployment generations before:"; printf '%s\n' "$GEN_BEFORE" | sed 's/^/  /'
else
  echo "Note: kubectl/BERTH_CONTEXT not usable; skipping the Deployment generation check (the script changes no Kubernetes object either way)."
fi
check_generations() {
  [[ "$GEN_CHECK" -eq 1 ]] || return 0
  local after; after="$(generations)" || { echo "Note: could not re-read Deployment generations." >&2; return 0; }
  echo "Deployment generations after:"; printf '%s\n' "$after" | sed 's/^/  /'
  if [[ "$after" == "$GEN_BEFORE" ]]; then echo "Deployment generations unchanged: PASS (no rollout)"
  else echo "Error: Deployment generations changed while this script ran (not caused by this script; check other activity)" >&2; return 1; fi
}

if [[ -d "$TARGET" ]]; then echo "Folder already exists: $TARGET"; else mkdir -p "$TARGET"; echo "Folder created: $TARGET"; fi
chmod o+rx "$TARGET"   # the pods run as the non-root uid 10001 on a read-only hostPath: world-readable is required
echo "Visible in the pods as: $POD_INGEST/$FOLDER (read-only hostPath; no restart needed)"

if [[ -n "$FROM" ]]; then
  "$PYTHON" - "$FROM" "$TARGET" <<'PY'
import os, shutil, sys
src, dst = os.path.realpath(sys.argv[1]), sys.argv[2]
if os.path.commonpath([src, os.path.realpath(dst)]) == src:
    sys.exit(f"Error: the target folder is inside --from ({src}); refusing to copy")
copied = existing = links_out = special = 0
for root, dirs, files in os.walk(src, followlinks=False):
    rel = os.path.relpath(root, src)
    out = dst if rel == "." else os.path.join(dst, rel)
    os.makedirs(out, exist_ok=True)
    os.chmod(out, (os.stat(out).st_mode & 0o777) | 0o055)   # pods run as uid 10001: dirs o+rx
    for d in list(dirs):                      # symlinked directories are listed in dirs but never descended into
        if os.path.islink(os.path.join(root, d)):
            dirs.remove(d); files.append(d)
    for f in files:
        s, t = os.path.join(root, f), os.path.join(out, f)
        if os.path.lexists(t):
            existing += 1; continue
        if os.path.islink(s):
            real = os.path.realpath(s)
            if real != src and not real.startswith(src + os.sep):
                links_out += 1; continue       # never carry a link that points outside the copied tree
            os.symlink(os.readlink(s), t); copied += 1; continue
        if not os.path.isfile(s):
            special += 1; continue             # sockets, devices, fifos
        shutil.copy2(s, t); copied += 1
        os.chmod(t, (os.stat(t).st_mode & 0o777) | 0o044)  # copy2 keeps the source mode: force o+r
print(f"Copied {copied} file(s) from {src} (skipped: {existing} already present, "
      f"{links_out} symlink(s) pointing outside, {special} special file(s))")
PY
fi

if [[ "$REGISTER" -eq 0 ]]; then
  echo "--no-register: not calling the API. Register later in the GUI (Add source -> folder picker) or rerun without --no-register."
  check_generations
  exit 0
fi

# The password and the token live only inside this python process: never in argv, the environment, traces or output.
set +e
"$PYTHON" - "$PORT" "$DATA/secrets/admin_password" "$POD_INGEST/$FOLDER" "$NAME" "$WORKSPACE" \
  "${CYNOVELA_ADMIN_USERNAME:-cynovela}" "$SCAN_TIMEOUT" <<'PY'
import json, sys, time, urllib.error, urllib.request
port, pw_file, path, name, workspace, user, timeout_s = sys.argv[1:8]
base = f"http://127.0.0.1:{port}"
token = ""

def call(method, url, body=None, timeout=30):
    data = None if body is None else json.dumps(body).encode()
    req = urllib.request.Request(base + url, data=data, method=method)
    req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("Authorization", "Bearer " + token)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            code, raw = r.status, r.read()
    except urllib.error.HTTPError as e:
        code, raw = e.code, e.read()
    except (urllib.error.URLError, OSError) as e:
        sys.exit(f"Error: cannot reach {base} ({getattr(e, 'reason', e)}). Is './ops/linux.sh connect' (or 'serve') running?")
    try:
        return code, json.loads(raw.decode("utf-8", "replace") or "null")
    except ValueError:
        return code, None

def detail(d):
    return d.get("detail") if isinstance(d, dict) and "detail" in d else d

try:
    with open(pw_file, encoding="utf-8") as fh:
        password = fh.read().strip()
except OSError as e:
    sys.exit(f"Error: cannot read {pw_file} ({e.strerror}). The folder is ready; register it in the GUI instead.")

code, d = call("POST", "/api/auth/login", {"username": user, "password": password})
password = ""
if code == 401:
    sys.exit(f"Error: login as '{user}' was rejected (401). {pw_file} may be out of sync with the password set in the GUI.\n"
             "       Update that file (or set CYNOVELA_ADMIN_USERNAME), or register the folder in the GUI.")
if code != 200 or not isinstance(d, dict) or not d.get("access_token"):
    sys.exit(f"Error: login failed (HTTP {code}): {detail(d)}")
token = d["access_token"]

def find_by_path():
    offset = 0
    while True:
        c, page = call("GET", f"/api/sources?limit=100&offset={offset}")   # limit must be 10/20/50/100
        if c != 200 or not isinstance(page, dict):
            return None
        items = page.get("items") or []
        for s in items:
            if isinstance(s, dict) and (s.get("path") or "").rstrip("/") == path:
                return s
        offset += 100
        if not items or offset >= int(page.get("total") or 0):
            return None

def link(sid):
    if not workspace:
        return
    c, ws = call("GET", f"/api/workspaces/{workspace}")
    if c != 200 or not isinstance(ws, dict) or not isinstance(ws.get("source_ids"), list):
        sys.exit(f"Error: cannot read workspace {workspace} (HTTP {c}): {detail(ws)}. Source {sid} is registered but NOT linked.")
    current = list(dict.fromkeys(ws["source_ids"]))
    if sid in current:
        print(f"Workspace {workspace}: source already linked")
        return
    # PATCH replaces the whole list, so send the current ids plus the new one.
    c, r = call("PATCH", f"/api/workspaces/{workspace}", {"source_ids": current + [sid]})
    if c != 200:
        sys.exit(f"Error: linking to workspace {workspace} failed (HTTP {c}): {detail(r)}")
    print(f"Workspace {workspace}: linked ({len(current)} -> {len(current) + 1} sources)")

code, d = call("POST", "/api/sources", {"name": name, "path": path, "auto_scan": False})
if code == 409:
    existing = find_by_path()
    if not existing:
        sys.exit(f"Error: the API refused the source (409): {detail(d)}\n"
                 "       No source with this path exists, so the display name is probably taken; retry with --name.")
    print(f"Already registered: {path} is source id={existing.get('id')} name={existing.get('name')} "
          f"status={existing.get('status')} file_count={existing.get('file_count')}. Nothing to do "
          "(rescan it from the GUI or with 'cynovela_cli.py scan start').")
    link(existing.get("id"))
    sys.exit(0)
if code != 200 or not isinstance(d, dict) or not d.get("id"):
    sys.exit(f"Error: registering the source failed (HTTP {code}): {detail(d)}")
sid = d["id"]
print(f"Source registered: id={sid} name={d.get('name')} path={d.get('path')}")
link(sid)

code, d = call("POST", f"/api/sources/{sid}/scan/async", {})
if code != 200:
    sys.exit(f"Error: starting the scan failed (HTTP {code}): {detail(d)}. Source id={sid} stays registered.")
print("Scan started; waiting ...")
deadline = time.time() + float(timeout_s)
src = {}
while time.time() < deadline:
    time.sleep(2)
    code, src = call("GET", f"/api/sources/{sid}", timeout=15)
    if code == 200 and isinstance(src, dict) and src.get("status") in ("completed", "failed", "canceled", "idle"):
        print(f"Result: source_id={sid} status={src.get('status')} file_count={src.get('file_count')}")
        sys.exit(0 if src.get("status") in ("completed", "idle") else 1)
last = src.get("status") if isinstance(src, dict) else None
sys.exit(f"Error: scan did not finish within {timeout_s} s (source_id={sid}, last status={last}). "
         f"It keeps running; check GET /api/sources/{sid}.")
PY
RC=$?
set -e
if [[ "$RC" -ne 0 ]]; then
  echo "Note: the folder $TARGET was created/filled before this failure and stays in place; only the API step failed." >&2
  check_generations || true
  exit 1
fi
check_generations
echo "Done. The folder also appears in the folder picker of 'Add source' and Quick Start."
