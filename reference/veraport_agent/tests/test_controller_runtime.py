from __future__ import annotations

import datetime
from pathlib import Path

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID

from veraport_agent.controller_config import ControllerConfig
from veraport_agent.controller_runtime import ControllerRuntime
from veraport_agent.hot_session import SessionBinding, principal_id
from veraport_agent.mcp_server import VeraPortMCPFacade


class Channel:
    def __init__(self, label, *, probe_ok=True):
        self.label = label
        self.probe_ok = probe_ok
        self.calls = []
        self.closed = False

    async def request(self, request):
        self.calls.append(dict(request))
        if request["operation"] == "lane.list":
            if not self.probe_ok:
                return {"request_id": request["request_id"], "ok": False}
            return {
                "request_id": request["request_id"],
                "ok": True,
                "result": {"lanes": []},
                "via": self.label,
            }
        return {
            "request_id": request["request_id"],
            "ok": True,
            "result": {"via": self.label},
            "via": self.label,
        }

    async def close(self):
        self.closed = True


def materials(tmp_path: Path):
    controller = ec.generate_private_key(ec.SECP256R1())
    workstation = ec.generate_private_key(ec.SECP256R1())
    tls_key = ec.generate_private_key(ec.SECP256R1())
    controller_path = tmp_path / "controller.pem"
    controller_path.write_bytes(
        controller.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )

    subject = issuer = x509.Name(
        [x509.NameAttribute(NameOID.COMMON_NAME, "localhost")]
    )
    now = datetime.datetime.now(datetime.timezone.utc)
    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(issuer)
        .public_key(tls_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(minutes=1))
        .not_valid_after(now + datetime.timedelta(days=1))
        .add_extension(
            x509.SubjectAlternativeName([x509.DNSName("localhost")]),
            critical=False,
        )
        .sign(workstation, hashes.SHA256())
    )
    cert_path = tmp_path / "workstation-cert.pem"
    cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    workstation_public_path = tmp_path / "workstation-public.pem"
    workstation_public_path.write_bytes(
        workstation.public_key().public_bytes(
            serialization.Encoding.PEM,
            serialization.PublicFormat.SubjectPublicKeyInfo,
        )
    )
    return (
        controller,
        workstation,
        controller_path,
        cert_path,
        workstation_public_path,
    )


def config(
    controller_path: Path,
    cert_path: Path,
    workstation_public_path: Path,
):
    return ControllerConfig.from_dict(
        {
            "schema": "VERAPORT_CONTROLLER_MCP_CONFIG_V1",
            "controller_key": str(controller_path),
            "tls_ca": str(cert_path),
            "workstation_public_key": str(workstation_public_path),
            "requested_capabilities": ["fs.read"],
            "gateway_operations": [
                "lane.list",
                "lane.open",
                "lane.renew",
                "lane.close",
                "fs.read_text",
            ],
            "endpoints": [
                {
                    "endpoint_id": "direct",
                    "mode": "DIRECT_STREAM",
                    "host": "127.0.0.1",
                    "port": 17444,
                    "server_hostname": "localhost",
                    "durable_idempotency": True,
                },
                {
                    "endpoint_id": "edge",
                    "mode": "EDGE_STREAM",
                    "host": "100.115.152.20",
                    "port": 17445,
                    "server_hostname": "localhost",
                    "durable_idempotency": True,
                },
            ],
        }
    )


@pytest.mark.asyncio
async def test_mcp_reconnect_reuses_persistent_veraport_sessions(tmp_path: Path):
    (
        controller,
        workstation,
        controller_path,
        cert_path,
        workstation_public_path,
    ) = materials(tmp_path)
    cfg = config(controller_path, cert_path, workstation_public_path)
    opens = []
    channels = {}

    async def opener(**kwargs):
        endpoint = (
            "direct" if kwargs["host"] == "127.0.0.1" else "edge"
        )
        opens.append(endpoint)
        channel = channels.setdefault(endpoint, Channel(endpoint))
        return channel, SessionBinding(
            session_id="session-" + endpoint,
            controller_principal=principal_id(
                controller.public_key(), "controller"
            ),
            workstation_principal=principal_id(
                workstation.public_key(), "workstation"
            ),
            granted_capabilities=frozenset({"fs.read"}),
            expires_at_ms=999999,
        )

    runtime = ControllerRuntime(
        cfg,
        open_session=opener,
        now_ms=lambda: 1000,
    )
    first = VeraPortMCPFacade(runtime)
    second = VeraPortMCPFacade(runtime)

    info1 = await first.machine_info()
    info2 = await second.machine_info()

    assert opens == ["direct", "edge"]
    assert info1["selected_path_id"] == "direct"
    assert info2["selected_path_id"] == "direct"
    assert {x["session_id"] for x in info2["paths"]} == {
        "session-direct",
        "session-edge",
    }


