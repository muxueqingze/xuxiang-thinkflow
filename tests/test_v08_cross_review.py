"""Independent v0.8 integration probes; fake runtimes/HTTP transport only.

The reviewer implemented executor/changes, but did not implement the service,
admission, attachments, task plans or provider protocol covered by these tests.
"""
import asyncio
import copy
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from src import desktop_service
from src.desktop_service import DesktopService
from src.agent_loop import AgentLoop, AgentConfig
from src.executor import Executor
from src.parser import Command
from src.provider import ProviderConfig
from test_desktop_service import FakeAgent


class ServiceCrossReview(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="thinkflow-cross-review-")
        self.root = Path(self.temp.name).resolve()
        self.cwd = self.root / "workspace"
        self.cwd.mkdir()
        self.calls, self.services = [], []
        self.runner = None
        def create(config, system, cwd, **kwargs):
            async def run(agent, prompt):
                self.calls.append(prompt)
                if self.runner:
                    await self.runner(agent, prompt)
            agent = FakeAgent(**kwargs, runner=run)
            agent.executor = Executor(cwd=cwd)
            return agent
        self.create_patch = patch.object(desktop_service, "create_agent", side_effect=create)
        self.prompt_patch = patch.object(desktop_service, "resolve_system_prompt", return_value="Local fixture")
        self.create_patch.start()
        self.prompt_patch.start()
        self.service = await self.open_service()

    async def open_service(self):
        service = DesktopService()
        self.services.append(service)
        await service.dispatch("initialize", {"data_dir": str(self.root / "data"),
            "config": {"base_url": "http://127.0.0.1:1", "model": "fake"}})
        await service.dispatch("open_workspace", {"cwd": str(self.cwd)})
        return service

    async def asyncTearDown(self):
        try:
            for service in self.services:
                await service.close()
        finally:
            self.create_patch.stop()
            self.prompt_patch.stop()
            self.temp.cleanup()

    async def submit(self, identifier, prompt, service=None, **extra):
        service = service or self.service
        return await service.dispatch("submit_input", {"command_id": identifier, "prompt": prompt,
            "session_id": service.session_id, **extra})

    async def test_ui_revert_final_receipt_failure_blocks_run_across_restart(self):
        file = self.cwd / "a.txt"
        file.write_bytes(b"old")
        await self.service.agent.executor.read("a.txt")
        result = await self.service.agent.executor.execute(Command("fixture-write", "write", path="a.txt", content="new"))
        store = self.service.agent.executor.change_store
        with patch.object(store, "complete", side_effect=OSError("isolated final receipt fault")):
            with self.assertRaises(OSError):
                await self.service.dispatch("revert_workspace_change", {
                    "change_id": result.change_id, "expected_revision": result.revision})
        self.assertEqual(file.read_bytes(), b"old")
        self.assertTrue(self.service.state()["recovery_required"])
        self.assertTrue(any(entry["operation"] == "revert" and entry["status"] == "unknown" for entry in store.list_changes()))
        with self.assertRaises(ValueError):
            await self.submit("must-wait", "next")
        await self.service.close()
        restarted = await self.open_service()
        self.assertTrue(restarted.state()["recovery_required"])
        with self.assertRaises(ValueError):
            await self.submit("still-wait", "next", restarted)
        await restarted.dispatch("acknowledge_recovery")
        await self.submit("explicit-next", "after confirmation", restarted)
        await restarted.dispatch("resume_queue")
        await restarted.task
        self.assertEqual(self.calls, ["after confirmation"])

    async def test_lost_ack_duplicate_stop_restart_and_explicit_resume(self):
        entered = asyncio.Event()
        async def hold(agent, prompt):
            if prompt == "first":
                entered.set()
                await asyncio.Event().wait()
        self.runner = hold
        await self.submit("first-id", "first")
        await entered.wait()
        # Drop the returned ACK but verify the actual snapshot already contains it.
        await self.submit("waiting-id", "waiting")
        disk = self.service.store.load()["desktop"]["input_commands"]
        self.assertEqual([entry["id"] for entry in disk], ["first-id", "waiting-id"])
        retry = await self.submit("waiting-id", "waiting")
        self.assertEqual(retry["receipt"]["status"], "queued")
        with self.assertRaises(ValueError):
            await self.submit("waiting-id", "different input")
        await self.service.dispatch("cancel")
        self.assertEqual(self.calls, ["first"])
        await self.service.close()
        restarted = await self.open_service()
        self.assertTrue(restarted.state()["queue_paused"])
        self.assertIsNone(restarted.task)
        await self.submit("waiting-id", "waiting", restarted)
        self.assertIsNone(restarted.task)
        await restarted.dispatch("resume_queue")
        await restarted.task
        self.assertEqual(self.calls, ["first", "waiting"])

    async def test_frozen_attachment_grants_old_hash_and_stale_write_fails(self):
        file = self.cwd / "source.txt"
        file.write_bytes(b"frozen contents")
        revision = hashlib.sha256(file.read_bytes()).hexdigest()
        await self.service.dispatch("pause_queue")
        await self.submit("attached", "Update source", attachments=[{"path": "source.txt", "revision": revision}])
        file.write_bytes(b"later external edit")
        async def write(agent, prompt):
            self.assertIn("frozen contents", prompt)
            self.assertNotIn("later external edit", prompt)
            result = await agent.executor.execute(Command("write-stale", "write", path="source.txt", content="model edit"))
            self.assertFalse(result.success)
            self.assertIn("revision_conflict", result.error)
            raise RuntimeError(result.error)
        self.runner = write
        await self.service.dispatch("resume_queue")
        await self.service.task
        self.assertEqual(file.read_bytes(), b"later external edit")
        receipt = await self.service.dispatch("get_input_receipt", {"session_id": self.service.session_id, "command_id": "attached"})
        self.assertEqual(receipt["receipt"]["status"], "failed")
        self.assertTrue(self.service.state()["queue_paused"])


