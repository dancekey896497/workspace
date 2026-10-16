#!/usr/bin/env bash
# Start only an isolated test server inside an existing NPU test container.
set -euo pipefail

usage() {
  cat <<'EOF'
Usage: bash run_server.sh --run-dir NEW_ABSOLUTE_PATH [options]
  --profile baseline|no-eviction  Default: baseline (customer IsolatedLRU)
  --l1-gb NUMBER                 Default: 1 (scaled experiment, not 512 GiB)
  --init-gb NUMBER               Default: 1
  --rpc-port NUMBER              Default: 15565
  --http-port NUMBER             Default: 18090
  --print-only                   Print arguments without starting a server
Environment: LMCACHE_BIN selects the existing CLI; PYTHON selects its interpreter.
No image pull, source patch, quota changes, deletion, or process termination.
EOF
}

run_dir=""
profile="baseline"
l1_gb="1"
init_gb="1"
rpc_port="15565"
http_port="18090"
print_only=0
while (($#)); do
  case "$1" in
    --help|-h) usage; exit 0 ;;
    --run-dir|--profile|--l1-gb|--init-gb|--rpc-port|--http-port)
      if (($# < 2)); then printf 'Missing value for %s\n' "$1" >&2; exit 2; fi
      case "$1" in
        --run-dir) run_dir="$2" ;;
        --profile) profile="$2" ;;
        --l1-gb) l1_gb="$2" ;;
        --init-gb) init_gb="$2" ;;
        --rpc-port) rpc_port="$2" ;;
        --http-port) http_port="$2" ;;
      esac
      shift 2 ;;
    --print-only) print_only=1; shift ;;
    *) printf 'Unknown argument: %s\n' "$1" >&2; exit 2 ;;
  esac
done
if [[ "$run_dir" != /* || "$run_dir" == / ]]; then
  printf '%s\n' '--run-dir must be a new absolute path under your SSD test directory.' >&2
  exit 2
fi
case "$profile" in baseline|no-eviction) ;; *) usage >&2; exit 2 ;; esac
python_bin="${PYTHON:-python3}"
cli="${LMCACHE_BIN:-lmcache}"
adapter_json="$("$python_bin" - "$run_dir/cache" "$profile" "$l1_gb" "$init_gb" "$rpc_port" "$http_port" <<'PY'
import json
import sys
base, profile, total, initial, rpc, http = sys.argv[1:]
if not (0 < float(initial) <= float(total)):
    raise SystemExit('Require 0 < init-gb <= l1-gb')
ports = (int(rpc), int(http))
if any(not 1 <= value <= 65535 for value in ports) or ports[0] == ports[1]:
    raise SystemExit('Require two different valid ports')
cfg = dict(type='fs_native', base_path=base, num_workers=4, use_odirect=True)
if profile == 'baseline':
    cfg['eviction'] = dict(eviction_policy='IsolatedLRU', trigger_watermark=0.8, eviction_ratio=0.1)
print(json.dumps(cfg, separators=(',', ':')))
PY
)"
args=(server --host 127.0.0.1 --port "$rpc_port" --http-host 127.0.0.1
  --http-port "$http_port" --chunk-size 4096 --supported-transfer-mode lmcache_driven
  --l1-size-gb "$l1_gb" --l1-init-size-gb "$init_gb" --l1-use-lazy --l1-align-bytes 4096
  --eviction-policy LRU --worker-reap-timeout-seconds 0 --l2-store-policy default
  --l2-adapter "$adapter_json")
printf 'PROFILE=%s L1_GiB=%s INIT_GiB=%s (customer: 512 GiB / default 20 GiB)\n' "$profile" "$l1_gb" "$init_gb"
printf 'Command: %q ' "$cli"
printf '%q ' "${args[@]}"
printf '\n'
if ((print_only)); then exit 0; fi
command -v "$cli" >/dev/null
"$python_bin" - "$rpc_port" "$http_port" <<'PY'
import socket
import sys
for port in map(int, sys.argv[1:]):
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', port))
PY
# mkdir fails if the directory exists, so an existing cache is never reused.
mkdir -- "$run_dir"
mkdir -- "$run_dir/cache"
printf '%s\n' "$adapter_json" > "$run_dir/adapter.json"
printf '%q ' "$cli" "${args[@]}" > "$run_dir/command.txt"
printf '\n' >> "$run_dir/command.txt"
printf '%s\n' "$$" > "$run_dir/server.pid"
printf 'Server log: %s/server.log\n' "$run_dir"
export LMCACHE_LOG_LEVEL=DEBUG
exec "$cli" "${args[@]}" > "$run_dir/server.log" 2>&1
