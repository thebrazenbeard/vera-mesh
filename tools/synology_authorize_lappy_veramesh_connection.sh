#!/bin/sh
# Authorized NAS-side connection of the existing VeraMesh edge to the existing
# Lappy VeraPort endpoint. Preserves Lappy identity/service and process policy.
set -eu

TARGET_HOST="${1:-100.88.50.35}"
TARGET_PORT="${2:-17444}"

PKG=/var/packages/VeraMesh
PY=/var/packages/python311/target/bin/python3.11
VAR="$PKG/var/vera"
CFG="$VAR/edge-config.json"
LIFECYCLE="$PKG/target/bin/veramesh_lifecycle.py"
START_READINESS="$PKG/target/bin/veramesh_start_readiness.py"
CONTROL="$PKG/tmp/run/control.sock"
LOCAL_EDGE_HOST=127.0.0.1
LOCAL_EDGE_PORT=17445

die() {
  echo "ERROR: $*" >&2
  exit 1
}

[ "$(id -u)" -eq 0 ] || die "run as root (for example: curl ... | sudo sh)"
[ -x "$PY" ] || die "Python 3.11 runtime missing: $PY"
[ -f "$CFG" ] || die "edge config missing: $CFG"
[ ! -L "$CFG" ] || die "edge config is a symlink; refusing"
[ -f "$LIFECYCLE" ] || die "lifecycle helper missing"
[ -f "$START_READINESS" ] || die "start-readiness helper missing"

case "$TARGET_PORT" in
  ''|*[!0-9]*) die "target port must be numeric" ;;
esac
[ "$TARGET_PORT" -ge 1 ] && [ "$TARGET_PORT" -le 65535 ] || die "target port outside 1..65535"

CURRENT=$("$PY" - "$CFG" <<'PY'
import json, os, stat, sys
path=sys.argv[1]
fd=os.open(path, os.O_RDONLY|os.O_NOFOLLOW)
try:
    st=os.fstat(fd)
    if not stat.S_ISREG(st.st_mode):
        raise SystemExit("edge config is not a regular file")
    if stat.S_IMODE(st.st_mode) != 0o600:
        raise SystemExit("edge config mode is not 0600")
    value=json.loads(os.read(fd, 8193).decode("utf-8"))
finally:
    os.close(fd)
if value.get("schema") != "VERA_MESH_EDGE_CONFIG_V1":
    raise SystemExit("wrong edge config schema")
print(str(value.get("target_host",""))+"\t"+str(value.get("target_port","")))
PY
)

CURRENT_HOST=$(printf '%s' "$CURRENT" | awk -F '	' '{print $1}')
CURRENT_PORT=$(printf '%s' "$CURRENT" | awk -F '	' '{print $2}')
CHANGED=false
BACKUP=""

if [ "$CURRENT_HOST" != "$TARGET_HOST" ] || [ "$CURRENT_PORT" != "$TARGET_PORT" ]; then
  echo "Retargeting VeraMesh edge: $CURRENT_HOST:$CURRENT_PORT -> $TARGET_HOST:$TARGET_PORT" >&2

  "$PY" "$LIFECYCLE" stop || die "lifecycle-safe stop failed; config unchanged"

  BACKUP="$VAR/edge-config.pre-lappy-connect.$(date -u '+%Y%m%dT%H%M%SZ').json"
  cp -p "$CFG" "$BACKUP"

  if ! "$PY" - "$CFG" "$TARGET_HOST" "$TARGET_PORT" <<'PY'
import json, os, stat, sys, tempfile
path, host, port = sys.argv[1], sys.argv[2], int(sys.argv[3])
fd=os.open(path, os.O_RDONLY|os.O_NOFOLLOW)
try:
    st=os.fstat(fd)
    raw=b""
    while True:
        chunk=os.read(fd,4096)
        if not chunk: break
        raw += chunk
        if len(raw)>8192: raise RuntimeError("edge config too large")
finally:
    os.close(fd)
value=json.loads(raw.decode("utf-8"))
expected={
    "schema","listen_host","listen_port","target_host","target_port",
    "connect_timeout_s","idle_timeout_s","max_connections"
}
if set(value) != expected or value.get("schema") != "VERA_MESH_EDGE_CONFIG_V1":
    raise RuntimeError("edge config shape mismatch")
