#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import tarfile
from pathlib import PurePosixPath

ALLOWED = {
    "MANIFEST.json",
    "README.md",
    "bin/run-veramesh-gateway.sh",
    "bin/veramesh-gateway",
    "config/controller.example.json",
    "config/oauth.example.json",
    "systemd/pkguser-verameshgateway.service",
}


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("bundle")
    parser.add_argument("--expected-commit", required=True)
    args = parser.parse_args()

    with tarfile.open(args.bundle, "r:gz") as tf:
        members = tf.getmembers()
        names = [m.name for m in members]
        if set(names) != ALLOWED or len(names) != len(ALLOWED):
            raise ValueError(f"bundle path set mismatch: {sorted(names)}")
        for member in members:
            p = PurePosixPath(member.name)
            if p.is_absolute() or ".." in p.parts or not member.isfile():
                raise ValueError(f"unsafe bundle member: {member.name}")
        extracted = {m.name: tf.extractfile(m).read() for m in members}

    manifest = json.loads(extracted["MANIFEST.json"].decode("utf-8"))
    if manifest.get("schema") != "VERAMESH_SYNOLOGY_GATEWAY_BUNDLE_V1":
        raise ValueError("wrong bundle schema")
    if manifest.get("package_identity") != "VeraMeshGateway":
        raise ValueError("wrong package identity")
    if manifest.get("source_commit") != args.expected_commit:
        raise ValueError("bundle source commit mismatch")
    if manifest.get("binary_vcs_revision") != args.expected_commit:
        raise ValueError("bundle binary VCS revision mismatch")
    if manifest.get("binary_vcs_modified") is not False:
        raise ValueError("bundle binary was not built from a clean source tree")
    if manifest.get("coexists_with") != "VeraMesh":
        raise ValueError("existing VeraMesh edge coexistence not declared")
    if manifest.get("listen") != "127.0.0.1:17446":
        raise ValueError("gateway listen contract drift")
    if manifest.get("default_capability_ceiling") != ["fs.read"]:
        raise ValueError("default package capability ceiling widened")

    described = {item["path"]: item for item in manifest.get("files", [])}
    if set(described) != ALLOWED - {"MANIFEST.json"}:
        raise ValueError("manifest file set mismatch")
    for path, item in described.items():
        data = extracted[path]
        if item.get("bytes") != len(data) or item.get("sha256") != sha256(data):
            raise ValueError(f"manifest identity mismatch: {path}")
        actual_mode = oct(next(m.mode for m in members if m.name == path) & 0o777)[2:].zfill(4)
        if item.get("mode") != actual_mode:
            raise ValueError(f"manifest mode mismatch: {path}")

    controller = json.loads(extracted["config/controller.example.json"].decode("utf-8"))
    if controller.get("requested_capabilities") != ["fs.read"]:
        raise ValueError("controller example widens default capabilities")
    forbidden = {"fs.write", "process.exec", "process.inspect", "process.interact", "process.control"}
    if forbidden.intersection(controller.get("requested_capabilities", [])):
        raise ValueError("forbidden capability present in default controller example")

    oauth = json.loads(extracted["config/oauth.example.json"].decode("utf-8"))
    if oauth.get("client_secret_env") != "VERAMESH_OAUTH_CLIENT_SECRET":
        raise ValueError("wrapper/OAuth secret contract mismatch")

    print(json.dumps({
        "schema": "VERAMESH_SYNOLOGY_GATEWAY_BUNDLE_VERIFICATION_V1",
        "status": "PASS",
        "source_commit": args.expected_commit,
        "bundle_sha256": sha256(open(args.bundle, "rb").read()),
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
