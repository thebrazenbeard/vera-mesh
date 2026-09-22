#!/bin/sh
# Read-only VeraMesh/Synology inspection probe.
# This script intentionally performs no installs, service changes, file writes,
# firewall/Tailscale mutations, credential reads, or package-state changes.
set -u

json_escape() {
  printf '%s' "$1" | sed 's/\\/\\\\/g; s/"/\\"/g; s/	/\\t/g; s//\\r/g; s/
/\\n/g'
}

emit_string() {
  key=$1
  value=$2
  printf '"%s":"%s"' "$key" "$(json_escape "$value")"
}

command_value() {
  if command -v "$1" >/dev/null 2>&1; then
    "$@" 2>/dev/null | head -n 1
  fi
}

file_sha256() {
  target=$1
  if [ ! -f "$target" ]; then
    return 0
  fi
  if command -v sha256sum >/dev/null 2>&1; then
    sha256sum "$target" 2>/dev/null | awk '{print $1}'
  elif command -v openssl >/dev/null 2>&1; then
    openssl dgst -sha256 "$target" 2>/dev/null | awk '{print $NF}'
  fi
}

pkg_status=""
if command -v synopkg >/dev/null 2>&1; then
  pkg_status=$(synopkg status VeraMesh 2>/dev/null | tr '\n' ' ' | sed 's/[[:space:]][[:space:]]*/ /g')
fi

target="/var/packages/VeraMesh/target"
gateway_candidates="
$target/bin/veramesh-gateway
$target/gateway/veramesh-gateway
$target/bin/veramesh-go-gateway
"
gateway_path=""
for candidate in $gateway_candidates; do
  if [ -f "$candidate" ]; then
    gateway_path=$candidate
    break
  fi
done

python_version=""
for py in python3.11 python3 python; do
  if command -v "$py" >/dev/null 2>&1; then
    python_version=$("$py" --version 2>&1 | head -n 1)
    break
  fi
done

node_version=""
if command -v node >/dev/null 2>&1; then
  node_version=$(node --version 2>/dev/null | head -n 1)
fi

listen_lines=""
if command -v ss >/dev/null 2>&1; then
  listen_lines=$(ss -lnt 2>/dev/null | awk 'NR==1 || /:1744[3-9][[:space:]]/ {print}' | tr '\n' ';')
elif command -v netstat >/dev/null 2>&1; then
  listen_lines=$(netstat -lnt 2>/dev/null | awk 'NR<=2 || /:1744[3-9][[:space:]]/ {print}' | tr '\n' ';')
fi

process_lines=$(ps 2>/dev/null | grep -E '[v]eramesh|[v]eraport|[v]erarelay' | tr '\n' ';')

dsm_version=""
if [ -r /etc.defaults/VERSION ]; then
  dsm_version=$(awk -F= '
    $1=="productversion" || $1=="buildnumber" || $1=="smallfixnumber" {
      gsub(/"/,"",$2); printf "%s=%s ",$1,$2
    }' /etc.defaults/VERSION)
fi

gateway_sha=""
gateway_version=""
if [ -n "$gateway_path" ]; then
  gateway_sha=$(file_sha256 "$gateway_path")
  if [ -x "$gateway_path" ]; then
    gateway_version=$("$gateway_path" -version 2>/dev/null | head -n 1)
  fi
fi

printf '{'
emit_string schema "VERAMESH_SYNOLOGY_READONLY_INSPECTION_V1"; printf ','
emit_string observed_at_utc "$(date -u '+%Y-%m-%dT%H:%M:%SZ' 2>/dev/null)"; printf ','
emit_string hostname "$(hostname 2>/dev/null)"; printf ','
emit_string kernel "$(uname -srm 2>/dev/null)"; printf ','
emit_string machine "$(uname -m 2>/dev/null)"; printf ','
emit_string identity "$(id 2>/dev/null)"; printf ','
emit_string dsm_version "$dsm_version"; printf ','
emit_string veramesh_package_status "$pkg_status"; printf ','
emit_string veramesh_target_present "$([ -d "$target" ] && printf true || printf false)"; printf ','
emit_string python_version "$python_version"; printf ','
emit_string node_version "$node_version"; printf ','
emit_string gateway_path "$gateway_path"; printf ','
emit_string gateway_sha256 "$gateway_sha"; printf ','
emit_string gateway_protocol_version "$gateway_version"; printf ','
emit_string relevant_listeners "$listen_lines"; printf ','
emit_string relevant_processes "$process_lines"
printf '}\n'
