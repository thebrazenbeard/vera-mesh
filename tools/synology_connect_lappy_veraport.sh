#!/bin/sh
# Establish a fresh authenticated VeraPort application session from TheSimsVault
# to Lappy through the existing VeraMesh edge. Does not use RDC and performs no
# configuration, identity, service, firewall, Tailscale, or process-policy mutation.
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
import ssl
import struct
import sys
import time
import uuid

try:
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature, encode_dss_signature
except Exception as exc:
    print(json.dumps({
        "schema":"VERAPORT_NAS_LAPPY_CONNECT_V1",
        "status":"BLOCKED",
        "code":"CRYPTOGRAPHY_NOT_AVAILABLE",
        "message":str(exc),
        "effects":{"writes":False,"services_changed":False,"rdc_used":False},
    }, sort_keys=True))
    raise SystemExit(3)

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
PUB_PATTERNS=("workstation", "public", ".pub.")

def b64u(v: bytes) -> str:
    return base64.urlsafe_b64encode(v).rstrip(b"=").decode("ascii")

def b64ud(s: str) -> bytes:
    return base64.urlsafe_b64decode((s+"="*((4-len(s)%4)%4)).encode("ascii"))

def spki(public) -> bytes:
    return public.public_bytes(serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo)

def key_id(public) -> str:
    return hashlib.sha256(spki(public)).hexdigest()

def principal_id(public, prefix: str) -> str:
    return f"{prefix}:{key_id(public)}"

def field(name: str, value: str) -> bytes:
    raw=value.encode("utf-8")
    return name.encode("ascii")+b"="+str(len(raw)).encode("ascii")+b":"+raw+b"\n"

def caps(values) -> str:
    return ",".join(sorted(set(values)))

def sign_p1363(private, payload: bytes) -> str:
    der=private.sign(payload, ec.ECDSA(hashes.SHA256()))
    r,s=decode_dss_signature(der)
    return b64u(r.to_bytes(32,"big")+s.to_bytes(32,"big"))

def verify_p1363(public, sig: str, payload: bytes) -> None:
    raw=b64ud(sig)
    if len(raw)!=64:
        raise ValueError("signature length")
    r=int.from_bytes(raw[:32],"big"); s=int.from_bytes(raw[32:],"big")
    public.verify(encode_dss_signature(r,s), payload, ec.ECDSA(hashes.SHA256()))

def candidate_files():
    seen=set()
    for root in SEARCH_ROOTS:
        if not root.exists():
            continue
        try:
            for base, dirs, files in os.walk(root):
                # Bound traversal to VeraMesh/controller/identity-looking paths.
                rel=Path(base)
                depth=len(rel.parts)-len(root.parts)
                if depth>7:
                    dirs[:] = []
                    continue
                dirs[:] = [d for d in dirs if not d.startswith(".snapshot")]
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
        if p.name.lower() not in KEY_NAMES and "controller" not in p.name.lower():
            continue
        try:
            data=p.read_bytes()
        except OSError:
            continue
        if hashlib.sha256(data).hexdigest().lower()!=EXPECTED_CONTROLLER_KEY_SHA256:
            continue
        try:
            key=serialization.load_pem_private_key(data,password=None)
        except Exception:
            continue
        if not isinstance(key, ec.EllipticCurvePrivateKey) or not isinstance(key.curve, ec.SECP256R1):
            continue
        matches.append((p,key))
    return matches

def load_candidate_publics(paths):
    out=[]
    seen=set()
    for p in paths:
        low=p.name.lower()
        if not low.endswith(".pem"):
            continue
        if not any(mark in low for mark in PUB_PATTERNS):
            continue
        try:
            data=p.read_bytes()
        except OSError:
            continue
        candidates=[]
        try:
            pub=serialization.load_pem_public_key(data)
            candidates.append(pub)
        except Exception:
            try:
                priv=serialization.load_pem_private_key(data,password=None)
                candidates.append(priv.public_key())
            except Exception:
                pass
        for pub in candidates:
            if not isinstance(pub, ec.EllipticCurvePublicKey) or not isinstance(pub.curve, ec.SECP256R1):
                continue
            kid=key_id(pub)
            if kid in seen:
                continue
            seen.add(kid)
            out.append((p,pub))
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
    print(json.dumps({
        "schema":"VERAPORT_NAS_LAPPY_CONNECT_V1",
        "status":"BLOCKED",
        "code":"ENROLLED_CONTROLLER_KEY_NOT_FOUND_ON_NAS",
        "expected_controller_key_sha256":EXPECTED_CONTROLLER_KEY_SHA256,
        "searched_roots":[str(x) for x in SEARCH_ROOTS],
        "pem_json_candidates_examined":len(paths),
        "effects":{"writes":False,"services_changed":False,"rdc_used":False},
    }, sort_keys=True))
    raise SystemExit(4)
if len(controllers)!=1:
    print(json.dumps({
        "schema":"VERAPORT_NAS_LAPPY_CONNECT_V1",
        "status":"BLOCKED",
        "code":"CONTROLLER_KEY_AMBIGUOUS",
        "matching_paths":[str(p) for p,_ in controllers],
        "effects":{"writes":False,"services_changed":False,"rdc_used":False},
    }, sort_keys=True))
    raise SystemExit(5)

controller_path,controller_key=controllers[0]
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

        workstation=None
        workstation_path=None
        for p,pub in public_candidates:
            if key_id(pub)==challenge.get("workstation_key_id") and principal_id(pub,"workstation")==challenge.get("workstation_principal"):
                workstation=pub
                workstation_path=p
                break
        if workstation is None:
            print(json.dumps({
                "schema":"VERAPORT_NAS_LAPPY_CONNECT_V1",
                "status":"BLOCKED",
                "code":"PINNED_WORKSTATION_PUBLIC_KEY_NOT_FOUND_ON_NAS",
                "workstation_principal_from_challenge":challenge.get("workstation_principal"),
                "workstation_key_id_from_challenge":challenge.get("workstation_key_id"),
                "public_key_candidates_examined":len(public_candidates),
                "controller_key_path":str(controller_path),
                "effects":{"writes":False,"services_changed":False,"rdc_used":False},
            }, sort_keys=True))
            return 6

        controller_pub=controller_key.public_key()
        nonce=b64u(os.urandom(32))
        controller_principal=principal_id(controller_pub,"controller")
        controller_kid=key_id(controller_pub)
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
        auth={"frame_type":"client_auth",**unsigned,"signature":sign_p1363(controller_key,sigbase)}
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
        verify_p1363(workstation,accept["signature"],server_sigbase)

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
            "schema":"VERAPORT_NAS_LAPPY_CONNECT_V1",
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
            "effects":{
                "writes":False,
                "services_changed":False,
                "identity_changed":False,
                "process_execution_changed":False,
                "rdc_used":False,
            },
        }, sort_keys=True))
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
        "schema":"VERAPORT_NAS_LAPPY_CONNECT_V1",
        "status":"FAILED",
        "code":type(exc).__name__,
        "message":str(exc),
        "controller_key_path":str(controller_path),
        "effects":{"writes":False,"services_changed":False,"rdc_used":False},
    }, sort_keys=True))
    raise SystemExit(7)
raise SystemExit(rc)
PY