class ProviderCrossReview(unittest.IsolatedAsyncioTestCase):
    async def test_split_reasoning_two_native_calls_and_plan_evidence(self):
        with tempfile.TemporaryDirectory(prefix="thinkflow-protocol-review-") as name:
            cwd = Path(name).resolve()
            (cwd / "a.txt").write_text("fixture content", encoding="utf8")
            config = AgentConfig(cwd=str(cwd), provider=ProviderConfig(
                base_url="http://127.0.0.1:1", model="deepseek-local-fixture", thinking_mode="enabled"))
            events, requests = [], []
            agent = AgentLoop(config, event_sink=lambda event: events.append(copy.deepcopy(event)))
            plan = {"explanation": "Local validation", "steps": [{"id": "read", "title": "Read fixture",
                "acceptance": "read receipt exists", "status": "completed", "evidence": ["read-fixture"]}]}
            async def handler(request):
                body = json.loads(request.content)
                requests.append(body)
                if len(requests) == 1:
                    frames = [
                        {"delta": {"reasoning_content": "reasoning "}},
                        {"delta": {"reasoning_content": "continued", "content": "Inspecting."}},
                        {"delta": {"tool_calls": [{"index": 0, "id": "read-fixture", "function": {"name": "read", "arguments": '{"path":'}}]}},
                        {"delta": {"tool_calls": [{"index": 1, "id": "plan-fixture", "function": {"name": "update_plan", "arguments": json.dumps(plan)}}]}},
                        {"delta": {"tool_calls": [{"index": 0, "function": {"arguments": '"a.txt"}'}}]}},
                        {"delta": {}, "finish_reason": "tool_calls"},
                    ]
                else:
                    frames = [{"delta": {"reasoning_content": "final reasoning", "content": "Finished."}, "finish_reason": "stop"}]
                raw = "".join("data: " + json.dumps({"choices": [frame]}) + "\n\n" for frame in frames) + "data: [DONE]\n\n"
                return httpx.Response(200, text=raw)
            await agent.client.aclose()
            agent.client = httpx.AsyncClient(base_url="http://127.0.0.1:1", transport=httpx.MockTransport(handler))
            try:
                await agent.run("inspect fixture")
                self.assertEqual(len(requests), 2)
                assistant = next(message for message in requests[1]["messages"] if message.get("tool_calls"))
                self.assertEqual(assistant["reasoning_content"], "reasoning continued")
                self.assertEqual(assistant["content"], "Inspecting.")
                self.assertEqual([item["id"] for item in assistant["tool_calls"]], ["read-fixture", "plan-fixture"])
                self.assertEqual(agent.task_plan, plan)
                self.assertTrue(any(event["type"] == "plan_updated" for event in events))
                self.assertTrue(all(message.get("tool_call_id") in {"read-fixture", "plan-fixture"}
                                    for message in requests[1]["messages"] if message["role"] == "tool"))
            finally:
                await agent.close()


if __name__ == "__main__":
    unittest.main(verbosity=2)
