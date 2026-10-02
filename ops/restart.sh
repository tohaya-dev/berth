#!/usr/bin/env bash
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
"$HERE/stop.sh"
"$HERE/start.sh"
