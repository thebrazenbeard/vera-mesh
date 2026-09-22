#!/bin/sh
# Establish a fresh authenticated VeraPort application session from TheSimsVault
# to Lappy through the existing VeraMesh edge. Does not use RDC and performs no
# configuration, identity, service, firewall, Tailscale, or process-policy mutation.
#
# Runtime dependencies: Synology Python 3.11 standard library + OpenSSL CLI.
set -eu

PY=/var/packages/python311/target/bin/python3.11
EDGE_HOST="${VERAMESH_EDGE_HOST:-127.0.0.1}"
EDGE_PORT="${VERAMESH_EDGE_PORT:-17445}"
EXPECTED_TLS_SHA256="${VERAPORT_TLS_SHA256:-5231599cb289eac7d383a32078311fa5899debe8e10b002e2e62ffabf8621e2a}"
EXPECTED_CONTROLLER_KEY_SHA256="${VERAPORT_CONTROLLER_KEY_SHA256:-a06bc020f1617c9cabe4839a88d5b1614a76b75d520af5a44627e34faa03f8d8}"
REQUESTED_CAPS="${VERAPORT_REQUESTED_CAPABILITIES:-fs.read,fs.write}"

[ -x "$PY" ] || { echo "ERROR: Python 3.11 runtime missing: $PY" >&2; exit 2; }

exec "$PY" - "$EDGE_HOST" "$EDGE_PORT" "$EXPECTED_TLS_SHA256" "$EXPECTED_CONTROLLER_KEY_SHA256" "$REQUESTED_CAPS" <<'PY'
from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import os
from pathlib import Path
import shutil
import ssl
import struct
import subprocess
import sys
import tempfile
import time
import uuid

EDGE_HOST=sys.argv[1]
EDGE_PORT=int(sys.argv[2])
EXPECTED_TLS_SHA256=sys.argv[3].lower()
EXPECTED_CONTROLLER_KEY_SHA256=sys.argv[4].lower()
REQUESTED_CAPS=tuple(sorted({x.strip() for x in sys.argv[5].split(",") if x.strip()}))
MAX_FRAME=1_048_576
ALPN="veraport/1"

SEARCH_ROOTS=[
    Path("/var/packages/VeraMesh"),
    Path("/volume1/@appdata/VeraMesh"),
    Path("/volume1/homes/psims85/.veramesh"),
    Path("/volume1/homes/psims85/VeraMesh"),
    Path("/volume1/homes/psims85"),
    Path("/root/.veramesh"),
    Path("/root/VeraMesh"),
]
KEY_NAMES={
    "vera-controller-bootstrap.pem",
    "controller-key.pem",
    "controller.pem",
}

def blocked(code: str, **extra) -> None:
    payload={
        "schema":"VERAPORT_NAS_LAPPY_CONNECT_V2",
        "status":"BLOCKED",
        "code":code,
        "effects":{
            "writes":False,
            "services_changed":False,
            "identity_changed":False,
            "process_execution_changed":False,
            "rdc_used":False,
        },
    }
    payload.update(extra)
    print(json.dumps(payload,sort_keys=True))

def b64u(v: bytes) -> str:
    return base64.urlsafe_b64encode(v).rstrip(b"=").decode("ascii")

def b64ud(s: str) -> bytes:
    return base64.urlsafe_b64decode((s+"="*((4-len(s)%4)%4)).encode("ascii"))

def field(name: str, value: str) -> bytes:
    raw=value.encode("utf-8")
    return name.encode("ascii")+b"="+str(len(raw)).encode("ascii")+b":"+raw+b"\n"

def caps(values) -> str:
    return ",".join(sorted(set(values)))

def find_openssl() -> str | None:
    candidates=[
        shutil.which("openssl"),
        "/usr/bin/openssl",
        "/bin/openssl",
        "/usr/syno/bin/openssl",
        "/usr/local/bin/openssl",
    ]
    for candidate in candidates:
        if candidate and os.path.isfile(candidate) and os.access(candidate,os.X_OK):
            return candidate
    return None

