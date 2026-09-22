#!/usr/bin/env python3
from __future__ import annotations

import argparse
import gzip
import hashlib
import io
import json
import os
import stat
import subprocess
import tarfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STATIC = {
    "bin/run-veramesh-gateway.sh": ("packaging/synology-gateway/run-veramesh-gateway.sh", 0o755),
    "config/controller.example.json": ("packaging/synology-gateway/controller.example.json", 0o600),
    "config/oauth.example.json": ("packaging/synology-gateway/oauth.example.json", 0o600),
    "systemd/pkguser-verameshgateway.service": ("packaging/synology-gateway/pkguser-verameshgateway.service", 0o644),
    "README.md": ("packaging/synology-gateway/README.md", 0o644),
}
FIXED_MTIME = 0


def git(*args: str) -> bytes:
    return subprocess.run(
        ["git", *args], cwd=ROOT, check=True, capture_output=True
    ).stdout


def git_head() -> str:
    return git("rev-parse", "HEAD").decode("ascii").strip()


def git_blob(path: str) -> bytes:
    return git("show", f"HEAD:{path}")


def regular_file_bytes(path: Path) -> bytes:
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode):
            raise ValueError(f"not a regular file: {path}")
        chunks = []
        while True:
            chunk = os.read(fd, 1024 * 1024)
            if not chunk:
                break
            chunks.append(chunk)
        return b"".join(chunks)
    finally:
        os.close(fd)


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def tar_gz(files: list[tuple[str, bytes, int]]) -> bytes:
    raw = io.BytesIO()
    with gzip.GzipFile(fileobj=raw, mode="wb", filename="", mtime=FIXED_MTIME, compresslevel=9) as gz:
        with tarfile.open(fileobj=gz, mode="w", format=tarfile.USTAR_FORMAT) as tf:
            for name, data, mode in sorted(files):
                info = tarfile.TarInfo(name)
                info.size = len(data)
                info.mode = mode
                info.mtime = FIXED_MTIME
                info.uid = 0
                info.gid = 0
                info.uname = ""
                info.gname = ""
                tf.addfile(info, io.BytesIO(data))
    return raw.getvalue()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--binary", required=True)
    parser.add_argument("--commit")
    parser.add_argument("--output")
    args = parser.parse_args()

    head = git_head()
    if args.commit and args.commit != head:
        raise ValueError(f"requested commit {args.commit} != exact Git HEAD {head}")

    version = git_blob("packaging/synology-gateway/VERSION").decode("ascii").strip()
    if not version:
        raise ValueError("empty gateway bundle version")

    binary_path = Path(args.binary)
    binary = regular_file_bytes(binary_path)
    if not binary:
        raise ValueError("gateway binary is empty")

    files: list[tuple[str, bytes, int]] = [("bin/veramesh-gateway", binary, 0o755)]
    manifest_files = [{
        "path": "bin/veramesh-gateway",
        "mode": "0755",
        "bytes": len(binary),
        "sha256": sha256(binary),
    }]

    for archive_path, (source_path, mode) in STATIC.items():
        data = git_blob(source_path)
        files.append((archive_path, data, mode))
        manifest_files.append({
            "path": archive_path,
            "source_path": source_path,
            "mode": f"{mode:04o}",
            "bytes": len(data),
            "sha256": sha256(data),
        })

    manifest = {
        "schema": "VERAMESH_SYNOLOGY_GATEWAY_BUNDLE_V1",
        "package_identity": "VeraMeshGateway",
        "version": version,
        "source_repository": "thebrazenbeard/vera-mesh",
        "source_commit": head,
        "target": {"goos": "linux", "goarch": "arm", "goarm": "7", "cgo": False},
        "listen": "127.0.0.1:17446",
        "coexists_with": "VeraMesh",
        "default_capability_ceiling": ["fs.read"],
        "files": sorted(manifest_files, key=lambda item: item["path"]),
    }
    manifest_bytes = (json.dumps(manifest, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")
    files.append(("MANIFEST.json", manifest_bytes, 0o644))

    out = Path(args.output) if args.output else ROOT / "dist" / f"VeraMeshGateway-{version}-linux-armv7.tar.gz"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(tar_gz(files))
    print(json.dumps({
        "bundle": str(out),
        "sha256": sha256(out.read_bytes()),
        "source_commit": head,
        "version": version,
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
