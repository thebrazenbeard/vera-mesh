#!/bin/sh
# Read-only follow-up probe for the installed VeraMesh/Tailscale/VeraRelay runtime.
# No install/start/stop/restart/network mutation/credential read is performed.
set -u

TARGET=/var/packages/VeraMesh/target
VAR=/var/packages/VeraMesh/var/vera
RUN=/var/packages/VeraMesh/tmp/run
PY=/var/packages/python311/target/bin/python3.11

sha256_file() {
  p=$1
  if [ ! -f "$p" ]; then
    printf '%s' "MISSING"
    return
  fi
  if command -v sha256sum >/dev/null 2>&1; then
    sha256sum "$p" 2>/dev/null | awk '{print $1}'
    return
  fi
  if command -v openssl >/dev/null 2>&1; then
    openssl dgst -sha256 "$p" 2>/dev/null | awk '{print $NF}'
    return
  fi
  printf '%s' "UNAVAILABLE"
}

one_line() {
  tr '\n' ' ' | sed 's/[[:space:]][[:space:]]*/ /g'
}

echo "schema=VERAMESH_SYNOLOGY_RUNTIME_OWNER_PROBE_V1"
echo "observed_at_utc=$(date -u '+%Y-%m-%dT%H:%M:%SZ' 2>/dev/null)"
echo "hostname=$(hostname 2>/dev/null)"
echo "machine=$(uname -m 2>/dev/null)"

echo "--- package ---"
if command -v synopkg >/dev/null 2>&1; then
  printf 'synopkg_status='
  synopkg status VeraMesh 2>/dev/null | one_line
  echo
fi
for p in   "$TARGET/bin/veramesh_edge.py"   "$TARGET/bin/veramesh_lifecycle.py"   "$TARGET/bin/veramesh_start_readiness.py"   "$TARGET/bin/veramesh_state.py"   "/var/packages/VeraMesh/conf/systemd/pkguser-veramesh.service"
do
  echo "file=$p sha256=$(sha256_file "$p")"
  if [ -e "$p" ] || [ -L "$p" ]; then
    ls -ld "$p" 2>/dev/null | sed 's/^/stat=/'
  fi
done

echo "--- package-unit ---"
if [ -x /usr/syno/bin/synosystemctl ]; then
  /usr/syno/bin/synosystemctl status pkguser-veramesh.service 2>&1 | head -n 40
else
  echo "synosystemctl=UNAVAILABLE"
fi

echo "--- lifecycle-status ---"
if [ -x "$PY" ] && [ -f "$TARGET/bin/veramesh_lifecycle.py" ]; then
  "$PY" "$TARGET/bin/veramesh_lifecycle.py" status >/tmp/veramesh-probe-status.$$ 2>&1
  rc=$?
  printf 'lifecycle_status_rc=%s output=' "$rc"
  cat /tmp/veramesh-probe-status.$$ 2>/dev/null | one_line
  echo
  rm -f /tmp/veramesh-probe-status.$$
else
  echo "lifecycle_status=UNAVAILABLE"
fi

echo "--- control-socket ---"
if [ -S "$RUN/control.sock" ]; then
  echo "control_socket=SOCKET_PRESENT"
  if [ -x "$PY" ]; then
    "$PY" - "$RUN/control.sock" <<'PY'
import json, socket, sys
path=sys.argv[1]
try:
    s=socket.socket(socket.AF_UNIX,socket.SOCK_STREAM)
    s.settimeout(1.0)
    s.connect(path)
    s.sendall(b'{"op":"status"}\n')
    data=b""
    while b"\n" not in data and len(data) <= 4096:
        chunk=s.recv(1024)
        if not chunk:
            break
        data += chunk
    s.close()
    line=data.split(b"\n",1)[0]
    obj=json.loads(line.decode("utf-8"))
    print("control_status="+json.dumps(obj,sort_keys=True,separators=(",",":")))
except Exception as exc:
    print("control_status_error="+type(exc).__name__+":"+str(exc))
PY
  fi
else
  echo "control_socket=ABSENT"
fi

echo "--- edge-config-nonsecret ---"
if [ -x "$PY" ] && [ -r "$VAR/edge-config.json" ]; then
  "$PY" - "$VAR/edge-config.json" <<'PY'
import json, sys
try:
    with open(sys.argv[1],"r",encoding="utf-8") as f:
        v=json.load(f)
    keep={k:v.get(k) for k in (
        "schema","listen_host","listen_port","target_host","target_port",
        "connect_timeout_s","idle_timeout_s","max_connections"
    )}
    print("edge_config="+json.dumps(keep,sort_keys=True,separators=(",",":")))
except Exception as exc:
    print("edge_config_error="+type(exc).__name__+":"+str(exc))
PY
else
  echo "edge_config=UNAVAILABLE"
fi

echo "--- lifecycle-state-nonsecret ---"
if [ -x "$PY" ] && [ -r "$VAR/lifecycle-state.json" ]; then
  "$PY" - "$VAR/lifecycle-state.json" <<'PY'
import json, sys
try:
    with open(sys.argv[1],"r",encoding="utf-8") as f:
        v=json.load(f)
    keep={k:v.get(k) for k in (
        "schema","installation_status","lifecycle_generation","lifecycle_state",
        "stopped_provenance","process_start_generation"
    )}
    print("lifecycle_state="+json.dumps(keep,sort_keys=True,separators=(",",":")))
except Exception as exc:
    print("lifecycle_state_error="+type(exc).__name__+":"+str(exc))
PY
else
  echo "lifecycle_state=UNAVAILABLE"
fi

echo "--- processes ---"
if ps -ef >/dev/null 2>&1; then
  ps -ef 2>/dev/null | grep -E '[v]eramesh|[v]eraport|[v]erarelay|[t]ailscale' || true
elif ps w >/dev/null 2>&1; then
  ps w 2>/dev/null | grep -E '[v]eramesh|[v]eraport|[v]erarelay|[t]ailscale' || true
else
  ps 2>/dev/null | grep -E '[v]eramesh|[v]eraport|[v]erarelay|[t]ailscale' || true
fi

echo "--- listeners-with-owner-if-permitted ---"
if command -v ss >/dev/null 2>&1; then
  ss -lntp 2>/dev/null | awk 'NR==1 || /:1744[3-9][[:space:]]/ {print}'
elif command -v netstat >/dev/null 2>&1; then
  netstat -lntp 2>/dev/null | awk 'NR<=2 || /:1744[3-9][[:space:]]/ {print}'
else
  echo "listener_tool=UNAVAILABLE"
fi

echo "--- tailscale-serve ---"
if command -v tailscale >/dev/null 2>&1; then
  tailscale serve status --json 2>/dev/null || tailscale serve status 2>/dev/null || echo "tailscale_serve_status=UNAVAILABLE_OR_DENIED"
else
  echo "tailscale_cli=UNAVAILABLE"
fi