OPENSSL=find_openssl()
if OPENSSL is None:
    blocked("OPENSSL_NOT_AVAILABLE")
    raise SystemExit(3)

def run_openssl(args, *, input_bytes: bytes | None=None) -> bytes:
    proc=subprocess.run(
        [OPENSSL,*args],
        input=input_bytes,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if proc.returncode!=0:
        msg=proc.stderr.decode("utf-8","replace").strip()
        raise RuntimeError("openssl "+" ".join(args[:3])+f" failed: {msg}")
    return proc.stdout

def public_der_from_pem(path: Path) -> bytes | None:
    for prefix in (["pkey","-pubin"],["pkey"]):
        proc=subprocess.run(
            [OPENSSL,*prefix,"-in",str(path),"-pubout","-outform","DER"],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            check=False,
        )
        if proc.returncode==0 and proc.stdout:
            return proc.stdout
    return None

def public_pem_from_pem(path: Path) -> bytes | None:
    for prefix in (["pkey","-pubin"],["pkey"]):
        proc=subprocess.run(
            [OPENSSL,*prefix,"-in",str(path),"-pubout","-outform","PEM"],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            check=False,
        )
        if proc.returncode==0 and proc.stdout:
            return proc.stdout
    return None

def key_id_from_der(der: bytes) -> str:
    return hashlib.sha256(der).hexdigest()

def principal_id_from_der(der: bytes, prefix: str) -> str:
    return f"{prefix}:{key_id_from_der(der)}"

def read_der_length(data: bytes, offset: int) -> tuple[int,int]:
    if offset>=len(data):
        raise ValueError("truncated DER length")
    first=data[offset]
    offset+=1
    if first<0x80:
        return first,offset
    count=first&0x7f
    if count==0 or count>4 or offset+count>len(data):
        raise ValueError("invalid DER length")
    value=int.from_bytes(data[offset:offset+count],"big")
    return value,offset+count

def der_to_p1363(der: bytes) -> bytes:
    off=0
    if len(der)<2 or der[off]!=0x30:
        raise ValueError("ECDSA signature is not DER sequence")
    off+=1
    seq_len,off=read_der_length(der,off)
    if off+seq_len!=len(der):
        raise ValueError("invalid ECDSA DER sequence length")
    values=[]
    for _ in range(2):
        if off>=len(der) or der[off]!=0x02:
            raise ValueError("ECDSA DER missing INTEGER")
        off+=1
        ilen,off=read_der_length(der,off)
        if ilen<1 or off+ilen>len(der):
            raise ValueError("invalid ECDSA DER INTEGER")
        raw=der[off:off+ilen]
        off+=ilen
        if raw[0]&0x80:
            raise ValueError("negative ECDSA DER INTEGER")
        raw=raw.lstrip(b"\x00") or b"\x00"
        if len(raw)>32:
            raise ValueError("ECDSA value exceeds P-256 width")
        values.append(raw.rjust(32,b"\x00"))
    if off!=len(der):
        raise ValueError("trailing ECDSA DER data")
    return b"".join(values)

def der_len(n: int) -> bytes:
    if n<0x80:
        return bytes([n])
    raw=n.to_bytes((n.bit_length()+7)//8,"big")
    return bytes([0x80|len(raw)])+raw

def der_int(raw: bytes) -> bytes:
    raw=raw.lstrip(b"\x00") or b"\x00"
    if raw[0]&0x80:
        raw=b"\x00"+raw
    return b"\x02"+der_len(len(raw))+raw

def p1363_to_der(raw: bytes) -> bytes:
    if len(raw)!=64:
        raise ValueError("P-256 signature must be 64 bytes")
    body=der_int(raw[:32])+der_int(raw[32:])
    return b"\x30"+der_len(len(body))+body

def sign_p1363(private_path: Path, payload: bytes) -> str:
    with tempfile.TemporaryDirectory(prefix="veraport-sign-") as td:
        payload_path=Path(td)/"payload.bin"
        sig_path=Path(td)/"sig.der"
        payload_path.write_bytes(payload)
        run_openssl(["dgst","-sha256","-sign",str(private_path),"-out",str(sig_path),str(payload_path)])
        raw=der_to_p1363(sig_path.read_bytes())
        return b64u(raw)

def verify_p1363(public_pem: bytes, sig: str, payload: bytes) -> None:
    raw=b64ud(sig)
    der=p1363_to_der(raw)
    with tempfile.TemporaryDirectory(prefix="veraport-verify-") as td:
        pub_path=Path(td)/"public.pem"
        payload_path=Path(td)/"payload.bin"
        sig_path=Path(td)/"sig.der"
        pub_path.write_bytes(public_pem)
        payload_path.write_bytes(payload)
        sig_path.write_bytes(der)
        proc=subprocess.run(
            [OPENSSL,"dgst","-sha256","-verify",str(pub_path),"-signature",str(sig_path),str(payload_path)],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        if proc.returncode!=0:
            raise ValueError("workstation application signature verification failed")

def candidate_files():
    seen=set()
    for root in SEARCH_ROOTS:
        if not root.exists():
            continue
        try:
            for base,dirs,files in os.walk(root):
                rel=Path(base)
                depth=len(rel.parts)-len(root.parts)
                if depth>7:
                    dirs[:]=[]
                    continue
                dirs[:]=[d for d in dirs if not d.startswith(".snapshot")]
                for name in files:
                    low=name.lower()
                    if not (low.endswith(".pem") or low.endswith(".json")):
                        continue
                    p=Path(base)/name
                    try:
                        st=p.stat()
                    except OSError:
                        continue
                    if st.st_size<=0 or st.st_size>64*1024:
                        continue
                    key=str(p)
                    if key in seen:
                        continue
                    seen.add(key)
                    yield p
        except OSError:
            continue

def find_controller_key(paths):
    matches=[]
    for p in paths:
        low=p.name.lower()
        if low not in KEY_NAMES and "controller" not in low:
            continue
        try:
            data=p.read_bytes()
        except OSError:
            continue
        if hashlib.sha256(data).hexdigest().lower()!=EXPECTED_CONTROLLER_KEY_SHA256:
            continue
        der=public_der_from_pem(p)
        pem=public_pem_from_pem(p)
        if not der or not pem:
            continue
        matches.append((p,der,pem))
    return matches

def load_candidate_publics(paths):
    out=[]
    seen=set()
    for p in paths:
        if not p.name.lower().endswith(".pem"):
            continue
        der=public_der_from_pem(p)
        pem=public_pem_from_pem(p)
        if not der or not pem:
            continue
        kid=key_id_from_der(der)
        if kid in seen:
            continue
        seen.add(kid)
        out.append((p,der,pem))
    return out

async def read_frame(reader):
    hdr=await reader.readexactly(4)
    size=struct.unpack("!I",hdr)[0]
    if size<2 or size>MAX_FRAME:
        raise RuntimeError(f"invalid frame size {size}")
    raw=await reader.readexactly(size)
    value=json.loads(raw.decode("utf-8"))
    if not isinstance(value,dict):
        raise RuntimeError("frame is not object")
    return value

async def write_frame(writer,value):
    raw=json.dumps(value,sort_keys=True,separators=(",",":"),ensure_ascii=True).encode("utf-8")
    if len(raw)>MAX_FRAME:
        raise RuntimeError("frame too large")
    writer.write(struct.pack("!I",len(raw))+raw)
    await writer.drain()

paths=list(candidate_files())
controllers=find_controller_key(paths)
if not controllers:
    blocked(
        "ENROLLED_CONTROLLER_KEY_NOT_FOUND_ON_NAS",
        expected_controller_key_sha256=EXPECTED_CONTROLLER_KEY_SHA256,
        searched_roots=[str(x) for x in SEARCH_ROOTS],
        pem_json_candidates_examined=len(paths),
        openssl=OPENSSL,
    )
    raise SystemExit(4)
if len(controllers)!=1:
    blocked(
        "CONTROLLER_KEY_AMBIGUOUS",
        matching_paths=[str(p) for p,_,_ in controllers],
        openssl=OPENSSL,
    )
    raise SystemExit(5)

controller_path,controller_der,controller_public_pem=controllers[0]
public_candidates=load_candidate_publics(paths)

async def connect():
    ctx=ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    ctx.check_hostname=False
    ctx.verify_mode=ssl.CERT_NONE
    ctx.minimum_version=ssl.TLSVersion.TLSv1_3
    ctx.set_alpn_protocols([ALPN])

    reader,writer=await asyncio.wait_for(
        asyncio.open_connection(EDGE_HOST,EDGE_PORT,ssl=ctx,server_hostname="veraport.local"),
        timeout=5.0,
    )
    try:
        sslobj=writer.get_extra_info("ssl_object")
        if sslobj is None:
            raise RuntimeError("TLS object unavailable")
        if sslobj.version()!="TLSv1.3":
            raise RuntimeError(f"unexpected TLS version {sslobj.version()}")
        if sslobj.selected_alpn_protocol()!=ALPN:
            raise RuntimeError(f"unexpected ALPN {sslobj.selected_alpn_protocol()}")
        peer=sslobj.getpeercert(binary_form=True)
        fp=hashlib.sha256(peer).hexdigest()
        if fp.lower()!=EXPECTED_TLS_SHA256:
            raise RuntimeError(f"TLS certificate fingerprint mismatch: {fp}")

        challenge=await asyncio.wait_for(read_frame(reader),timeout=5.0)
        if challenge.get("frame_type")!="server_challenge":
            raise RuntimeError(f"expected server_challenge, got {challenge.get('frame_type')}")
        if challenge.get("protocol_version")!="veraport-v1":
            raise RuntimeError("unexpected protocol version")

        workstation_pem=None
        workstation_path=None
        for p,der,pem in public_candidates:
            if (
                key_id_from_der(der)==challenge.get("workstation_key_id")
                and principal_id_from_der(der,"workstation")==challenge.get("workstation_principal")
            ):
                workstation_pem=pem
                workstation_path=p
                break
        if workstation_pem is None:
            blocked(
                "PINNED_WORKSTATION_PUBLIC_KEY_NOT_FOUND_ON_NAS",
                workstation_principal_from_challenge=challenge.get("workstation_principal"),
                workstation_key_id_from_challenge=challenge.get("workstation_key_id"),
                public_key_candidates_examined=len(public_candidates),
                controller_key_path=str(controller_path),
                openssl=OPENSSL,
            )
            return 6

        nonce=b64u(os.urandom(32))
        controller_principal=principal_id_from_der(controller_der,"controller")
        controller_kid=key_id_from_der(controller_der)
        unsigned={
            "protocol_version":"veraport-v1",
            "controller_principal":controller_principal,
            "controller_key_id":controller_kid,
            "workstation_principal":challenge["workstation_principal"],
            "server_challenge":challenge["challenge"],
            "client_nonce":nonce,
            "requested_capabilities":list(REQUESTED_CAPS),
        }
        sigbase=b"".join((
            b"veramesh-veraport-v1/session-client-auth\n",
            field("protocol_version",unsigned["protocol_version"]),
            field("controller_principal",unsigned["controller_principal"]),
            field("controller_key_id",unsigned["controller_key_id"]),
            field("workstation_principal",unsigned["workstation_principal"]),
            field("server_challenge",unsigned["server_challenge"]),
            field("client_nonce",unsigned["client_nonce"]),
            field("requested_capabilities",caps(unsigned["requested_capabilities"])),
        ))
        auth={
            "frame_type":"client_auth",
            **unsigned,
            "signature":sign_p1363(controller_path,sigbase),
        }
        await asyncio.wait_for(write_frame(writer,auth),timeout=5.0)

        accept=await asyncio.wait_for(read_frame(reader),timeout=5.0)
        if accept.get("frame_type")=="session_reject":
            raise RuntimeError(f"session rejected: {accept.get('code')}: {accept.get('message')}")
        if accept.get("frame_type")!="server_accept":
            raise RuntimeError(f"expected server_accept, got {accept.get('frame_type')}")
        if accept.get("controller_principal")!=controller_principal:
            raise RuntimeError("server accepted a different controller principal")
        if accept.get("workstation_principal")!=challenge.get("workstation_principal"):
            raise RuntimeError("server accept workstation principal mismatch")
        if accept.get("server_challenge")!=challenge.get("challenge") or accept.get("client_nonce")!=nonce:
            raise RuntimeError("server accept nonce/challenge mismatch")
        granted=tuple(sorted(accept.get("granted_capabilities") or []))
        if not set(granted).issubset(set(REQUESTED_CAPS)):
            raise RuntimeError("server granted unrequested capabilities")
        if int(accept.get("expires_at_ms",0))<=int(time.time()*1000):
            raise RuntimeError("server accept already expired")

        server_sigbase=b"".join((
            b"veramesh-veraport-v1/session-server-accept\n",
            field("protocol_version",accept["protocol_version"]),
            field("session_id",accept["session_id"]),
            field("controller_principal",accept["controller_principal"]),
            field("workstation_principal",accept["workstation_principal"]),
            field("workstation_key_id",accept["workstation_key_id"]),
            field("server_challenge",accept["server_challenge"]),
            field("client_nonce",accept["client_nonce"]),
            field("granted_capabilities",caps(granted)),
            field("expires_at_ms",str(accept["expires_at_ms"])),
        ))
        verify_p1363(workstation_pem,accept["signature"],server_sigbase)

        request_id="nas-lane-list-"+uuid.uuid4().hex
        await asyncio.wait_for(write_frame(writer,{
            "protocol_version":"veraport-v1",
            "request_id":request_id,
            "operation":"lane.list",
        }),timeout=5.0)
        response=await asyncio.wait_for(read_frame(reader),timeout=5.0)
        if response.get("request_id")!=request_id:
            raise RuntimeError("lane.list response request_id mismatch")
        if response.get("ok") is not True:
            raise RuntimeError(f"lane.list failed: {response.get('error')}")

        print(json.dumps({
            "schema":"VERAPORT_NAS_LAPPY_CONNECT_V2",
            "status":"CONNECTED",
            "observed_at_utc":time.strftime("%Y-%m-%dT%H:%M:%SZ",time.gmtime()),
            "transport":{
                "edge_host":EDGE_HOST,
                "edge_port":EDGE_PORT,
                "tls_version":sslobj.version(),
                "alpn":sslobj.selected_alpn_protocol(),
                "tls_cert_sha256":fp,
            },
            "application_session":{
                "session_id":accept["session_id"],
                "controller_principal":controller_principal,
                "controller_key_id":controller_kid,
                "workstation_principal":accept["workstation_principal"],
                "workstation_key_id":accept["workstation_key_id"],
                "granted_capabilities":list(granted),
                "expires_at_ms":accept["expires_at_ms"],
                "server_signature_verified":True,
                "controller_key_path":str(controller_path),
                "workstation_public_key_path":str(workstation_path),
            },
            "lane_list":response.get("result"),
            "runtime_dependency":{
                "python":sys.executable,
                "openssl":OPENSSL,
            },
            "effects":{
                "writes":False,
                "services_changed":False,
                "identity_changed":False,
                "process_execution_changed":False,
                "rdc_used":False,
            },
        },sort_keys=True))
        return 0
    finally:
        writer.close()
        try:
            await writer.wait_closed()
        except Exception:
            pass

try:
    rc=asyncio.run(connect())
except Exception as exc:
    print(json.dumps({
        "schema":"VERAPORT_NAS_LAPPY_CONNECT_V2",
        "status":"FAILED",
        "code":type(exc).__name__,
        "message":str(exc),
        "controller_key_path":str(controller_path),
        "runtime_dependency":{"python":sys.executable,"openssl":OPENSSL},
        "effects":{
            "writes":False,
            "services_changed":False,
            "identity_changed":False,
            "process_execution_changed":False,
            "rdc_used":False,
        },
    },sort_keys=True))
    raise SystemExit(7)
raise SystemExit(rc)
PY
