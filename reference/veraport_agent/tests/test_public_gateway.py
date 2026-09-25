from __future__ import annotations

from types import SimpleNamespace

import pytest

from veraport_agent.public_gateway import (
    PublicProcessNotFound,
    PublicWorkstationFacade,
    canonical_remote_path,
)


class GatewayStub:
    def __init__(self):
        self.calls = []

    async def call_operation(self, operation, **body):
        self.calls.append((operation, dict(body)))
        if operation == "process.start":
            return {
                "ok": True,
                "result": {
                    "process_handle": "proc:internal",
                    "running": True,
                    "pid": 123,
                },
            }
        if operation in {
            "process.status",
            "process.output",
            "process.input",
            "process.terminate",
        }:
            result = {
                "process_handle": body["process_handle"],
                "running": operation != "process.terminate",
            }
            if operation == "process.output":
                result.update({"stdout": "ok", "stderr": ""})
            if operation == "process.input":
                result["bytes_written"] = 3
            return {"ok": True, "result": result}
        return {"ok": True, "result": {"operation": operation}}


class RuntimeStub:
    def __init__(self, *, close_ok=True):
        self.config = SimpleNamespace(
            requested_capabilities=frozenset({
                "fs.read",
                "fs.write",
                "process.exec",
                "process.inspect",
                "process.interact",
                "process.control",
            }),
            gateway_operations=frozenset(),
        )
        self.gateway = GatewayStub()
        self.close_ok = close_ok
        self.opened = []
        self.closed = []
        self.reads = []

    async def ensure_started(self):
        return None

    async def machine_info(self):
        return {"schema": "machine-info"}

    async def open_lane(self, **kwargs):
        self.opened.append(dict(kwargs))
        return {
            "ok": True,
            "result": {
                "lane_id": kwargs["lane_id"],
                "fencing_token": 777,
            },
        }

    async def close_lane(self, **kwargs):
        self.closed.append(dict(kwargs))
        if self.close_ok:
            return {"ok": True, "result": {"closed": True}}
        return {
            "ok": False,
            "error": {
                "code": "CLOSE_FAILED",
                "message": "cleanup failed",
            },
        }

    async def read_text(self, **kwargs):
        self.reads.append(("fs.read_text", dict(kwargs)))
        return {"ok": True, "result": {"content": "hello"}}

    async def read_bytes(self, **kwargs):
        self.reads.append(("fs.read_bytes", dict(kwargs)))
        return {"ok": True, "result": {"content_base64": "aGk="}}

    async def read_operation(self, operation, **kwargs):
        self.reads.append((operation, dict(kwargs)))
        return {"ok": True, "result": {"operation": operation}}

    async def write_text(self, **kwargs):
        self.reads.append(("fs.write_text", dict(kwargs)))
        return {"ok": True, "result": {"written": True}}


def ids():
    counter = 0

    def next_id():
        nonlocal counter
        counter += 1
        return f"id{counter}"

    return next_id


def test_remote_path_canonicalization_is_controller_os_independent():
    assert canonical_remote_path(r"C:\Users\Patrick\work\..\repo") == (
        "C:/Users/Patrick/repo"
    )
    assert canonical_remote_path("/srv/work/../repo") == "/srv/repo"
    assert canonical_remote_path(r"\\server\share\a\..\b") == (
        "//server/share/b"
    )
    with pytest.raises(ValueError, match="absolute"):
        canonical_remote_path("relative/path")


@pytest.mark.asyncio
async def test_public_read_hides_lane_and_uses_exact_read_claim():
    runtime = RuntimeStub()
    public = PublicWorkstationFacade(
        runtime,
        actor_id="user:patrick",
        id_factory=ids(),
    )

    result = await public.read_file(path=r"C:\Users\Patrick\repo\README.md")

    assert result == {"content": "hello"}
    opened = runtime.opened[0]
    assert opened["capabilities"] == ["fs.read"]
    assert opened["claims"] == [{
        "key": "fs:C:/Users/Patrick/repo/README.md",
        "mode": "read",
    }]
    assert "lane_id" not in result
    assert "fencing_token" not in result
    assert len(runtime.closed) == 1


@pytest.mark.asyncio
async def test_successful_write_reports_cleanup_failure_without_becoming_failure():
    runtime = RuntimeStub(close_ok=False)
    public = PublicWorkstationFacade(
        runtime,
        actor_id="user:patrick",
        id_factory=ids(),
    )

    result = await public.write_file(
        path=r"C:\Users\Patrick\repo\x.txt",
        content="done",
    )

    assert result["written"] is True
    assert result["_veramesh_cleanup"]["closed"] is False
    assert result["_veramesh_cleanup"]["error"]["code"] == "CLOSE_FAILED"


@pytest.mark.asyncio
async def test_managed_process_exposes_public_handle_not_lane_or_internal_handle():
    runtime = RuntimeStub()
    public = PublicWorkstationFacade(
        runtime,
        actor_id="user:patrick",
        id_factory=ids(),
    )

    started = await public.start_process(
        argv=["python", "-c", "print('x')"],
        cwd=r"C:\Users\Patrick\repo",
        max_runtime_s=30,
    )
    handle = started["process_handle"]

    assert handle.startswith("job:")
    assert handle != "proc:internal"
    opened = runtime.opened[0]
    assert set(opened["capabilities"]) == {
        "process.exec",
        "process.inspect",
        "process.interact",
        "process.control",
    }
    assert opened["claims"] == [{
        "key": "cwd:C:/Users/Patrick/repo",
        "mode": "write",
    }]

    status = await public.process_status(process_handle=handle)
    assert status["process_handle"] == handle
    assert runtime.gateway.calls[-1][1]["process_handle"] == "proc:internal"

    output = await public.process_output(process_handle=handle)
    assert output["process_handle"] == handle
    assert output["stdout"] == "ok"

    wrote = await public.process_input(
        process_handle=handle,
        input_text="hi",
    )
    assert wrote["process_handle"] == handle
    assert wrote["bytes_written"] == 3

    terminated = await public.terminate_process(process_handle=handle)
    assert terminated["process_handle"] == handle
    assert terminated["running"] is False

    released = await public.release_process(process_handle=handle)
    assert released["released"] is True
    with pytest.raises(PublicProcessNotFound):
        await public.process_status(process_handle=handle)


@pytest.mark.asyncio
async def test_public_process_handles_are_actor_facade_local():
    runtime = RuntimeStub()
    first = PublicWorkstationFacade(
        runtime,
        actor_id="user:first",
        id_factory=ids(),
    )
    second = PublicWorkstationFacade(
        runtime,
        actor_id="user:second",
        id_factory=ids(),
    )
    started = await first.start_process(
        argv=["python"],
        cwd=r"C:\Users\Patrick",
        max_runtime_s=5,
    )
    with pytest.raises(PublicProcessNotFound):
        await second.process_status(
            process_handle=started["process_handle"]
        )


@pytest.mark.asyncio
async def test_content_search_uses_hidden_mirrored_read_lane():
    runtime = RuntimeStub()
    public = PublicWorkstationFacade(
        runtime,
        actor_id="user:patrick",
        id_factory=ids(),
    )
    result = await public.search_content(
        root=r"C:\Users\Patrick\repo",
        query="needle",
        file_pattern="*.py",
    )
    assert result == {"operation": "fs.search_content"}
    assert runtime.reads[-1][0] == "fs.search_content"
    assert runtime.opened[0]["claims"][0] == {
        "key": "fs:C:/Users/Patrick/repo",
        "mode": "read",
    }
