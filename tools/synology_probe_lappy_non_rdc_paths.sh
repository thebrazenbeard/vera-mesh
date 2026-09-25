#!/bin/sh
# Read-only probe for non-RDC Lappy management paths over the tailnet.
set -eu
PY=/var/packages/python311/target/bin/python3.11
HOST="${1:-100.88.50.35}"
[ -x "$PY" ] || { echo "ERROR: Python 3.11 runtime missing: $PY" >&2; exit 2; }
exec "$PY" - "$HOST" <<'PY'
import json, socket, sys, time
host=sys.argv[1]
ports={
    "ssh":22,
    "smb":445,
    "winrm_http":5985,
    "winrm_https":5986,
    "rdp":3389,
    "workbridge_default":8765,
    "veraport":17444,
}
result={}
for name,port in ports.items():
    start=time.monotonic()
    try:
        with socket.create_connection((host,port),timeout=1.5):
            result[name]={"port":port,"tcp_open":True,"connect_ms":round((time.monotonic()-start)*1000,1)}
    except OSError as exc:
        result[name]={"port":port,"tcp_open":False,"error":exc.__class__.__name__}
print(json.dumps({
    "schema":"LAPPY_NON_RDC_PATH_PROBE_V1",
    "host":host,
    "paths":result,
    "effects":{"writes":False,"services_changed":False,"rdc_used":False}
},sort_keys=True))
PY
