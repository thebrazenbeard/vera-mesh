import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
SPEC = importlib.util.spec_from_file_location(
    "operator", HERE / "openai_responses_veramesh_operator.py"
)
operator = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(operator)


class FakeTransport:
    def __init__(self, responses):
        self.responses = list(responses)
        self.payloads = []

    def create(self, payload):
        self.payloads.append(json.loads(json.dumps(payload)))
        if not self.responses:
            raise AssertionError("unexpected API call")
        return self.responses.pop(0)


class OperatorTests(unittest.TestCase):
    def config(self, **kwargs):
        base = dict(
            api_key="sk-test-secret",
            tunnel_id="tunnel_test_secret",
            model="gpt-test",
        )
        base.update(kwargs)
        return operator.OperatorConfig(**base)

    def test_mcp_tool_uses_tunnel_id_not_server_url(self):
        tool = operator.build_mcp_tool(self.config())
        self.assertEqual(tool["tunnel_id"], "tunnel_test_secret")
        self.assertNotIn("server_url", tool)
        self.assertEqual(tool["require_approval"], "always")

    def test_default_profile_is_read_only_surface(self):
        tool = operator.build_mcp_tool(self.config())
        self.assertIn("fs_read_text", tool["allowed_tools"])
        self.assertNotIn("fs_write_text", tool["allowed_tools"])
        self.assertNotIn("process_start", tool["allowed_tools"])

    def test_filesystem_profile_excludes_process(self):
        tool = operator.build_mcp_tool(self.config(profile="filesystem"))
        self.assertIn("fs_write_text", tool["allowed_tools"])
        self.assertNotIn("process_start", tool["allowed_tools"])

    def test_build_profile_includes_managed_process_surface(self):
        tool = operator.build_mcp_tool(self.config(profile="build"))
        for name in operator.PROCESS_TOOLS:
            self.assertIn(name, tool["allowed_tools"])

    def test_auto_approve_readonly_is_narrow(self):
        cfg = self.config(profile="build", auto_approve_readonly=True)
        policy = operator.approval_policy(cfg)
        never = policy["never"]["tool_names"]
        self.assertIn("fs_read_text", never)
        self.assertIn("process_output", never)
        self.assertNotIn("fs_write_text", never)
        self.assertNotIn("process_start", never)
        self.assertNotIn("process_input", never)
        self.assertNotIn("process_terminate", never)

    def test_tunnel_config_reads_only_expected_schema(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "tunnel-runtime.json"
            path.write_text(
                json.dumps(
                    {
                        "schema": "VERAMESH_TUNNEL_RUNTIME_SERVICE_V1",
                        "tunnel_id": "tunnel_from_config",
                        "runtime_api_key_file": "must-not-be-read.key",
                    }
                ),
                encoding="utf-8",
            )
            self.assertEqual(
                operator.load_tunnel_id_from_config(path),
                "tunnel_from_config",
            )

    def test_redacted_payload_hides_tunnel(self):
        cfg = self.config()
        session = operator.OperatorSession(
            cfg,
            transport=FakeTransport([]),
            approval_decider=lambda _: (False, None),
        )
        session.transcript.append(operator.user_input_item("inspect"))
        text = json.dumps(operator.redacted_payload(session._payload(), cfg))
        self.assertNotIn(cfg.tunnel_id, text)
        self.assertNotIn(cfg.api_key, text)
        self.assertIn("<redacted>", text)

    def test_stateless_approval_replays_output_and_response(self):
        first = {
            "id": "resp_1",
            "output": [
                {
                    "id": "reasoning_1",
                    "type": "reasoning",
                    "encrypted_content": "encrypted-reasoning",
                },
                {
                    "id": "mcpr_1",
                    "type": "mcp_approval_request",
                    "name": "machine_info",
                    "server_label": "lappy_veramesh",
                    "arguments": "{}",
                },
            ],
        }
        second = {
            "id": "resp_2",
            "output": [
                {
                    "type": "message",
                    "role": "assistant",
                    "content": [
                        {"type": "output_text", "text": "Lappy is reachable."}
                    ],
                }
            ],
        }
        transport = FakeTransport([first, second])
        session = operator.OperatorSession(
            self.config(),
            transport=transport,
            approval_decider=lambda request: (True, None),
        )
        response = session.send("Inspect Lappy")
        self.assertEqual(operator.output_text(response), "Lappy is reachable.")
        self.assertEqual(len(transport.payloads), 2)
        for payload in transport.payloads:
            self.assertFalse(payload["store"])
            self.assertNotIn("previous_response_id", payload)
        second_input = transport.payloads[1]["input"]
        self.assertEqual(second_input[0]["role"], "user")
        self.assertEqual(second_input[1]["type"], "reasoning")
        self.assertEqual(second_input[2]["type"], "mcp_approval_request")
        self.assertEqual(second_input[3]["type"], "mcp_approval_response")
        self.assertTrue(second_input[3]["approve"])

    def test_rejected_approval_is_replayed(self):
        first = {
            "id": "resp_1",
            "output": [
                {
                    "id": "mcpr_1",
                    "type": "mcp_approval_request",
                    "name": "fs_write_text",
                    "arguments": '{"path":"C:/Temp/a.txt"}',
                }
            ],
        }
        second = {"id": "resp_2", "output": []}
        transport = FakeTransport([first, second])
        session = operator.OperatorSession(
            self.config(profile="filesystem"),
            transport=transport,
            approval_decider=lambda request: (False, "not authorized"),
        )
        session.send("Write a file")
        item = transport.payloads[1]["input"][-1]
        self.assertEqual(item["type"], "mcp_approval_response")
        self.assertFalse(item["approve"])
        self.assertEqual(item["reason"], "not authorized")

    def test_output_text_fallback(self):
        response = {
            "output": [
                {
                    "type": "message",
                    "content": [
                        {"type": "output_text", "text": "hello"},
                        {"type": "output_text", "text": "world"},
                    ],
                }
            ]
        }
        self.assertEqual(operator.output_text(response), "hello\nworld")


if __name__ == "__main__":
    unittest.main()
