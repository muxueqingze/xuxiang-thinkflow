"""Targeted independent regression for native-write fallback, no network."""
import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src.agent_loop import AgentLoop, AgentConfig
from src.changes import ChangeStore
from src.provider import ProviderConfig


WRITES = {"write", "append", "edit", "mkdir", "touch", "copy"}


def response(content="", tools=None):
    delta = {"tool_calls": tools} if tools is not None else {"content": content}
    return httpx.Response(200, text="data: " + json.dumps({"choices": [{"delta": delta,
        "finish_reason": "tool_calls" if tools is not None else "stop"}]}) + "\n\ndata: [DONE]\n\n")


def native(identifier, tool="write", **arguments):
    return {"index": 0, "id": identifier, "type": "function",
        "function": {"name": tool, "arguments": json.dumps(arguments)}}


class NativeWriteFallbackTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="thinkflow-fallback-review-")
        self.root = Path(self.temp.name).resolve()
        self.cwd = self.root / "workspace"
        self.cwd.mkdir()
        self.agents, self.events, self.requests = [], [], []

    async def asyncTearDown(self):
        for agent in self.agents:
            await agent.close()
        self.temp.cleanup()

    async def make(self, **provider_options):
        agent = AgentLoop(AgentConfig(cwd=str(self.cwd), max_run_turns=5,
            provider=ProviderConfig(base_url="http://127.0.0.1:1", model="local-fixture", **provider_options)),
            event_sink=lambda event: self.events.append(copy.deepcopy(event)))
        self.agents.append(agent)
        await agent.client.aclose()
        return agent

    def attach(self, agent, handler):
        async def record(request):
            payload = json.loads(request.content)
            self.requests.append(payload)
            return handler(payload, len(self.requests))
        agent.client = httpx.AsyncClient(base_url="http://127.0.0.1:1", transport=httpx.MockTransport(record))

    @staticmethod
    def names(body):
        return {tool["function"]["name"] for tool in body.get("tools", [])}

    async def test_default_explicit_allow_deny_disabled_and_anthropic_schemas(self):
        agent = await self.make()
        defaults = self.names(agent.build_request_body("fixture"))
        self.assertTrue({"read", "bash"} <= defaults)
        self.assertTrue(WRITES <= defaults)
        agent.native_write_fallback = True
        agent.config.provider.disabled_native_tools = ["copy"]
        fallback = self.names(agent.build_request_body("fixture"))
        self.assertTrue(WRITES - {"copy"} <= fallback)
        self.assertNotIn("copy", fallback)
        agent.native_write_fallback = False
        agent.config.provider.native_tools = ["write", "read", "copy"]
        agent.config.provider.disabled_native_tools = ["copy"]
        self.assertEqual(self.names(agent.build_request_body("fixture")), {"write", "read"})
        agent.config.provider.enable_native_tools = False
        self.assertEqual(self.names(agent.build_request_body("fixture")), set())
        anthropic = await self.make(format="anthropic")
        anthropic.config.provider.max_tokens = 8192
        names = {tool["name"] for tool in anthropic.build_request_body("fixture")["tools"]}
        self.assertTrue(WRITES <= names)
        anthropic.native_write_fallback = True
        names = {tool["name"] for tool in anthropic.build_request_body("fixture")["tools"]}
        self.assertTrue(WRITES <= names)

    async def test_default_stream_writes_remain_one_request_delayed_fifo(self):
        agent = await self.make()
        self.attach(agent, lambda body, count: response(
            '<tf-write id="1" path="a.txt">first</tf-write>'
            '<tf-append id="2" path="a.txt"> second</tf-append>Done.'))
        await agent.run("fixture")
        self.assertEqual(len(self.requests), 1)
        self.assertEqual((self.cwd / "a.txt").read_text(), "first second")
        self.assertEqual([record.flow for record in agent.context.records], ["delayed", "delayed"])
        self.assertFalse(agent.native_write_fallback)

    async def test_parser_fallback_notice_snapshot_and_new_run_reset(self):
        agent = await self.make()
        def handler(body, count):
            if count == 1:
                return response('<tf-write id="9" path="a.txt">unfinished')
            if count == 2:
                self.assertTrue(WRITES <= self.names(body))
                self.assertTrue(any("PROTOCOL FALLBACK" in str(message.get("content", "")) for message in body["messages"]))
                return response(tools=[native("repair", path="a.txt", content="repaired")])
            return response("Done.")
        self.attach(agent, handler)
        await agent.run("fixture")
        self.assertEqual(len(self.requests), 3)
        self.assertEqual((self.cwd / "a.txt").read_text(), "repaired")
        self.assertTrue(agent.native_write_fallback)
        self.assertTrue(any(event["type"] == "status_notice" and "原生文件工具" in event.get("message", "") for event in self.events))
        record = next(record for record in agent.context.records if record.id == "repair")
        self.assertEqual(record.flow, "blocking")
        self.assertTrue(all(event["flow"] == "blocking" for event in self.events
                            if event["type"] in {"tool_started", "tool_completed"} and event.get("id") == "repair"))
        restored = await self.make()
        restored.load_snapshot(agent.to_snapshot())
        self.assertTrue(restored.native_write_fallback)
        requests = []
        async def fresh(request):
            body = json.loads(request.content)
            requests.append(body)
            return response("New task finished.")
        restored.client = httpx.AsyncClient(base_url="http://127.0.0.1:1", transport=httpx.MockTransport(fresh))
        await restored.run("new fixture")
        self.assertFalse(restored.native_write_fallback)
        self.assertTrue(WRITES <= self.names(requests[0]))

    async def test_explicit_allow_does_not_expand_after_parse_error(self):
        agent = await self.make(native_tools=["read"])
        self.attach(agent, lambda body, count: response('<tf-write id="9" path="x">unfinished')
                    if count == 1 else response("Stopped malformed output."))
        await agent.run("fixture")
        self.assertEqual(len(self.requests), 2)
        self.assertEqual(self.names(self.requests[1]), {"read"})
        self.assertFalse(agent.native_write_fallback)

    async def test_fallback_unknown_receipt_stops_without_next_model_request(self):
        agent = await self.make()
        store = ChangeStore(self.root / "changes", self.cwd)
        agent.executor.set_change_store(store)
        self.attach(agent, lambda body, count: response('<tf-write id="9" path="a.txt">unfinished')
            if count == 1 else response(tools=[native("uncertain", path="a.txt", content="may exist")]))
        with patch.object(store, "complete", side_effect=OSError("final receipt fault")):
            await agent.run("fixture")
        self.assertEqual(len(self.requests), 2)
        self.assertEqual(agent.context.records[-1].status, "unknown")
        self.assertTrue(agent.last_error)
        self.assertEqual(store.list_changes()[0]["status"], "unknown")
        self.assertEqual((self.cwd / "a.txt").read_text(), "may exist")


if __name__ == "__main__":
    unittest.main(verbosity=2)
