"""Run optional delivery checks through both production tool protocols."""
import asyncio
import json
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import AsyncMock, patch

import httpx

from src.agent_loop import AgentConfig, AgentLoop
from src.delivery import package_projects
from src.provider import ProviderConfig


def native(name, ident, **arguments):
    return {"tool_calls": [{"index": 0, "id": ident, "function": {
        "name": name, "arguments": json.dumps(arguments)}}]}


class DeliveryTests(unittest.IsolatedAsyncioTestCase):
    def scripted(self, *responses, **config):
        directory = Path(self.enterContext(tempfile.TemporaryDirectory()))
        requests = []

        def reply(request):
            delta = responses[len(requests)]
            if isinstance(delta, str):
                delta = {"content": delta}
            requests.append(json.loads(request.content))
            finish = "tool_calls" if "tool_calls" in delta else "stop"
            choice = {"choices": [{"delta": delta, "finish_reason": finish}]}
            return httpx.Response(200, content=f"data: {json.dumps(choice)}\n\ndata: [DONE]\n\n")

        client = httpx.AsyncClient(base_url="https://fixture.invalid", transport=httpx.MockTransport(reply))
        with patch.object(AgentLoop, "_new_http_client", return_value=client):
            agent = AgentLoop(AgentConfig(cwd=str(directory), provider=ProviderConfig(
                model="fixture", format="openai"), **config), event_sink=lambda _: None)
        self.addAsyncCleanup(agent.close)
        return agent, directory, requests

    def package(self, directory, script):
        (directory / "node_modules").mkdir(parents=True)
        (directory / "package.json").write_text(json.dumps({"scripts": {"build": "node build.cjs"}}))
        (directory / "build.cjs").write_text(script, encoding="utf-8")

    @unittest.skipUnless(shutil.which("npm"), "package checks require npm")
    async def test_independent_packages_and_native_fix_only_rebuild_dirty_project(self):
        agent, root, requests = self.scripted(
            native("write", "n1", path="a/input", content="A"),
            '<tf-write id="1" path="b/input">B</tf-write>Done.',
            native("append", "n2", path="b/input", content="!"),
            "Fixed.", delivery_verify=True)
        script = """const fs = require('fs');
const data = fs.readFileSync('input', 'utf8');
fs.appendFileSync('builds', data + '\\n');
process.exitCode = data === 'B' ? 1 : 0;"""
        self.package(root / "a", script)
        self.package(root / "b", script)
        await agent.run("Write inputs and verify both packages.")
        self.assertEqual(agent.stopped_reason, "completed")
        self.assertEqual(len(requests), 4)
        self.assertEqual((root / "a/builds").read_text().splitlines(), ["A"])
        self.assertEqual((root / "b/builds").read_text().splitlines(), ["B", "B!"])

    async def test_native_copy_checks_only_its_destination_and_approval_denial_remains_failed(self):
        agent, root, requests = self.scripted(native("copy", "n1", path="a/source", dest="b/result"),
                                              "Done.", "Still done.", delivery_verify=True)
        for name in ("a", "b"):
            self.package(root / name, "")
        (root / "a/source").write_text("ok")
        agent.config.security.approval_mode = "request_all"
        agent.approval_handler = AsyncMock(return_value=False)
        with patch.object(agent.executor, "_run_process", new_callable=AsyncMock) as execute:
            await agent.run("Copy the file.")
            execute.assert_not_awaited()
        agent.approval_handler.assert_awaited_once_with("bash", {"cmd": "npm run build", "cwd": str(root / "b")})
        self.assertIn("APPROVAL DENIED", str(requests[2]))
        self.assertEqual(agent.stopped_reason, "delivery_verification_failed")
        self.assertIn(str(root / "b"), agent.last_error)

    async def test_build_honors_bash_policy_through_regular_executor(self):
        agent, root, _ = self.scripted('<tf-touch id="1" path="file" />Done.', delivery_verify=True,
                                       max_delivery_fix_attempts=0)
        self.package(root, "")
        agent.config.security.bash_policy = "off"
        with patch.object(agent.executor, "_run_process", new_callable=AsyncMock) as spawn:
            await agent.run("Create the file.")
            spawn.assert_not_awaited()
        self.assertEqual(agent.stopped_reason, "delivery_verification_failed")

    @unittest.skipUnless(shutil.which("npm"), "package checks require npm")
    async def test_approved_build_receives_configured_env_and_cancellation_settles_process(self):
        agent, root, _ = self.scripted('<tf-touch id="1" path="file" />Done.', delivery_verify=True)
        self.package(root, """require('fs').writeFileSync('started', process.env.DELIVERY_TEST_LABEL);
setInterval(() => {}, 1000);""")
        agent.config.security.approval_mode = "request_all"
        agent.approval_handler = AsyncMock(return_value=True)
        agent.config.security.env_passthrough.append("DELIVERY_TEST_LABEL")
        with patch.dict("os.environ", DELIVERY_TEST_LABEL="approved"), patch.object(
                agent.executor, "_stop_process_tree", wraps=agent.executor._stop_process_tree) as stop:
            task = asyncio.create_task(agent.run("Create the file."))
            try:
                async with asyncio.timeout(10):
                    while not (root / "started").exists():
                        if task.done():
                            await task
                            self.fail(agent.last_error)
                        await asyncio.sleep(.02)
                self.assertEqual((root / "started").read_text(), "approved")
            finally:
                task.cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await task
            stop.assert_awaited_once()
            self.assertIsNotNone(stop.await_args.args[0].returncode)
        agent.approval_handler.assert_awaited_once_with("bash", {"cmd": "npm run build", "cwd": str(root)})
        self.assertEqual(agent.stopped_reason, "cancelled")

    async def test_script_only_direct_success_after_write_clears_each_path(self):
        agent, root, requests = self.scripted(
            native("write", "n1", path="one.py", content="print(1)"),
            native("bash", "n2", cmd="python one.py"),
            '<tf-write id="1" path="two.py">print(2)</tf-write>'
            '<tf-append id="2" path="one.py">\nprint(3)</tf-append>Done.',
            native("bash", "n3", cmd="python two.py"),
            native("bash", "n4", cmd="echo one.py"),
            "Done.", "I have finished.", auto_verify_runnable_artifacts=True)
        await agent.run("Write and run both scripts.")
        self.assertEqual(agent.stopped_reason, "delivery_verification_failed")
        self.assertEqual(len(requests), 7)
        self.assertIn("one.py", agent.last_error)
        self.assertNotIn("two.py", agent.last_error)
        self.assertEqual([r.id for r in agent.context.records if r.tool == "bash" and r.exit_code == 0],
                         ["n2", "n3", "n4"])

    async def test_pending_script_can_finish_with_native_execution_and_quoted_path(self):
        agent, root, _ = self.scripted(native("write", "n1", path="hello (world).py", content="print(42)"),
                                       "Done.", native("bash", "n2", cmd='python "hello (world).py"'),
                                       "Printed 42.", auto_verify_runnable_artifacts=True)
        await agent.run("Write and run the script.")
        self.assertEqual(agent.stopped_reason, "completed")
        self.assertEqual(agent.context.records[-1].stdout.strip(), "42")

    async def test_identical_file_does_not_retry_failed_build(self):
        agent, root, requests = self.scripted('<tf-write id="1" path="file">A</tf-write>Done.',
                                              '<tf-write id="2" path="file">A</tf-write>Done.',
                                              delivery_verify=True)
        self.package(root, "")
        agent.config.security.approval_mode = "request_all"
        agent.approval_handler = AsyncMock(return_value=False)
        await agent.run("Create and verify.")
        self.assertEqual(len(requests), 2)
        self.assertEqual(agent.stopped_reason, "delivery_verification_failed")
        self.assertEqual(agent.approval_handler.await_count, 1)

    async def test_replayed_bash_receipt_cannot_verify_new_version(self):
        run = native("bash", "n2", cmd="python script.py")
        agent, root, requests = self.scripted(
            native("write", "n1", path="script.py", content="print(1)"), run,
            '<tf-append id="1" path="script.py">\nprint(2)</tf-append>Edited.',
            run, "Verified.", auto_verify_runnable_artifacts=True)
        await agent.run("Create and verify.")
        self.assertEqual(len(requests), 5)
        self.assertEqual(agent.stopped_reason, "delivery_verification_failed")
        self.assertEqual([r.stdout.strip() for r in agent.context.records if r.tool == "bash"], ["1"])

    def test_manifest_search_stays_inside_workspace(self):
        with tempfile.TemporaryDirectory() as directory:
            parent = Path(directory).resolve()
            root = parent / "workspace"
            root.mkdir()
            (parent / "package.json").write_text("{}")
            self.assertEqual(package_projects(str(root), {root / "file", parent / "other"}), [])
