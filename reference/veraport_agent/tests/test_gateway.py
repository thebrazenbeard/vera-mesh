import pytest

from veraport_agent.gateway import GatewayOperationDenied, READ_OPERATIONS, VeraPortGateway


class Pool:
    def __init__(self):
        self.calls = []

    async def request(self, workstation, controller, request, *, now_ms):
        self.calls.append((workstation, controller, dict(request), now_ms))
        return {
            "ok": True,
            "request_id": request["request_id"],
            "result": {"operation": request["operation"]},
        }


def id_factory():
    values = iter(["request-1", "request-2", "request-3"])
    return lambda: next(values)


@pytest.mark.asyncio
async def test_gateway_is_thin_projection_over_existing_pool():
    pool = Pool()
    gateway = VeraPortGateway(
        pool,
        workstation_principal="workstation:w",
        controller_principal="controller:c",
        allowed_operations=READ_OPERATIONS,
        now_ms=lambda: 1234,
        request_id_factory=id_factory(),
    )

    first = await gateway.list_lanes()
    second = await gateway.read_text(lane_id="lane", fencing_token=7, path="/x")

    assert first["request_id"] == "request-1"
    assert second["request_id"] == "request-2"
    assert [call[0] for call in pool.calls] == ["workstation:w", "workstation:w"]
    assert [call[1] for call in pool.calls] == ["controller:c", "controller:c"]
    assert [call[3] for call in pool.calls] == [1234, 1234]


@pytest.mark.asyncio
async def test_mutation_denied_when_gateway_policy_is_read_only():
    pool = Pool()
    gateway = VeraPortGateway(
        pool,
        workstation_principal="workstation:w",
        controller_principal="controller:c",
        allowed_operations=READ_OPERATIONS,
    )

    with pytest.raises(GatewayOperationDenied):
        await gateway.write_text(
            lane_id="lane",
            fencing_token=1,
            path="/x",
            content="x",
        )
    assert pool.calls == []


@pytest.mark.asyncio
async def test_process_exec_requires_explicit_gateway_operation_grant():
    pool = Pool()
    gateway = VeraPortGateway(
        pool,
        workstation_principal="workstation:w",
        controller_principal="controller:c",
        allowed_operations={"process.exec"},
        request_id_factory=lambda: "process-1",
        now_ms=lambda: 1,
    )

    result = await gateway.run_process(
        lane_id="lane",
        fencing_token=2,
        argv=["python", "-V"],
        cwd="/work",
    )

    assert result["ok"] is True
    assert pool.calls[0][1] == "controller:c"
    assert pool.calls[0][2]["operation"] == "process.exec"
    assert pool.calls[0][2]["request_id"] == "process-1"


def test_gateway_requires_controller_principal():
    with pytest.raises(ValueError, match="controller_principal"):
        VeraPortGateway(
            Pool(),
            workstation_principal="workstation:w",
            controller_principal="",
            allowed_operations=READ_OPERATIONS,
        )


def test_unknown_operation_in_gateway_policy_rejected_at_construction():
    with pytest.raises(ValueError):
        VeraPortGateway(
            Pool(),
            workstation_principal="workstation:w",
            controller_principal="controller:c",
            allowed_operations={"magic.root"},
        )