value["target_host"]=host
value["target_port"]=port
data=(json.dumps(value,sort_keys=True,separators=(",",":"))+"\n").encode("utf-8")
directory=os.path.dirname(path)
tmpfd,tmp=tempfile.mkstemp(prefix=".edge-config.",suffix=".tmp",dir=directory)
try:
    os.fchmod(tmpfd,0o600)
    os.fchown(tmpfd,st.st_uid,st.st_gid)
    os.write(tmpfd,data)
    os.fsync(tmpfd)
finally:
    os.close(tmpfd)
os.replace(tmp,path)
dirfd=os.open(directory,os.O_RDONLY)
try: os.fsync(dirfd)
finally: os.close(dirfd)
PY
  then
    cp -p "$BACKUP" "$CFG"
    "$PY" "$START_READINESS" start >/dev/null 2>&1 || true
    die "atomic edge-config update failed; prior config restored"
  fi

  if ! "$PY" "$START_READINESS" start; then
    echo "New target failed qualified startup; restoring prior config." >&2
    cp -p "$BACKUP" "$CFG"
    "$PY" "$START_READINESS" start || die "rollback restored config but restart also failed"
    die "Lappy target activation failed and was rolled back"
  fi
  CHANGED=true
fi

"$PY" - "$CFG" "$CONTROL" "$LOCAL_EDGE_HOST" "$LOCAL_EDGE_PORT" "$TARGET_HOST" "$TARGET_PORT" "$CHANGED" <<'PY'
import hashlib, json, os, socket, ssl, sys, time

cfg_path, control, edge_host, edge_port, target_host, target_port, changed = sys.argv[1:]
edge_port=int(edge_port); target_port=int(target_port)

with open(cfg_path,"r",encoding="utf-8") as f:
    cfg=json.load(f)

def tcp_ok(host,port):
    try:
        with socket.create_connection((host,port),timeout=3):
            return True
    except OSError:
        return False

def cert_fp(host,port):
    ctx=ssl.create_default_context()
    ctx.check_hostname=False
    ctx.verify_mode=ssl.CERT_NONE
    with socket.create_connection((host,port),timeout=5) as raw:
        with ctx.wrap_socket(raw,server_hostname="veraport.local") as tls:
            der=tls.getpeercert(binary_form=True)
            return hashlib.sha256(der).hexdigest(), tls.version()

control_status=None
control_error=None
try:
    s=socket.socket(socket.AF_UNIX,socket.SOCK_STREAM)
    s.settimeout(2)
    s.connect(control)
    s.sendall(b'{"op":"status"}\n')
    data=b""
    while b"\n" not in data and len(data)<=4096:
        chunk=s.recv(1024)
        if not chunk: break
        data+=chunk
    s.close()
    control_status=json.loads(data.split(b"\n",1)[0].decode("utf-8"))
except Exception as exc:
    control_error=f"{type(exc).__name__}:{exc}"

direct_tcp=tcp_ok(target_host,target_port)
edge_tcp=tcp_ok(edge_host,edge_port)
direct_fp=edge_fp=None
direct_tls=edge_tls=None
tls_error=None
if direct_tcp and edge_tcp:
    try:
        direct_fp,direct_tls=cert_fp(target_host,target_port)
        edge_fp,edge_tls=cert_fp(edge_host,edge_port)
    except Exception as exc:
        tls_error=f"{type(exc).__name__}:{exc}"

result={
    "schema":"VERAMESH_LAPPY_CONNECTION_QUALIFICATION_V1",
    "observed_at_utc":time.strftime("%Y-%m-%dT%H:%M:%SZ",time.gmtime()),
    "changed":changed.lower()=="true",
    "config_target":{"host":cfg.get("target_host"),"port":cfg.get("target_port")},
    "expected_target":{"host":target_host,"port":target_port},
    "control_status":control_status,
    "control_error":control_error,
    "direct_target_tcp":direct_tcp,
    "local_edge_tcp":edge_tcp,
    "direct_tls_version":direct_tls,
    "edge_tls_version":edge_tls,
    "direct_cert_sha256":direct_fp,
    "edge_cert_sha256":edge_fp,
    "tls_error":tls_error,
    "certificate_match":bool(direct_fp and edge_fp and direct_fp==edge_fp),
    "process_execution_changed":False,
    "lappy_identity_changed":False,
}
print(json.dumps(result,sort_keys=True,separators=(",",":")))
if cfg.get("target_host")!=target_host or cfg.get("target_port")!=target_port:
    raise SystemExit(2)
if control_status is None or not direct_tcp or not edge_tcp or not result["certificate_match"]:
    raise SystemExit(3)
PY
