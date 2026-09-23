#!/usr/bin/env bash
# Dedicated single-node Linux reference substrate. Does not install a container desktop.
set -euo pipefail
umask 077
# BERTH_* is the primary name; HAN_SOLO_* is still honoured as a deprecated alias (BERTH_* wins when both are set).
DATA="${BERTH_DATA_DIR:-${HAN_SOLO_DATA_DIR:-}}"
CONTEXT="${BERTH_CONTEXT:-${HAN_SOLO_CONTEXT:-}}"
: "${DATA:?Set BERTH_DATA_DIR (deprecated alias: HAN_SOLO_DATA_DIR) to a dedicated Linux data directory}"
: "${CONTEXT:?Set BERTH_CONTEXT (deprecated alias: HAN_SOLO_CONTEXT) to a dedicated name}"
VERSION="${K3S_VERSION:-v1.35.5+k3s1}"
PORT="${BERTH_KUBE_PORT:-${HAN_SOLO_KUBE_PORT:-26443}}"
[[ "$CONTEXT" =~ ^(hansolo|berth)-[a-z0-9-]+$ ]] || { echo "Use a dedicated berth-* context (legacy hansolo-* is still accepted)"; exit 2; }
DATA="$(realpath -m "$DATA")"
[[ "$DATA" != /mnt/* && "$DATA" != / && "$DATA" != "$HOME" ]] || exit 2
[[ "$(uname -m)" = x86_64 ]] || { echo "This reference installer was validated on amd64 only"; exit 2; }
[[ "$(ps -p 1 -o comm=)" = systemd ]] || { echo "Enable systemd in the dedicated WSL distribution"; exit 2; }
sudo -n true
# Refuse any existing K3s installation: never repurpose another cluster or shared binary.
if [[ -e /usr/local/bin/k3s ]] || systemctl list-unit-files 'k3s*' --no-legend | grep -q k3s; then
  echo "K3s already exists. Reuse the explicitly configured context; this installer will not overwrite it."
  exit 2
fi
mkdir -p "$DATA/k3s/agent/etc/kubelet.conf.d"
if [[ "$(stat -fc %T /sys/fs/cgroup)" != cgroup2fs ]]; then
  cat > "$DATA/k3s/agent/etc/kubelet.conf.d/10-wsl-cgroup-v1.conf" <<'YAML'
apiVersion: kubelet.config.k8s.io/v1beta1
kind: KubeletConfiguration
failCgroupV1: false
YAML
fi
curl -fsSL https://get.k3s.io -o "$DATA/install-k3s.sh"
# Official installer validates the release binary against its SHA256 manifest.
sudo env INSTALL_K3S_NAME="$CONTEXT" INSTALL_K3S_VERSION="$VERSION" INSTALL_K3S_SKIP_START=true \
  sh "$DATA/install-k3s.sh" server --data-dir "$DATA/k3s" --node-name "$CONTEXT" \
  --https-listen-port "$PORT" --bind-address 0.0.0.0 \
  --write-kubeconfig "$DATA/kubeconfig" --write-kubeconfig-mode 600 \
  --default-local-storage-path "$DATA/volumes" --disable traefik --disable servicelb
UNIT="k3s-$CONTEXT"
sudo mkdir -p "/etc/systemd/system/$UNIT.service.d"
printf '[Service]\nEnvironment=GODEBUG=netdns=go\n' | sudo tee "/etc/systemd/system/$UNIT.service.d/10-dns.conf" >/dev/null
sudo systemctl daemon-reload
sudo systemctl start "$UNIT"
for attempt in $(seq 1 90); do
  if sudo test -s "$DATA/kubeconfig"; then break; fi
  sleep 2
done
sudo cat "$DATA/kubeconfig" > "$DATA/client.yaml"
chmod 600 "$DATA/client.yaml"
export KUBECONFIG="$DATA/client.yaml"
kubectl config rename-context default "$CONTEXT"
kubectl --context "$CONTEXT" wait --for=condition=Ready node/"$CONTEXT" --timeout=300s
echo "Use KUBECONFIG=$DATA/client.yaml and BERTH_CONTEXT=$CONTEXT"