@pytest.mark.asyncio
async def test_tls_handshake_without_lane_list_does_not_make_path_current(
    tmp_path: Path,
):
    (
        controller,
        workstation,
        controller_path,
        cert_path,
        workstation_public_path,
    ) = materials(tmp_path)
    cfg = config(controller_path, cert_path, workstation_public_path)

    async def opener(**kwargs):
        label = (
            "direct" if kwargs["host"] == "127.0.0.1" else "edge"
        )
        return Channel(label, probe_ok=(label == "edge")), SessionBinding(
            session_id="session-" + label,
            controller_principal=principal_id(
                controller.public_key(), "controller"
            ),
            workstation_principal=principal_id(
                workstation.public_key(), "workstation"
            ),
            granted_capabilities=frozenset({"fs.read"}),
            expires_at_ms=999999,
        )

    runtime = ControllerRuntime(
        cfg,
        open_session=opener,
        now_ms=lambda: 1000,
    )
    info = await runtime.machine_info()

    assert info["selected_path_id"] == "edge"
    assert [x["endpoint_id"] for x in info["paths"]] == ["edge"]


@pytest.mark.asyncio
async def test_wrong_controller_principal_is_not_registered(tmp_path: Path):
    (
        controller,
        workstation,
        controller_path,
        cert_path,
        workstation_public_path,
    ) = materials(tmp_path)
    cfg = config(controller_path, cert_path, workstation_public_path)

    async def opener(**kwargs):
        return Channel("bad"), SessionBinding(
            session_id="bad",
            controller_principal="controller:wrong",
            workstation_principal=principal_id(
                workstation.public_key(), "workstation"
            ),
            granted_capabilities=frozenset({"fs.read"}),
            expires_at_ms=999999,
        )

    runtime = ControllerRuntime(
        cfg,
        open_session=opener,
        now_ms=lambda: 1000,
    )
    with pytest.raises(Exception, match="no authenticated"):
        await runtime.machine_info()


@pytest.mark.asyncio
async def test_stale_sessions_are_recreated_before_tool_call(tmp_path: Path):
    (
        controller,
        workstation,
        controller_path,
        cert_path,
        workstation_public_path,
    ) = materials(tmp_path)
    cfg = config(controller_path, cert_path, workstation_public_path)
    count = 0

    async def opener(**kwargs):
        nonlocal count
        count += 1
        label = (
            "direct" if kwargs["host"] == "127.0.0.1" else "edge"
        )
        return Channel(label), SessionBinding(
            session_id=f"{label}-{count}",
            controller_principal=principal_id(
                controller.public_key(), "controller"
            ),
            workstation_principal=principal_id(
                workstation.public_key(), "workstation"
            ),
            granted_capabilities=frozenset({"fs.read"}),
            expires_at_ms=1500 if count <= 2 else 999999,
        )

    now = [1000]
    runtime = ControllerRuntime(
        cfg,
        open_session=opener,
        now_ms=lambda: now[0],
    )
    await runtime.machine_info()
    assert count == 2

    now[0] = 2000
    await runtime.machine_info()
    assert count == 4


@pytest.mark.asyncio
async def test_lane_capability_cannot_exceed_controller_ceiling(
    tmp_path: Path,
):
    (
        controller,
        workstation,
        controller_path,
        cert_path,
        workstation_public_path,
    ) = materials(tmp_path)
    cfg = config(controller_path, cert_path, workstation_public_path)

    async def opener(**kwargs):
        label = (
            "direct" if kwargs["host"] == "127.0.0.1" else "edge"
        )
        return Channel(label), SessionBinding(
            session_id="session-" + label,
            controller_principal=principal_id(
                controller.public_key(), "controller"
            ),
            workstation_principal=principal_id(
                workstation.public_key(), "workstation"
            ),
            granted_capabilities=frozenset({"fs.read"}),
            expires_at_ms=999999,
        )

    facade = VeraPortMCPFacade(
        ControllerRuntime(
            cfg,
            open_session=opener,
            now_ms=lambda: 1000,
        )
    )

    with pytest.raises(PermissionError, match="session ceiling"):
        await facade.lane_open(
            "x",
            "x",
            ["fs.write"],
            [{"key": "fs:/tmp", "mode": "write"}],
        )
