"""Core behavior tests for ThinkFlow."""

import asyncio
import httpx
import io
import json
import os
import sys
import tempfile
from pathlib import Path
from rich.console import Console

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.agent_loop import AgentConfig, AgentLoop
from src import renderer
from src.cli import (
    BUILTIN_SYSTEM_PROMPT,
    apply_env_defaults,
    build_model_selection_items,
    build_provider_profiles,
    build_resume_selection_items,
    build_slash_completer,
    build_thinking_selection_items,
    create_agent,
    handle_slash_command,
    load_discovered_config,
    current_permission_mode,
    cycle_permission_mode,
    resolve_system_prompt,
    selector_open_style,
    switch_model,
    write_config_template,
)
from src.compaction import COMPACTION_HEADER, CompactionConfig, compact_messages
from src.context import CommandRecord, ContextManager
from src.delivery import VerificationResult
from src.executor import ExecutionResult, Executor
from src.interfaces import ExternalInterfaces, InterfaceConfig
from src import interfaces
from src.model_registry import choose_model, merge_active_provider, provider_catalog, resolve_auto_model
from src.parser import Command, StreamingParser
from src.provider import ProviderConfig
from src.streaming import EventType, SSEStreamProcessor
from src.security import SecurityPolicy
from src.session import SessionStore
from src.runtime_context import cwd_session_path, thinkflow_home
from src.skills import SkillConfig, SkillManager
from src.text_filter import SafeTextStreamFilter
from src.tool_registry import (
    TOOL_FLOW_BLOCKING,
    TOOL_FLOW_CONFIRM,
    TOOL_FLOW_DELAYED,
    TOOL_KIND_INPUT,
    TOOL_KIND_OUTPUT,
    TOOL_RISK_HIGH,
    TOOL_RISK_LOW,
)


def test_executor_resolves_relative_paths_to_cwd():
    async def scenario():
        with tempfile.TemporaryDirectory() as tmp:
            executor = Executor(cwd=tmp)
            result = await executor.execute(Command(
                id="1",
                tool="write",
                path="nested/file.txt",
                content="hello",
            ))
            assert result.success, result.error
            expected = os.path.join(tmp, "nested", "file.txt")
            assert os.path.exists(expected)

            read_result = await executor.read("nested/file.txt")
            assert read_result.success
            assert read_result.content == "hello"

            edit_result = await executor.execute(Command(
                id="2",
                tool="edit",
                path="nested/file.txt",
                old_text="hello",
                new_text="hello world",
            ))
            assert edit_result.success, edit_result.error
            with open(expected, "r", encoding="utf-8") as f:
                assert f.read() == "hello world"

    asyncio.run(scenario())


def test_executor_append_touch_and_copy():
    async def scenario():
        with tempfile.TemporaryDirectory() as tmp:
            executor = Executor(cwd=tmp)
            write_result = await executor.execute(Command(
                id="1",
                tool="write",
                path="source.txt",
                content="hello",
            ))
            assert write_result.success, write_result.error

            append_result = await executor.execute(Command(
                id="2",
                tool="append",
                path="source.txt",
                content=" world",
            ))
            assert append_result.success, append_result.error

            touch_result = await executor.execute(Command(
                id="3",
                tool="touch",
                path="empty.txt",
            ))
            assert touch_result.success, touch_result.error

            copy_result = await executor.execute(Command(
                id="4",
                tool="copy",
                path="source.txt",
                dest="copy.txt",
            ))
            assert copy_result.success, copy_result.error

            with open(os.path.join(tmp, "source.txt"), "r", encoding="utf-8") as f:
                assert f.read() == "hello world"
            assert os.path.exists(os.path.join(tmp, "empty.txt"))
            with open(os.path.join(tmp, "copy.txt"), "r", encoding="utf-8") as f:
                assert f.read() == "hello world"

    asyncio.run(scenario())


def test_executor_rejects_empty_write_and_append():
    async def scenario():
        with tempfile.TemporaryDirectory() as tmp:
            executor = Executor(cwd=tmp)
            empty_write = await executor.execute(Command(
                id="1",
                tool="write",
                path="empty.txt",
                content="   \n",
            ))
            assert not empty_write.success
            assert "内容为空" in empty_write.error
            assert not os.path.exists(os.path.join(tmp, "empty.txt"))

            empty_append = await executor.execute(Command(
                id="2",
                tool="append",
                path="append.txt",
                content="",
            ))
            assert not empty_append.success
            assert "内容为空" in empty_append.error

            touch = await executor.execute(Command(
                id="3",
                tool="touch",
                path="empty.txt",
            ))
            assert touch.success, touch.error
            assert os.path.exists(os.path.join(tmp, "empty.txt"))

    asyncio.run(scenario())

def test_executor_read_truncates_large_files():
    async def scenario():
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "large.txt")
            with open(path, "w", encoding="utf-8") as f:
                f.write("abcdef")

            executor = Executor(cwd=tmp, max_read_chars=3)
            result = await executor.read("large.txt")
            assert result.success
            assert result.content == "abc"
            assert result.truncated is True

    asyncio.run(scenario())


def test_executor_blocks_outside_cwd_by_default():
    async def scenario():
        with tempfile.TemporaryDirectory() as tmp:
            executor = Executor(cwd=tmp)
            outside = os.path.join(os.path.dirname(tmp), "outside-thinkflow-test.txt")
            result = await executor.execute(Command(
                id="1",
                tool="write",
                path=outside,
                content="nope",
            ))
            assert not result.success
            assert "允许范围" in result.error
            assert not os.path.exists(outside)

    asyncio.run(scenario())


def test_executor_blocks_sensitive_reads_by_default():
    async def scenario():
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, ".env")
            with open(path, "w", encoding="utf-8") as f:
                f.write("SECRET=1")

            executor = Executor(cwd=tmp)
            result = await executor.read(".env")
            assert not result.success
            assert "疑似密钥文件" in result.error

    asyncio.run(scenario())


def test_executor_blocks_config_json_and_redacts_secret_fields():
    async def scenario():
        with tempfile.TemporaryDirectory() as tmp:
            with open(os.path.join(tmp, "config.json"), "w", encoding="utf-8") as f:
                f.write('{"api_key":"secret-value"}')
            with open(os.path.join(tmp, "notes.json"), "w", encoding="utf-8") as f:
                f.write('{"token":"secret-token","safe":"ok"}')

            executor = Executor(cwd=tmp)
            blocked = await executor.read("config.json")
            assert not blocked.success
            assert "疑似密钥文件" in blocked.error

            redacted = await executor.read("notes.json")
            assert redacted.success
            assert "secret-token" not in redacted.content
            assert "[REDACTED]" in redacted.content

            grepped = await executor.grep("token", ".", "*.json")
            assert grepped.success
            assert "secret-token" not in grepped.content

    asyncio.run(scenario())


def test_executor_can_explicitly_allow_sensitive_reads():
    async def scenario():
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, ".env")
            with open(path, "w", encoding="utf-8") as f:
                f.write("SECRET=1")

            policy = SecurityPolicy(allowed_roots=[tmp], allow_sensitive_paths=True)
            executor = Executor(cwd=tmp, security=policy)
            result = await executor.read(".env")
            assert result.success
            assert result.content == "SECRET=1"

    asyncio.run(scenario())


def test_bash_safe_policy_blocks_dangerous_commands():
    async def scenario():
        with tempfile.TemporaryDirectory() as tmp:
            executor = Executor(cwd=tmp)
            result = await executor.execute(Command(
                id="1",
                tool="bash",
                cmd="rm -rf /",
            ))
            assert not result.success
            assert result.exit_code == 126
            assert "危险命令" in result.error

    asyncio.run(scenario())


def test_bash_env_does_not_leak_api_key_by_default():
    async def scenario():
        old = os.environ.get("THINKFLOW_API_KEY")
        os.environ["THINKFLOW_API_KEY"] = "secret-value"
        try:
            with tempfile.TemporaryDirectory() as tmp:
                executor = Executor(cwd=tmp)
                result = await executor.execute(Command(
                    id="1",
                    tool="bash",
                    cmd='python -c "import os; print(os.environ.get(\'THINKFLOW_API_KEY\'))"',
                ))
                assert result.success
                assert "secret-value" not in result.stdout
        finally:
            if old is None:
                os.environ.pop("THINKFLOW_API_KEY", None)
            else:
                os.environ["THINKFLOW_API_KEY"] = old

    asyncio.run(scenario())


def test_approval_request_all_blocks_high_risk_tools():
    async def scenario():
        with tempfile.TemporaryDirectory() as tmp:
            config = AgentConfig(
                provider=ProviderConfig(api_key="test-key", format="openai"),
                cwd=tmp,
                security=SecurityPolicy(approval_mode="request_all"),
            )
            agent = AgentLoop(config)
            try:
                result = await agent._execute_command(Command(
                    id="1",
                    tool="bash",
                    cmd="echo blocked",
                ))
                assert not result.success
                assert "APPROVAL REQUIRED" in result.error
                assert agent.context.records[-1].risk == "high"
            finally:
                await agent.close()

    asyncio.run(scenario())


def test_approval_approve_all_allows_high_risk_tools_through_policy():
    async def scenario():
        with tempfile.TemporaryDirectory() as tmp:
            config = AgentConfig(
                provider=ProviderConfig(api_key="test-key", format="openai"),
                cwd=tmp,
                security=SecurityPolicy(approval_mode="approve_all", bash_policy="safe"),
            )
            agent = AgentLoop(config)
            try:
                result = await agent._execute_command(Command(
                    id="1",
                    tool="bash",
                    cmd="echo allowed",
                ))
                assert result.success, result.error
                assert "allowed" in result.stdout
            finally:
                await agent.close()

    asyncio.run(scenario())



def test_permission_modes_map_and_read_only_blocks_side_effects():
    async def scenario():
        with tempfile.TemporaryDirectory() as tmp:
            read_only = SecurityPolicy.from_config({"security": {"profile": "read-only"}}, tmp)
            assert read_only.read_only is True
            executor = Executor(cwd=tmp, security=read_only)
            result = await executor.execute(Command(id="1", tool="write", path="a.txt", content="x"))
            assert not result.success
            assert "read-only" in result.error
            assert not os.path.exists(os.path.join(tmp, "a.txt"))

            open_policy = SecurityPolicy.from_config({"security": {"profile": "danger-full-access"}}, tmp)
            assert open_policy.allowed_roots is None
            assert open_policy.bash_policy == "unrestricted"
            assert open_policy.approval_mode == "approve_all"

            config = AgentConfig(provider=ProviderConfig(api_key="test-key", format="openai"), cwd=tmp)
            agent = AgentLoop(config)
            try:
                assert current_permission_mode(agent) == "workspace-write"
                assert cycle_permission_mode(agent) == "danger-full-access"
                assert current_permission_mode(agent) == "danger-full-access"
                assert cycle_permission_mode(agent) == "read-only"
                assert agent.config.security.read_only is True
            finally:
                await agent.close()

    asyncio.run(scenario())

def test_security_open_profile_is_explicitly_permissive():
    with tempfile.TemporaryDirectory() as tmp:
        policy = SecurityPolicy.from_config({"security": {"profile": "open"}}, tmp)
        assert policy.allowed_roots is None
        assert policy.allow_sensitive_paths is True
        assert policy.bash_policy == "unrestricted"
        assert policy.env_passthrough == ["*"]


def test_context_snapshot_roundtrip_and_mark_injected():
    ctx = ContextManager()
    command = Command(id="7", tool="write", path="a.txt", content="body")
    result = ExecutionResult(success=True, tool="write", path="a.txt", bytes_written=4)
    ctx.record(command, result)
    assert ctx.start_stamp == 8

    ctx.mark_injected("7")
    assert ctx.records[0].injected == 1

    restored = ContextManager.from_dict(ctx.to_dict())
    assert restored.start_stamp == 8
    assert restored.records[0].id == "7"
    assert restored.records[0].injected == 1


def test_command_ledger_injection_includes_hash_flow_risk_and_summary():
    async def scenario():
        with tempfile.TemporaryDirectory() as tmp:
            agent = AgentLoop(AgentConfig(
                provider=ProviderConfig(api_key="test-key", format="openai"),
                cwd=tmp,
            ))
            try:
                result = await agent.executor.execute(Command(
                    id="1",
                    tool="write",
                    path="ledger.txt",
                    content="ledger content",
                ))
                agent.context.record(
                    Command(id="1", tool="write", path="ledger.txt", content="ledger content"),
                    result,
                    flow=agent.tool_registry.flow("write"),
                    risk=agent.tool_registry.risk("write"),
                )
                ledger = agent.context.build_injection()
                assert "THINKFLOW COMMAND LEDGER" in ledger
                assert 'flow="delayed"' in ledger
                assert 'risk="low"' in ledger
                assert 'hash="' in ledger
                assert "summary: write" in ledger
            finally:
                await agent.close()

    asyncio.run(scenario())

def test_agent_snapshot_roundtrip():
    async def scenario():
        config = AgentConfig(
            provider=ProviderConfig(api_key="test-key", format="openai"),
            cwd=".",
        )
        agent = AgentLoop(config)
        try:
            agent.messages.append({"role": "user", "content": "hello"})
            agent._executed_ids.add("1")
            agent._turn_count = 3
            snapshot = agent.to_snapshot()
        finally:
            await agent.close()

        restored = AgentLoop(config)
        try:
            restored.load_snapshot(snapshot)
            assert restored.messages == [{"role": "user", "content": "hello"}]
            assert restored._executed_ids == {"1"}
            assert restored._turn_count == 3
        finally:
            await restored.close()

    asyncio.run(scenario())


def test_agent_keeps_system_prompt_stable_and_appends_runtime_status():
    config = AgentConfig(
        provider=ProviderConfig(api_key="test-key", format="openai"),
        cwd=".",
        system_prompt="",
    )
    agent = AgentLoop(config)
    try:
        assert agent.build_system_prompt() == ""
        agent.config.system_prompt = "custom"
        assert agent.build_system_prompt() == "custom"
        agent.context.next_stamp = 41
        body1 = agent.build_request_body(agent.build_system_prompt())
        agent.context.next_stamp = 42
        body2 = agent.build_request_body(agent.build_system_prompt())
        assert body1["messages"][:-1] == body2["messages"][:-1]
        assert body1["messages"][0] == {"role": "system", "content": "custom"}
        assert body2["messages"][0] == {"role": "system", "content": "custom"}
        assert "41" in body1["messages"][-1]["content"]
        assert "42" in body2["messages"][-1]["content"]
        assert body1["messages"][-1]["content"] != body2["messages"][-1]["content"]
        assert len(agent.messages) == 0
    finally:
        asyncio.run(agent.close())

def test_traditional_read_tool_uses_executor_cwd():
    async def scenario():
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "note.txt")
            with open(path, "w", encoding="utf-8") as f:
                f.write("read me")

            config = AgentConfig(
                provider=ProviderConfig(api_key="test-key", format="openai"),
                cwd=tmp,
            )
            agent = AgentLoop(config)
            old_console = renderer.console
            output = io.StringIO()
            renderer.console = Console(file=output, force_terminal=False, theme=renderer.THEME)
            try:
                await agent._handle_traditional_tools([{
                    "name": "read",
                    "id": "call_1",
                    "input": '{"path":"note.txt"}',
                }])
                assert agent.messages[-2]["tool_calls"][0]["id"] == "call_1"
                assert agent.messages[-1]["role"] == "tool"
                assert agent.messages[-1]["content"] == "read me"
            finally:
                renderer.console = old_console
                await agent.close()

    asyncio.run(scenario())


def test_executor_list_glob_and_grep_tools():
    async def scenario():
        with tempfile.TemporaryDirectory() as tmp:
            os.makedirs(os.path.join(tmp, "src"), exist_ok=True)
            with open(os.path.join(tmp, "src", "app.py"), "w", encoding="utf-8") as f:
                f.write("print('needle')\n")
            with open(os.path.join(tmp, "README.md"), "w", encoding="utf-8") as f:
                f.write("hello\n")

            executor = Executor(cwd=tmp)
            listed = await executor.list_files(".", recursive=False)
            assert listed.success
            assert "src/" in listed.content
            assert "README.md" in listed.content

            globbed = await executor.glob("src/*.py")
            assert globbed.success
            assert "src/app.py" in globbed.content

            grepped = await executor.grep("needle", ".", "*.py")
            assert grepped.success
            assert "src/app.py:1" in grepped.content

    asyncio.run(scenario())


def test_traditional_tools_cover_main_agent_actions():
    async def scenario():
        with tempfile.TemporaryDirectory() as tmp:
            config = AgentConfig(
                provider=ProviderConfig(api_key="test-key", format="openai"),
                cwd=tmp,
            )
            agent = AgentLoop(config)
            old_console = renderer.console
            output = io.StringIO()
            renderer.console = Console(file=output, force_terminal=False, theme=renderer.THEME)
            try:
                await agent._handle_traditional_tools([
                    {"name": "write", "id": "call_write", "input": '{"path":"a.txt","content":"needle"}'},
                    {"name": "append", "id": "call_append", "input": '{"path":"a.txt","content":" plus"}'},
                    {"name": "touch", "id": "call_touch", "input": '{"path":"empty.txt"}'},
                    {"name": "copy", "id": "call_copy", "input": '{"path":"a.txt","dest":"b.txt"}'},
                    {"name": "pwd", "id": "call_pwd", "input": '{}'},
                    {"name": "list_files", "id": "call_list", "input": '{"path":"."}'},
                    {"name": "grep", "id": "call_grep", "input": '{"pattern":"needle","path":".","file_glob":"*.txt"}'},
                    {"name": "bash", "id": "call_bash", "input": '{"cmd":"python -c \\"print(123)\\""}'},
                ])
                assert os.path.exists(os.path.join(tmp, "a.txt"))
                assert os.path.exists(os.path.join(tmp, "empty.txt"))
                assert os.path.exists(os.path.join(tmp, "b.txt"))
                tool_results = [m["content"] for m in agent.messages if m.get("role") == "tool"]
                assert any("a.txt" in content for content in tool_results)
                assert any(tmp in content for content in tool_results)
                assert any("needle" in content for content in tool_results)
                assert any("123" in content for content in tool_results)
            finally:
                renderer.console = old_console
                await agent.close()

    asyncio.run(scenario())


def test_tool_registry_exposes_open_interfaces():
    async def scenario():
        config = AgentConfig(
            provider=ProviderConfig(api_key="test-key", format="openai"),
            cwd=".",
        )
        agent = AgentLoop(config)
        try:
            names = [schema["name"] for schema in agent.tool_registry.schemas()]
            assert "read" in names
            assert "pwd" in names
            assert "append" in names
            assert "touch" in names
            assert "copy" in names
            assert "web_search" in names
            assert "fetch_url" in names
            assert "image_generate" in names
            assert "list_skills" in names
            assert "read_skill" in names
            assert any(tool["function"]["name"] == "web_search" for tool in agent.tool_registry.openai_tools())
            assert any(tool["name"] == "read_skill" for tool in agent.tool_registry.anthropic_tools())
            assert agent.tool_registry.kind("read") == TOOL_KIND_INPUT
            assert agent.tool_registry.kind("write") == TOOL_KIND_OUTPUT
            assert agent.tool_registry.flow("write") == TOOL_FLOW_DELAYED
            assert agent.tool_registry.flow("read") == TOOL_FLOW_BLOCKING
            assert agent.tool_registry.flow("bash") == TOOL_FLOW_BLOCKING
            assert agent.tool_registry.flow("custom_shell") == TOOL_FLOW_CONFIRM
            assert agent.tool_registry.risk("write") == TOOL_RISK_LOW
            assert agent.tool_registry.risk("bash") == TOOL_RISK_HIGH
        finally:
            await agent.close()

    asyncio.run(scenario())


def test_skill_manager_reads_codex_and_claude_style_skills():
    with tempfile.TemporaryDirectory() as tmp:
        codex_skill = os.path.join(tmp, ".agents", "skills", "writer")
        claude_skill = os.path.join(tmp, ".claude", "skills", "reviewer")
        claude_command = os.path.join(tmp, ".claude", "commands")
        os.makedirs(codex_skill, exist_ok=True)
        os.makedirs(claude_skill, exist_ok=True)
        os.makedirs(claude_command, exist_ok=True)
        with open(os.path.join(codex_skill, "SKILL.md"), "w", encoding="utf-8") as f:
            f.write("---\nname: writer\ndescription: Write focused prose.\n---\n\nUse concise prose.\n")
        with open(os.path.join(claude_skill, "SKILL.md"), "w", encoding="utf-8") as f:
            f.write("---\nname: reviewer\ndescription: Review code.\n---\n\nFind bugs first.\n")
        with open(os.path.join(claude_command, "audit.md"), "w", encoding="utf-8") as f:
            f.write("# Audit Command\nRun an audit.\n")

        manager = SkillManager(tmp, SkillConfig(roots=[]))
        names = [skill.name for skill in manager.list_skills()]
        assert "writer" in names
        assert "reviewer" in names
        assert "claude-command:audit" in names
        body = manager.read_skill("writer")
        assert "Use concise prose." in body


def test_interfaces_block_private_fetch_and_explain_disabled_image_generation():
    async def scenario():
        with tempfile.TemporaryDirectory() as tmp:
            interfaces = ExternalInterfaces(InterfaceConfig.from_config({}), cwd=tmp)
            fetched = await interfaces.fetch_url({"url": "http://127.0.0.1:9"})
            assert "默认拒绝" in fetched
            image = await interfaces.image_generate({"prompt": "a calm test image"})
            assert "尚未配置生成器" in image
            outside = os.path.join(os.path.dirname(tmp), "outside-image.png")
            configured = ExternalInterfaces(InterfaceConfig.from_config({
                "interfaces": {
                    "image_generation": {
                        "enabled": True,
                        "provider": "command",
                        "command": [],
                    }
                }
            }), cwd=tmp)
            blocked = await configured.image_generate({"prompt": "x", "output_path": outside})
            assert "拒绝写入 cwd 外路径" in blocked

    asyncio.run(scenario())


def test_interfaces_block_dns_private_hosts():
    old_getaddrinfo = interfaces.socket.getaddrinfo

    def fake_getaddrinfo(*_args, **_kwargs):
        return [(None, None, None, "", ("127.0.0.1", 0))]

    interfaces.socket.getaddrinfo = fake_getaddrinfo
    try:
        assert interfaces._is_private_host("example.test") is True
    finally:
        interfaces.socket.getaddrinfo = old_getaddrinfo


def test_configured_custom_tool_executes_as_native_tool():
    async def scenario():
        with tempfile.TemporaryDirectory() as tmp:
            script = os.path.join(tmp, "echo_tool.py")
            with open(script, "w", encoding="utf-8") as f:
                f.write(
                    "import json, sys\n"
                    "data = json.load(sys.stdin)\n"
                    "print('hello ' + data.get('name', 'world'))\n"
                )
            config = {
                "interfaces": {
                    "custom_tools": [
                        {
                            "name": "hello_tool",
                            "description": "Say hello.",
                            "parameters": {
                                "type": "object",
                                "properties": {"name": {"type": "string"}},
                            },
                            "command": [sys.executable, script],
                        }
                    ]
                }
            }
            agent = AgentLoop(AgentConfig(
                provider=ProviderConfig(api_key="test-key", format="openai"),
                cwd=tmp,
                interfaces=InterfaceConfig.from_config(config),
            ))
            try:
                names = [schema["name"] for schema in agent.tool_registry.schemas()]
                assert "hello_tool" in names
                assert agent.tool_registry.kind("hello_tool") == "exec"
                result = await agent._execute_traditional_tool("hello_tool", {"name": "thinkflow"})
                assert result == "hello thinkflow"
            finally:
                await agent.close()

    asyncio.run(scenario())


def test_openai_stream_split_tool_arguments():
    class FakeResponse:
        async def aiter_lines(self):
            lines = [
                'data: {"choices":[{"delta":{"tool_calls":[{"index":0,"id":"call_1","function":{"name":"read","arguments":"{\\"pa"}}]}}]}',
                'data: {"choices":[{"delta":{"tool_calls":[{"index":0,"function":{"arguments":"th\\":\\"README.md\\"}"}}]}}]}',
                'data: {"choices":[{"delta":{},"finish_reason":"tool_calls"}]}',
            ]
            for line in lines:
                yield line

    async def scenario():
        config = AgentConfig(
            provider=ProviderConfig(api_key="test-key", format="openai"),
            cwd=".",
        )
        agent = AgentLoop(config)
        try:
            events = [event async for event in agent._process_stream(FakeResponse())]
            assert [event.type for event in events] == [
                EventType.TOOL_USE_START,
                EventType.TOOL_USE_DELTA,
                EventType.TOOL_USE_DELTA,
                EventType.MESSAGE_STOP,
            ]
            assert events[0].tool_id == "call_1"
            assert events[1].tool_input + events[2].tool_input == '{"path":"README.md"}'
            assert events[-1].finish_reason == "tool_calls"
        finally:
            await agent.close()

    asyncio.run(scenario())


def test_openai_stream_bad_json_reports_error_event():
    class FakeResponse:
        async def aiter_lines(self):
            yield "data: {not json}"

    async def scenario():
        config = AgentConfig(
            provider=ProviderConfig(api_key="test-key", format="openai"),
            cwd=".",
        )
        agent = AgentLoop(config)
        try:
            events = [event async for event in agent._process_stream(FakeResponse())]
            assert events[0].type == EventType.ERROR
            assert "JSON decode" in events[0].error
        finally:
            await agent.close()

    asyncio.run(scenario())


def test_agent_sets_last_error_on_stream_event_error():
    class FakeResponse:
        async def aiter_lines(self):
            yield "data: {not json}"

        async def aclose(self):
            pass

    async def scenario():
        config = AgentConfig(
            provider=ProviderConfig(api_key="test-key", format="openai"),
            cwd=".",
        )
        agent = AgentLoop(config)
        old_console = renderer.console
        renderer.console = Console(file=io.StringIO(), force_terminal=False, theme=renderer.THEME)

        async def fake_send(_path, _body):
            return FakeResponse()

        agent._send_stream_request = fake_send
        try:
            await agent.run("bad stream")
            assert "JSON decode" in agent.last_error
        finally:
            renderer.console = old_console
            await agent.close()

    asyncio.run(scenario())


def test_anthropic_message_delta_preserves_stop_reason():
    processor = SSEStreamProcessor()
    event = processor._parse_event(
        "message_delta",
        {"type": "message_delta", "delta": {"stop_reason": "max_tokens"}},
    )
    assert event is not None
    assert event.type == EventType.MESSAGE_STOP
    assert event.finish_reason == "max_tokens"


def test_env_defaults_do_not_override_config():
    old = os.environ.get("THINKFLOW_MODEL")
    os.environ["THINKFLOW_MODEL"] = "env-model"
    try:
        config = apply_env_defaults({"model": "config-model"})
        assert config["model"] == "config-model"
        config = apply_env_defaults({})
        assert config["model"] == "env-model"
    finally:
        if old is None:
            os.environ.pop("THINKFLOW_MODEL", None)
        else:
            os.environ["THINKFLOW_MODEL"] = old




def test_global_session_and_context_injection_use_thinkflow_home():
    old_home = os.environ.get("THINKFLOW_HOME")
    try:
        with tempfile.TemporaryDirectory() as tmp_home, tempfile.TemporaryDirectory() as tmp_work:
            os.environ["THINKFLOW_HOME"] = tmp_home
            home = thinkflow_home()
            assert home.resolve() == Path(tmp_home).resolve()
            (Path(tmp_home) / "AGENTS.md").write_text("GLOBAL_CTX", encoding="utf-8")
            (Path(tmp_work) / "AGENTS.md").write_text("WORKSPACE_CTX", encoding="utf-8")

            store = SessionStore(cwd=tmp_work)
            assert (Path(tmp_home) / "sessions").resolve() in store.path.resolve().parents
            assert store.path.resolve() == cwd_session_path(tmp_work).resolve()

            prompt = resolve_system_prompt({"context": {"enabled": True}}, cwd=tmp_work)
            assert "GLOBAL_CTX" in prompt
            assert "WORKSPACE_CTX" in prompt
            assert "ThinkFlow injected context" in prompt
    finally:
        if old_home is None:
            os.environ.pop("THINKFLOW_HOME", None)
        else:
            os.environ["THINKFLOW_HOME"] = old_home

def test_system_prompt_defaults_to_builtin_and_context_is_appended():
    assert resolve_system_prompt({"context": {"enabled": False}}) == BUILTIN_SYSTEM_PROMPT
    assert "\u6c90\u96ea\u6e05\u6cfd" not in BUILTIN_SYSTEM_PROMPT
    assert "续想 agent 运行约定" in BUILTIN_SYSTEM_PROMPT
    assert "英文名是 ThinkFlow" in BUILTIN_SYSTEM_PROMPT
    assert "完成报告" in BUILTIN_SYSTEM_PROMPT
    assert "文件归纳整理" in BUILTIN_SYSTEM_PROMPT
    assert resolve_system_prompt({"use_builtin_system_prompt": True, "context": {"enabled": False}}) == BUILTIN_SYSTEM_PROMPT
    assert resolve_system_prompt({"context": {"enabled": False}}, use_builtin=True) == BUILTIN_SYSTEM_PROMPT
    assert resolve_system_prompt({"system_prompt": "custom prompt", "context": {"enabled": False}}) == "custom prompt"
    assert resolve_system_prompt({"disable_system_prompt": True}) == ""
    assert resolve_system_prompt({}, disable_system_prompt=True) == ""

    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "prompt.txt")
        with open(path, "w", encoding="utf-8") as f:
            f.write("custom prompt")
        assert resolve_system_prompt({"use_builtin_system_prompt": True, "context": {"enabled": False}}, system_prompt_path=path) == "custom prompt"


def test_compaction_keeps_recent_messages_and_summarizes_old():
    messages = [
        {"role": "user", "content": f"user message {i}"}
        for i in range(10)
    ]
    config = CompactionConfig(
        enabled=True,
        max_messages=5,
        keep_recent_messages=3,
        max_summary_chars=2000,
    )
    compacted, stats = compact_messages(messages, config)

    assert stats.changed
    assert len(compacted) == 4
    assert compacted[0]["role"] == "user"
    assert COMPACTION_HEADER in compacted[0]["content"]
    assert [m["content"] for m in compacted[-3:]] == [
        "user message 7",
        "user message 8",
        "user message 9",
    ]


def test_compaction_keeps_tool_result_with_tool_call():
    messages = [
        {"role": "user", "content": "before"},
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [{
                "id": "call_1",
                "type": "function",
                "function": {"name": "read", "arguments": "{\"path\":\"a.txt\"}"},
            }],
        },
        {"role": "tool", "tool_call_id": "call_1", "content": "file content"},
        {"role": "user", "content": "after"},
    ]
    config = CompactionConfig(enabled=True, max_messages=3, keep_recent_messages=2)
    compacted, stats = compact_messages(messages, config)

    assert stats.changed
    assert compacted[1]["role"] == "assistant"
    assert compacted[2]["role"] == "tool"
    assert compacted[2]["tool_call_id"] == "call_1"


def test_agent_manual_compaction_updates_snapshot_count():
    async def scenario():
        config = AgentConfig(
            provider=ProviderConfig(api_key="test-key", format="openai"),
            cwd=".",
            compaction=CompactionConfig(enabled=True, max_messages=3, keep_recent_messages=1),
        )
        agent = AgentLoop(config)
        try:
            agent.messages = [
                {"role": "user", "content": "one"},
                {"role": "assistant", "content": "two"},
                {"role": "user", "content": "three"},
                {"role": "assistant", "content": "four"},
            ]
            stats = agent.compact(force=True)
            snapshot = agent.to_snapshot()
            assert stats.changed
            assert snapshot["compaction_count"] == 1
            assert COMPACTION_HEADER in agent.messages[0]["content"]
        finally:
            await agent.close()

    asyncio.run(scenario())


def test_context_injection_clips_large_write_content():
    command = Command(id="1", tool="write", path="big.txt", content="x" * 5000)
    result = ExecutionResult(success=True, tool="write", path="big.txt", bytes_written=5000)
    ctx = ContextManager()
    ctx.record(command, result)
    injection = ctx.build_injection()

    assert injection is not None
    assert "THINKFLOW CLIPPED" in injection
    assert "xxxxx" in injection
    assert len(injection) < 4600


def test_write_config_template_creates_starter_config_without_key():
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "config.json")
        write_config_template(path)
        with open(path, "r", encoding="utf-8") as f:
            data = f.read()
        parsed = json.loads(data)
        assert '"api_key": ""' in data
        assert '"base_url": ""' in data
        assert '"model": ""' in data
        assert '"active_provider": ""' in data
        assert '"providers": {}' in data
        assert '"use_builtin_system_prompt": true' in data
        assert '"native_tools": []' in data
        assert '"disabled_native_tools": []' in data
        assert parsed["max_tokens"] == 100000
        assert parsed["max_auto_continues"] == 8
        assert parsed["delivery_verify"] is False
        assert parsed["auto_verify_runnable_artifacts"] is False
        assert parsed["compaction"]["max_chars"] == 200000
        assert "opencode.ai" not in data
        assert '"compaction"' in data
        try:
            write_config_template(path)
        except FileExistsError:
            pass
        else:
            raise AssertionError("write_config_template should not overwrite existing files")


def test_named_provider_profile_merges_without_overwriting_legacy_shape():
    config = {
        "active_provider": "opencode-go",
        "providers": {
            "opencode-go": {
                "provider": "openai",
                "base_url": "https://opencode.ai/zen/go",
                "api_key": "test-key",
                "model": "auto",
                "models": ["deepseek-v4-flash", "glm-5.2", "kimi-k2"],
                "model_discovery": {"enabled": True},
            }
        },
    }
    merged = merge_active_provider(config)
    assert merged["base_url"] == "https://opencode.ai/zen/go"
    assert merged["api_key"] == "test-key"
    assert merged["models"] == ["deepseek-v4-flash", "glm-5.2", "kimi-k2"]
    assert merged["model_discovery"]["enabled"] is True


def test_named_provider_profile_keeps_env_or_cli_overrides_when_profile_field_is_empty():
    config = {
        "api_key": "env-key",
        "base_url": "https://fallback.test",
        "active_provider": "opencode-go",
        "providers": {
            "opencode-go": {
                "provider": "openai",
                "base_url": "https://opencode.ai/zen/go",
                "api_key": "",
                "model": "auto",
                "models": ["glm-5.2"],
            }
        },
    }
    merged = merge_active_provider(config)
    assert merged["api_key"] == "env-key"
    assert merged["base_url"] == "https://opencode.ai/zen/go"


def test_auto_discovery_skips_unrelated_or_malformed_config_json():
    with tempfile.TemporaryDirectory() as tmp:
        bad = os.path.join(tmp, "config.json")
        good = os.path.join(tmp, "thinkflow.json")
        with open(bad, "w", encoding="utf-8") as f:
            f.write('{"app": "demo", "features": ["broken"}')
        with open(good, "w", encoding="utf-8") as f:
            json.dump({
                "provider": "openai",
                "base_url": "https://example.test",
                "api_key": "test-key",
                "model": "model",
            }, f)

        config = load_discovered_config([(bad, False), (good, False)])
        assert config["model"] == "model"


def test_auto_model_prefers_glm_then_kimi_then_deepseek():
    assert choose_model(["deepseek-v4-flash", "kimi-k2", "glm-5.2"]) == "glm-5.2"
    resolved = resolve_auto_model({
        "active_provider": "opencode-go",
        "providers": {
            "opencode-go": {
                "provider": "openai",
                "base_url": "https://opencode.ai/zen/go",
                "api_key": "test-key",
                "model": "auto",
                "models": ["deepseek-v4-flash", "kimi-k2"],
            }
        },
    })
    assert resolved["model"] == "kimi-k2"


def test_provider_request_defaults_are_protocol_neutral():
    async def scenario():
        openai_agent = AgentLoop(AgentConfig(
            provider=ProviderConfig(
                api_key="test-key",
                base_url="https://example.test/v1",
                model="model",
                format="openai",
            ),
            cwd=".",
        ))
        try:
            body = openai_agent.build_request_body("")
            assert openai_agent.get_request_path() == "/chat/completions"
            assert "stream_options" not in body
            assert body["tools"]
            openai_agent.config.provider.native_tools = ["pwd", "write"]
            body = openai_agent.build_request_body("")
            exposed = {
                tool["function"]["name"]
                for tool in body["tools"]
            }
            assert exposed == {"pwd", "write"}
            openai_agent.config.provider.native_tools = []
            openai_agent.config.provider.disabled_native_tools = ["bash"]
            body = openai_agent.build_request_body("")
            exposed = {
                tool["function"]["name"]
                for tool in body["tools"]
            }
            assert "bash" not in exposed
            openai_agent.config.provider.api_path = "/custom/chat"
            assert openai_agent.get_request_path() == "/custom/chat"
            openai_agent.config.provider.enable_native_tools = False
            body = openai_agent.build_request_body("")
            assert "tools" not in body
        finally:
            await openai_agent.close()

        anthropic_agent = AgentLoop(AgentConfig(
            provider=ProviderConfig(
                api_key="test-key",
                base_url="https://api.anthropic.com",
                model="model",
                format="anthropic",
            ),
            cwd=".",
        ))
        try:
            body = anthropic_agent.build_request_body("")
            assert anthropic_agent.get_request_path() == "/v1/messages"
            assert "thinking" not in body
            anthropic_agent.config.provider.thinking_budget = 1024
            body = anthropic_agent.build_request_body("")
            assert body["thinking"]["budget_tokens"] == 1024
        finally:
            await anthropic_agent.close()

    asyncio.run(scenario())


def test_create_agent_accepts_native_tool_string_or_list_config():
    agent = create_agent({
        "api_key": "test-key",
        "base_url": "https://example.test",
        "model": "model",
        "provider": "openai",
        "native_tools": "write",
        "disabled_native_tools": ["bash"],
    }, "")
    try:
        assert agent.config.provider.native_tools == ["write"]
        assert agent.config.provider.disabled_native_tools == ["bash"]
        assert agent.config.provider.max_tokens == 100000
        assert agent.config.max_auto_continues == 8
        assert agent.config.delivery_verify is False
        assert agent.config.auto_verify_runnable_artifacts is False
        assert agent.config.compaction.max_chars == 200000
    finally:
        asyncio.run(agent.close())


def test_safe_text_filter_streams_text_and_removes_command_blocks():
    filt = SafeTextStreamFilter()
    out = []
    out.append(filt.feed("hello "))
    out.append(filt.feed("<wri"))
    out.append(filt.feed('te id="1" path="a.txt">secret</write>'))
    out.append(filt.feed(" world"))
    out.append(filt.flush())

    assert "".join(out) == "hello  world"


def test_safe_text_filter_removes_self_closing_commands():
    filt = SafeTextStreamFilter()
    text = (
        'A <mkdir id="1" path="tmp" /> B <bash id="2" cmd="echo ok" /> '
        'C <touch id="3" path="x" /> D <copy id="4" path="a" dest="b" /> E'
    )
    assert filt.feed(text) + filt.flush() == "A  B  C  D  E"


def test_safe_text_filter_preserves_non_tool_angle_text():
    filt = SafeTextStreamFilter()
    assert filt.feed("a <world> and <writeup>") + filt.flush() == "a <world> and <writeup>"


def test_filters_preserve_fenced_tool_examples_and_strip_executable_text():
    fenced = '```xml\n<tf-write id="1" path="demo.txt">x</tf-write>\n```\n'
    filt = SafeTextStreamFilter(allow_legacy_tags=False)
    assert "<tf-write" in (filt.feed(fenced) + filt.flush())

    mixed = fenced + '<tf-write id="2" path="real.txt">y</tf-write>\nvisible'
    rendered = renderer.strip_command_blocks(mixed)
    assert '<tf-write id="1"' in rendered
    assert '<tf-write id="2"' not in rendered
    assert "visible" in rendered


def test_strict_parser_requires_tf_prefix():
    strict = StreamingParser(allow_legacy_tags=False)
    assert strict.feed('<write id="1" path="legacy.txt">no</write>') == []
    assert strict.feed('<tf-write id="1" path="new.txt">yes</tf-write>')[0].path == "new.txt"


def test_renderer_flushes_streamed_markdown():
    old_console = renderer.console
    output = io.StringIO()
    renderer.console = Console(file=output, force_terminal=False, theme=renderer.THEME)
    try:
        renderer.render_text_chunk("### Heading\n\n- item")
        renderer.flush_text()
        rendered = output.getvalue()
        assert "Heading" in rendered
        assert "item" in rendered
        assert "### Heading" not in rendered
    finally:
        renderer.flush_text()
        renderer.console = old_console


def test_renderer_tool_summary_lists_command_paths():
    old_console = renderer.console
    output = io.StringIO()
    renderer.console = Console(file=output, force_terminal=False, theme=renderer.THEME)
    try:
        renderer.render_tool_summary([
            CommandRecord(id="1", tool="write", path="notes/a.txt", status="success", bytes_written=5),
        ])
        rendered = output.getvalue()
        assert "流式工具汇总" in rendered
        assert "notes/a.txt" in rendered
    finally:
        renderer.console = old_console


def test_renderer_distinguishes_native_tool_kinds_and_running_state():
    old_console = renderer.console
    output = io.StringIO()
    renderer.console = Console(file=output, force_terminal=False, theme=renderer.THEME)
    try:
        renderer.render_tool_call("read", {"path": "README.md"})
        renderer.render_tool_result("write", "ok")
        rendered = output.getvalue()
        assert "tool_call：read" in rendered
        assert "输入" in rendered
        assert "OK" in rendered
        assert "tool_result：write" in rendered
        assert "输出" in rendered
    finally:
        renderer.console = old_console


def test_agent_notices_streaming_tool_hint_before_command_closes():
    agent = AgentLoop(AgentConfig())
    try:
        assert agent.current_text_tool_activity() == ""
        agent.note_text_tool_stream("准备写文件 <tf-write id=\"1\" path=\"a.txt\">")
        assert agent.current_text_tool_activity() == "write"
    finally:
        asyncio.run(agent.close())


def test_agent_executes_text_channel_command_without_history_leak():
    class FakeResponse:
        async def aiter_lines(self):
            lines = [
                'data: {"choices":[{"delta":{"content":"Before "}}]}',
                'data: {"choices":[{"delta":{"content":"<tf-write id=\\"1\\" path=\\"text-channel.txt\\">"}}]}',
                'data: {"choices":[{"delta":{"content":"hello</tf-write> After"}}]}',
                'data: {"choices":[{"delta":{},"finish_reason":"stop"}]}',
            ]
            for line in lines:
                yield line

        async def aclose(self):
            pass

    async def scenario():
        with tempfile.TemporaryDirectory() as tmp:
            config = AgentConfig(
                provider=ProviderConfig(api_key="test-key", format="openai"),
                cwd=tmp,
            )
            agent = AgentLoop(config)
            old_console = renderer.console
            output = io.StringIO()
            renderer.console = Console(file=output, force_terminal=False, theme=renderer.THEME)

            async def fake_send(_path, _body):
                return FakeResponse()

            agent._send_stream_request = fake_send
            try:
                await agent.run("write from text channel")
                with open(os.path.join(tmp, "text-channel.txt"), "r", encoding="utf-8") as f:
                    assert f.read() == "hello"
                assistant_messages = [
                    message.get("content", "")
                    for message in agent.messages
                    if message.get("role") == "assistant"
                ]
                assert assistant_messages == ["Before  After"]
                assert all("<tf-write" not in text for text in assistant_messages)
            finally:
                renderer.console = old_console
                await agent.close()

    asyncio.run(scenario())



def test_agent_executes_text_commands_fifo_even_when_stream_is_fast():
    class FakeResponse:
        def __init__(self, content: str):
            self.content = content

        async def aiter_lines(self):
            yield 'data: ' + json.dumps({
                "choices": [{"delta": {"content": self.content}, "finish_reason": "stop"}]
            })

        async def aclose(self):
            pass

    async def scenario():
        with tempfile.TemporaryDirectory() as tmp:
            config = AgentConfig(
                provider=ProviderConfig(api_key="test-key", format="openai"),
                cwd=tmp,
            )
            agent = AgentLoop(config)
            old_console = renderer.console
            renderer.console = Console(file=io.StringIO(), force_terminal=False, theme=renderer.THEME)
            executed: list[str] = []
            first_finished = asyncio.Event()

            async def fake_execute(command: Command) -> ExecutionResult:
                executed.append(command.id)
                if command.id == "1":
                    await asyncio.sleep(0.05)
                    first_finished.set()
                    result = ExecutionResult(success=True, tool=command.tool, stdout="first")
                else:
                    assert first_finished.is_set(), "second command ran before first finished"
                    result = ExecutionResult(success=True, tool=command.tool, stdout="second")
                agent.context.record(
                    command,
                    result,
                    flow=agent.tool_registry.flow(command.tool),
                    risk=agent.tool_registry.risk(command.tool),
                )
                return result

            responses = [
                FakeResponse('<tf-bash id="1" cmd="slow-first" /><tf-bash id="2" cmd="needs-first" />'),
                FakeResponse('done'),
            ]

            async def fake_send(_path, _body):
                return responses.pop(0)

            agent._execute_command = fake_execute
            agent._send_stream_request = fake_send
            try:
                await agent.run("run dependent commands")
                assert executed == ["1", "2"]
                assert [record.id for record in agent.context.records] == ["1", "2"]
                assert all(record.status == "success" for record in agent.context.records)
            finally:
                renderer.console = old_console
                await agent.close()

    asyncio.run(scenario())


def test_agent_records_skipped_commands_after_queue_failure():
    class FakeResponse:
        def __init__(self, content: str):
            self.content = content

        async def aiter_lines(self):
            yield 'data: ' + json.dumps({
                "choices": [{"delta": {"content": self.content}, "finish_reason": "stop"}]
            })

        async def aclose(self):
            pass

    async def scenario():
        with tempfile.TemporaryDirectory() as tmp:
            config = AgentConfig(
                provider=ProviderConfig(api_key="test-key", format="openai"),
                cwd=tmp,
            )
            agent = AgentLoop(config)
            old_console = renderer.console
            renderer.console = Console(file=io.StringIO(), force_terminal=False, theme=renderer.THEME)
            executed: list[str] = []
            seen_bodies: list[dict] = []
            command_text = (
                '<tf-bash id="1" cmd="ok" />'
                '<tf-bash id="2" cmd="fail" />'
                '<tf-bash id="3" cmd="must-not-run" />'
            )
            responses = [FakeResponse(command_text), FakeResponse('done')]

            async def fake_execute(command: Command) -> ExecutionResult:
                executed.append(command.id)
                if command.id == "1":
                    result = ExecutionResult(success=True, tool=command.tool, stdout="ok")
                elif command.id == "2":
                    result = ExecutionResult(success=False, tool=command.tool, error="boom")
                else:
                    raise AssertionError("skipped command should not be executed")
                agent.context.record(
                    command,
                    result,
                    flow=agent.tool_registry.flow(command.tool),
                    risk=agent.tool_registry.risk(command.tool),
                )
                return result

            async def fake_send(_path, body):
                seen_bodies.append(body)
                return responses.pop(0)

            agent._execute_command = fake_execute
            agent._send_stream_request = fake_send
            try:
                await agent.run("run three commands")
                assert executed == ["1", "2"]
                assert [(r.id, r.status) for r in agent.context.records] == [
                    ("1", "success"),
                    ("2", "failed"),
                    ("3", "skipped"),
                ]
                feedback = chr(10).join(
                    m.get("content", "")
                    for m in seen_bodies[1]["messages"]
                    if m.get("role") == "user"
                )
                assert 'id="2"' in feedback and 'status="failed"' in feedback
                assert 'id="3"' in feedback and 'status="skipped"' in feedback
                assert "Skipped because an earlier queued command failed" in feedback
            finally:
                renderer.console = old_console
                await agent.close()

    asyncio.run(scenario())

def test_agent_does_not_execute_fenced_text_channel_command():
    class FakeResponse:
        async def aiter_lines(self):
            lines = [
                'data: {"choices":[{"delta":{"content":"```xml\\n<tf-write id=\\"1\\" path=\\"example.txt\\">"}}]}',
                'data: {"choices":[{"delta":{"content":"nope</tf-write>\\n```\\nDone"}}]}',
                'data: {"choices":[{"delta":{},"finish_reason":"stop"}]}',
            ]
            for line in lines:
                yield line

        async def aclose(self):
            pass

    async def scenario():
        with tempfile.TemporaryDirectory() as tmp:
            config = AgentConfig(
                provider=ProviderConfig(api_key="test-key", format="openai"),
                cwd=tmp,
            )
            agent = AgentLoop(config)
            old_console = renderer.console
            renderer.console = Console(file=io.StringIO(), force_terminal=False, theme=renderer.THEME)

            async def fake_send(_path, _body):
                return FakeResponse()

            agent._send_stream_request = fake_send
            try:
                await agent.run("show example only")
                assert not os.path.exists(os.path.join(tmp, "example.txt"))
                assistant_messages = [
                    message.get("content", "")
                    for message in agent.messages
                    if message.get("role") == "assistant"
                ]
                assert "<tf-write" in assistant_messages[-1]
            finally:
                renderer.console = old_console
                await agent.close()

    asyncio.run(scenario())


def test_agent_auto_continues_after_length_finish_reason():
    class FakeResponse:
        def __init__(self, lines):
            self._lines = lines

        async def aiter_lines(self):
            for line in self._lines:
                yield line

        async def aclose(self):
            pass

    async def scenario():
        with tempfile.TemporaryDirectory() as tmp:
            responses = [
                FakeResponse([
                    'data: {"choices":[{"delta":{"content":"### Heading\\npart"},"finish_reason":"length"}]}',
                ]),
                FakeResponse([
                    'data: {"choices":[{"delta":{"content":" continued"},"finish_reason":"stop"}]}',
                ]),
            ]
            config = AgentConfig(
                provider=ProviderConfig(api_key="test-key", format="openai"),
                cwd=tmp,
                max_auto_continues=2,
            )
            agent = AgentLoop(config)
            old_console = renderer.console
            renderer.console = Console(file=io.StringIO(), force_terminal=False, theme=renderer.THEME)

            async def fake_send(_path, _body):
                return responses.pop(0)

            agent._send_stream_request = fake_send
            try:
                await agent.run("continue test")
                assert len(agent.usage.turns) == 2
                assert agent.usage.turns[0].abort_reason == "length"
                assert any(
                    "THINKFLOW CONTINUE" in message.get("content", "")
                    for message in agent.messages
                    if message.get("role") == "user"
                )
                assistant_messages = [
                    message.get("content", "")
                    for message in agent.messages
                    if message.get("role") == "assistant"
                ]
                assert assistant_messages == ["### Heading\npart", "continued"]
            finally:
                renderer.console = old_console
                await agent.close()

    asyncio.run(scenario())


def test_agent_auto_continues_after_blocking_text_command_result():
    class FakeResponse:
        def __init__(self, lines):
            self._lines = lines

        async def aiter_lines(self):
            for line in self._lines:
                yield line

        async def aclose(self):
            pass

    async def scenario():
        with tempfile.TemporaryDirectory() as tmp:
            seen_bodies = []
            responses = [
                FakeResponse([
                    'data: {"choices":[{"delta":{"content":"<tf-bash id=\\"1\\" cmd=\\"echo PROMPT_TEXT\\" />"},"finish_reason":"stop"}]}',
                ]),
                FakeResponse([
                    'data: {"choices":[{"delta":{"content":"done"},"finish_reason":"stop"}]}',
                ]),
            ]
            config = AgentConfig(
                provider=ProviderConfig(api_key="test-key", format="openai"),
                cwd=tmp,
                max_auto_continues=2,
            )
            agent = AgentLoop(config)
            old_console = renderer.console
            renderer.console = Console(file=io.StringIO(), force_terminal=False, theme=renderer.THEME)

            async def fake_send(_path, body):
                seen_bodies.append(body)
                return responses.pop(0)

            agent._send_stream_request = fake_send
            try:
                await agent.run("read then continue")
                assert len(seen_bodies) == 2
                assert len(agent.usage.turns) == 2
                assert agent.usage.turns[0].abort_reason == "end_turn"
                second_messages = seen_bodies[1]["messages"]
                assert any(
                    "PROMPT_TEXT" in message.get("content", "")
                    and "blocking tool output returned automatically" in message.get("content", "")
                    for message in second_messages
                    if message.get("role") == "user"
                )
                assert any(
                    message.get("content") == "done"
                    for message in agent.messages
                    if message.get("role") == "assistant"
                )
            finally:
                renderer.console = old_console
                await agent.close()

    asyncio.run(scenario())


def test_agent_auto_continues_after_text_read_command_result():
    class FakeResponse:
        def __init__(self, lines):
            self._lines = lines

        async def aiter_lines(self):
            for line in self._lines:
                yield line

        async def aclose(self):
            pass

    async def scenario():
        with tempfile.TemporaryDirectory() as tmp:
            with open(os.path.join(tmp, "prompt.txt"), "w", encoding="utf-8") as f:
                f.write("READ_PROMPT_TEXT")
            seen_bodies = []
            responses = [
                FakeResponse([
                    'data: {"choices":[{"delta":{"content":"<tf-read id=\\"1\\" path=\\"prompt.txt\\" />"},"finish_reason":"stop"}]}',
                ]),
                FakeResponse([
                    'data: {"choices":[{"delta":{"content":"done"},"finish_reason":"stop"}]}',
                ]),
            ]
            config = AgentConfig(
                provider=ProviderConfig(api_key="test-key", format="openai"),
                cwd=tmp,
                max_auto_continues=2,
            )
            agent = AgentLoop(config)
            old_console = renderer.console
            renderer.console = Console(file=io.StringIO(), force_terminal=False, theme=renderer.THEME)

            async def fake_send(_path, body):
                seen_bodies.append(body)
                return responses.pop(0)

            agent._send_stream_request = fake_send
            try:
                await agent.run("read prompt")
                assert len(seen_bodies) == 2
                assert any(
                    "READ_PROMPT_TEXT" in message.get("content", "")
                    for message in seen_bodies[1]["messages"]
                    if message.get("role") == "user"
                )
            finally:
                renderer.console = old_console
                await agent.close()

    asyncio.run(scenario())


def test_agent_auto_continues_after_delivery_verification_failure():
    class FakeResponse:
        def __init__(self, lines):
            self._lines = lines

        async def aiter_lines(self):
            for line in self._lines:
                yield line

        async def aclose(self):
            pass

    async def scenario():
        import src.agent_loop as agent_loop_module

        with tempfile.TemporaryDirectory() as tmp:
            seen_bodies = []
            responses = [
                FakeResponse([
                    'data: {"choices":[{"delta":{"content":"<tf-write id=\\"1\\" path=\\"package.json\\">{\\"scripts\\":{\\"build\\":\\"echo bad\\"}}</tf-write>"},"finish_reason":"stop"}]}',
                ]),
                FakeResponse([
                    'data: {"choices":[{"delta":{"content":"fixed"},"finish_reason":"stop"}]}',
                ]),
            ]
            calls = []

            async def fake_verify(cwd, changed_paths):
                calls.append((cwd, changed_paths))
                if len(calls) == 1:
                    return VerificationResult(
                        attempted=True,
                        success=False,
                        project_dir=tmp,
                        reason="npm run build failed",
                    )
                return VerificationResult(attempted=True, success=True, project_dir=tmp, reason="ok")

            old_verify = agent_loop_module.verify_delivery
            agent_loop_module.verify_delivery = fake_verify
            config = AgentConfig(
                provider=ProviderConfig(api_key="test-key", format="openai"),
                cwd=tmp,
                delivery_verify=True,
                max_delivery_fix_attempts=2,
            )
            agent = AgentLoop(config)
            old_console = renderer.console
            renderer.console = Console(file=io.StringIO(), force_terminal=False, theme=renderer.THEME)

            async def fake_send(_path, body):
                seen_bodies.append(body)
                return responses.pop(0)

            agent._send_stream_request = fake_send
            try:
                await agent.run("write project")
                assert len(seen_bodies) == 2
                assert len(calls) == 1
                assert any(
                    "THINKFLOW DELIVERY CHECK FAILED" in message.get("content", "")
                    for message in seen_bodies[1]["messages"]
                    if message.get("role") == "user"
                )
            finally:
                renderer.console = old_console
                agent_loop_module.verify_delivery = old_verify
                await agent.close()

    asyncio.run(scenario())



def test_agent_auto_continues_after_runnable_script_write_without_execution():
    class FakeResponse:
        def __init__(self, lines):
            self._lines = lines

        async def aiter_lines(self):
            for line in self._lines:
                yield line

        async def aclose(self):
            pass

    async def scenario():
        with tempfile.TemporaryDirectory() as tmp:
            seen_bodies = []
            responses = [
                FakeResponse([
                    'data: {"choices":[{"delta":{"content":"<tf-write id=\\"1\\" path=\\"gen_report.py\\">print(' + "'done'" + ')</tf-write>"},"finish_reason":"stop"}]}',
                ]),
                FakeResponse([
                    'data: {"choices":[{"delta":{"content":"<tf-bash id=\\"2\\" cmd=\\"python gen_report.py\\" />"},"finish_reason":"stop"}]}',
                ]),
                FakeResponse([
                    'data: {"choices":[{"delta":{"content":"verified"},"finish_reason":"stop"}]}',
                ]),
            ]
            config = AgentConfig(
                provider=ProviderConfig(api_key="test-key", format="openai"),
                cwd=tmp,
                max_auto_continues=2,
                auto_verify_runnable_artifacts=True,
            )
            agent = AgentLoop(config)
            old_console = renderer.console
            renderer.console = Console(file=io.StringIO(), force_terminal=False, theme=renderer.THEME)

            async def fake_send(_path, body):
                seen_bodies.append(body)
                return responses.pop(0)

            agent._send_stream_request = fake_send
            try:
                await agent.run("write generator")
                assert len(seen_bodies) == 3
                assert any(
                    "THINKFLOW DELIVERY VERIFY REQUIRED" in message.get("content", "")
                    and "gen_report.py" in message.get("content", "")
                    for message in seen_bodies[1]["messages"]
                    if message.get("role") == "user"
                )
                out_path = os.path.join(tmp, "gen_report.py")
                assert os.path.exists(out_path)
            finally:
                renderer.console = old_console
                await agent.close()

    asyncio.run(scenario())


def test_runnable_script_write_does_not_auto_continue_by_default():
    class FakeResponse:
        def __init__(self, lines):
            self._lines = lines

        async def aiter_lines(self):
            for line in self._lines:
                yield line

        async def aclose(self):
            pass

    async def scenario():
        with tempfile.TemporaryDirectory() as tmp:
            seen_bodies = []
            responses = [
                FakeResponse([
                    'data: {"choices":[{"delta":{"content":"<tf-write id=\\"1\\" path=\\"gen_report.py\\">print(' + "'done'" + ')</tf-write>"},"finish_reason":"stop"}]}',
                ]),
            ]
            config = AgentConfig(
                provider=ProviderConfig(api_key="test-key", format="openai"),
                cwd=tmp,
            )
            agent = AgentLoop(config)
            old_console = renderer.console
            renderer.console = Console(file=io.StringIO(), force_terminal=False, theme=renderer.THEME)

            async def fake_send(_path, body):
                seen_bodies.append(body)
                return responses.pop(0)

            agent._send_stream_request = fake_send
            try:
                await agent.run("write generator")
                assert len(seen_bodies) == 1
                out_path = os.path.join(tmp, "gen_report.py")
                assert os.path.exists(out_path)
            finally:
                renderer.console = old_console
                await agent.close()

    asyncio.run(scenario())

def test_parser_error_continues_with_successful_receipts_injected():
    class FakeResponse:
        def __init__(self, lines):
            self._lines = lines

        async def aiter_lines(self):
            for line in self._lines:
                yield line

        async def aclose(self):
            pass

    async def scenario():
        with tempfile.TemporaryDirectory() as tmp:
            seen_bodies = []
            responses = [
                FakeResponse([
                    'data: {"choices":[{"delta":{"content":"<tf-write id=\\"1\\" path=\\"ok.txt\\">ok</tf-write><tf-write id=\\"2\\" path=\\"bad.txt\\">broken"},"finish_reason":"stop"}]}',
                ]),
                FakeResponse([
                    'data: {"choices":[{"delta":{"content":"continued"},"finish_reason":"stop"}]}',
                ]),
            ]
            config = AgentConfig(
                provider=ProviderConfig(api_key="test-key", format="openai"),
                cwd=tmp,
                delivery_verify=False,
            )
            agent = AgentLoop(config)
            old_console = renderer.console
            renderer.console = Console(file=io.StringIO(), force_terminal=False, theme=renderer.THEME)

            async def fake_send(_path, body):
                seen_bodies.append(body)
                return responses.pop(0)

            agent._send_stream_request = fake_send
            try:
                await agent.run("write then parser error")
                assert os.path.exists(os.path.join(tmp, "ok.txt"))
                assert not os.path.exists(os.path.join(tmp, "bad.txt"))
                assert len(seen_bodies) == 2
                second_messages = seen_bodies[1]["messages"]
                assert any(
                    "THINKFLOW PARSER ERROR" in message.get("content", "")
                    for message in second_messages
                    if message.get("role") == "user"
                )
                assert any(
                    "THINKFLOW COMMAND LEDGER" in message.get("content", "")
                    and "ok.txt" in message.get("content", "")
                    for message in second_messages
                    if message.get("role") == "user"
                )
            finally:
                renderer.console = old_console
                await agent.close()

    asyncio.run(scenario())


def test_agent_auto_continues_incomplete_text_command_block():
    class FakeResponse:
        def __init__(self, lines):
            self._lines = lines

        async def aiter_lines(self):
            for line in self._lines:
                yield line

        async def aclose(self):
            pass

    async def scenario():
        with tempfile.TemporaryDirectory() as tmp:
            responses = [
                FakeResponse([
                    'data: {"choices":[{"delta":{"content":"<tf-write id=\\"1\\" path=\\"out.txt\\">hello"},"finish_reason":"length"}]}',
                ]),
                FakeResponse([
                    'data: {"choices":[{"delta":{"content":" world</tf-write>"},"finish_reason":"stop"}]}',
                ]),
            ]
            config = AgentConfig(
                provider=ProviderConfig(api_key="test-key", format="openai"),
                cwd=tmp,
                max_auto_continues=2,
            )
            agent = AgentLoop(config)
            old_console = renderer.console
            renderer.console = Console(file=io.StringIO(), force_terminal=False, theme=renderer.THEME)

            async def fake_send(_path, _body):
                return responses.pop(0)

            agent._send_stream_request = fake_send
            try:
                await agent.run("write with truncation")
                assert len(agent.usage.turns) == 2
                out_path = os.path.join(tmp, "out.txt")
                assert os.path.exists(out_path)
                with open(out_path, "r", encoding="utf-8") as f:
                    assert f.read() == "hello world"
                assert not agent.text_parser.buffer
            finally:
                renderer.console = old_console
                await agent.close()

    asyncio.run(scenario())

def test_agent_auto_continues_after_max_tokens_finish_reason():
    class FakeResponse:
        def __init__(self, lines):
            self._lines = lines

        async def aiter_lines(self):
            for line in self._lines:
                yield line

        async def aclose(self):
            pass

    async def scenario():
        with tempfile.TemporaryDirectory() as tmp:
            responses = [
                FakeResponse([
                    'data: {"choices":[{"delta":{"content":"part"},"finish_reason":"max_tokens"}]}',
                ]),
                FakeResponse([
                    'data: {"choices":[{"delta":{"content":" done"},"finish_reason":"stop"}]}',
                ]),
            ]
            config = AgentConfig(
                provider=ProviderConfig(api_key="test-key", format="openai"),
                cwd=tmp,
                max_auto_continues=2,
            )
            agent = AgentLoop(config)
            old_console = renderer.console
            renderer.console = Console(file=io.StringIO(), force_terminal=False, theme=renderer.THEME)

            async def fake_send(_path, _body):
                return responses.pop(0)

            agent._send_stream_request = fake_send
            try:
                await agent.run("continue test")
                assert len(agent.usage.turns) == 2
                assert agent.usage.turns[0].abort_reason == "length"
            finally:
                renderer.console = old_console
                await agent.close()

    asyncio.run(scenario())


def test_agent_auto_continues_after_transport_error():
    class BrokenResponse:
        def __init__(self):
            self.closed = False

        async def aiter_lines(self):
            yield 'data: {"choices":[{"delta":{"content":"part"}}]}'
            raise httpx.ReadError("broken stream")

        async def aclose(self):
            self.closed = True

    class GoodResponse:
        async def aiter_lines(self):
            yield 'data: {"choices":[{"delta":{"content":" done"},"finish_reason":"stop"}]}'

        async def aclose(self):
            pass

    async def scenario():
        with tempfile.TemporaryDirectory() as tmp:
            responses = [BrokenResponse(), GoodResponse()]
            config = AgentConfig(
                provider=ProviderConfig(api_key="test-key", format="openai"),
                cwd=tmp,
                max_auto_continues=2,
            )
            agent = AgentLoop(config)
            old_console = renderer.console
            renderer.console = Console(file=io.StringIO(), force_terminal=False, theme=renderer.THEME)

            async def fake_send(_path, _body):
                return responses.pop(0)

            agent._send_stream_request = fake_send
            try:
                await agent.run("continue test")
                assert len(agent.usage.turns) == 2
                assert agent.usage.turns[0].abort_reason == "length"
            finally:
                renderer.console = old_console
                await agent.close()

    asyncio.run(scenario())


def test_slash_inspection_commands_do_not_fail():
    async def scenario():
        config = AgentConfig(
            provider=ProviderConfig(api_key="test-key", format="openai"),
            cwd=".",
        )
        agent = AgentLoop(config)
        old_console = renderer.console
        renderer.console = Console(file=io.StringIO(), force_terminal=False, theme=renderer.THEME)
        try:
            for command in (
                "/ctx", "/model", "/tools", "/interfaces", "/skills",
                "/security", "/sandbox", "/sandbox open", "/sandbox balanced",
                "/pwd", "/savings", "/new", "/resume", "/thinking",
            ):
                assert handle_slash_command(command, agent) is True
            output = io.StringIO()
            renderer.console = Console(file=output, force_terminal=False, theme=renderer.THEME)
            handle_slash_command("/help", agent)
            assert "显示隐藏彩蛋" not in output.getvalue()
        finally:
            renderer.console = old_console
            await agent.close()

    asyncio.run(scenario())


def test_slash_model_and_thinking_switch_current_session():
    async def scenario():
        config = AgentConfig(provider=ProviderConfig(api_key="test-key", format="openai"))
        agent = AgentLoop(config)
        old_console = renderer.console
        renderer.console = Console(file=io.StringIO(), force_terminal=False, theme=renderer.THEME)
        try:
            agent.config.provider.available_models = ["glm-5.2", "kimi-k2.7-code", "deepseek-v4-flash"]
            assert handle_slash_command("/model kimi-k2.7-code", agent) is True
            assert agent.config.provider.model == "kimi-k2.7-code"
            assert handle_slash_command("/thinking medium", agent) is True
            assert agent.config.provider.thinking_budget == 4096
            assert handle_slash_command("/thinking 2048", agent) is True
            assert agent.config.provider.thinking_budget == 2048
        finally:
            renderer.console = old_console
            await agent.close()

    asyncio.run(scenario())


def test_selection_items_include_channel_and_session_metadata():
    async def scenario():
        with tempfile.TemporaryDirectory() as tmp:
            config = AgentConfig(
                provider=ProviderConfig(
                    api_key="test-key",
                    format="openai",
                    profile_name="opencode-go",
                    model="glm-5.2",
                    available_models=["glm-5.2", "kimi-k2.7-code"],
                    model_source="configured",
                    thinking_budget=4096,
                ),
                cwd=tmp,
            )
            agent = AgentLoop(config)
            try:
                model_items = build_model_selection_items(agent)
                assert model_items[0].label == "glm-5.2  - opencode-go"
                assert model_items[0].meta == "current"
                assert model_items[1].label == "kimi-k2.7-code  - opencode-go"

                thinking_items = build_thinking_selection_items(agent.config.provider.thinking_budget)
                assert any(item.value == 4096 and item.meta == "current" for item in thinking_items)

                store = SessionStore(os.path.join(tmp, "session.json"), cwd=tmp)
                store.save({
                    "version": 1,
                    "messages": [{"role": "user", "content": "hello from old session"}],
                    "context": {},
                    "executed_ids": [],
                    "turn_count": 1,
                })
                resume_items = build_resume_selection_items(store, store.discover())
                assert any("hello from old session" in item.label for item in resume_items)
                assert any("msgs=1" in item.meta and "turns=1" in item.meta for item in resume_items)
                assert all(str(store.path) not in item.label for item in resume_items)
                assert all(str(store.path) not in item.detail for item in resume_items)
            finally:
                await agent.close()

    asyncio.run(scenario())


def test_inline_selector_style_html_is_valid():
    from prompt_toolkit.formatted_text import HTML

    HTML(selector_open_style(True) + " > selected </style>")
    HTML(selector_open_style(False) + "   item </style>")


def test_model_selection_spans_provider_profiles_and_switches_endpoint():
    async def scenario():
        config = resolve_auto_model(merge_active_provider({
            "active_provider": "opencode-go",
            "providers": {
                "opencode-go": {
                    "provider": "openai",
                    "base_url": "https://opencode.ai/zen/go",
                    "api_key": "test-key",
                    "model": "auto",
                    "models": ["glm-5.2", "kimi-k2.7-code"],
                },
                "deepseek": {
                    "provider": "openai",
                    "base_url": "https://api.deepseek.com",
                    "api_key": "deepseek-key",
                    "model": "deepseek-v4-flash",
                    "models": ["deepseek-v4-flash", "deepseek-v4-pro"],
                },
            },
        }))
        profiles = build_provider_profiles(config)
        assert [profile.name for profile in profiles] == ["opencode-go", "deepseek"]
        assert profiles[0].model == "glm-5.2"
        assert profiles[1].models == ["deepseek-v4-flash", "deepseek-v4-pro"]

        agent = create_agent(config, "")
        old_console = renderer.console
        renderer.console = Console(file=io.StringIO(), force_terminal=False, theme=renderer.THEME)
        try:
            items = build_model_selection_items(agent)
            labels = [item.label for item in items]
            assert "glm-5.2  - opencode-go" in labels
            assert "deepseek-v4-pro  - deepseek" in labels

            switch_model(agent, "deepseek/deepseek-v4-pro")
            provider = agent.config.provider
            assert provider.profile_name == "deepseek"
            assert provider.model == "deepseek-v4-pro"
            assert provider.base_url == "https://api.deepseek.com"
            assert provider.api_key == "deepseek-key"
            assert provider.available_models == ["deepseek-v4-flash", "deepseek-v4-pro"]

            selection = next(
                item.value
                for item in build_model_selection_items(agent)
                if getattr(item.value, "provider_name", "") == "opencode-go"
                and getattr(item.value, "model", "") == "kimi-k2.7-code"
            )
            switch_model(agent, selection)
            assert agent.config.provider.profile_name == "opencode-go"
            assert agent.config.provider.model == "kimi-k2.7-code"
            assert agent.config.provider.base_url == "https://opencode.ai/zen/go"
        finally:
            renderer.console = old_console
            await agent.close()

    asyncio.run(scenario())


def test_provider_catalog_does_not_overlay_active_profile_onto_other_profiles():
    config = merge_active_provider({
        "active_provider": "opencode-go",
        "providers": {
            "opencode-go": {
                "provider": "openai",
                "base_url": "https://opencode.ai/zen/go",
                "api_key": "test-key",
                "model": "glm-5.2",
                "models": ["glm-5.2"],
            },
            "deepseek": {
                "provider": "openai",
                "base_url": "https://api.deepseek.com",
                "api_key": "deepseek-key",
                "model": "deepseek-v4-flash",
                "models": ["deepseek-v4-flash", "deepseek-v4-pro"],
            },
        },
    })

    active = provider_catalog(config, "opencode-go")
    other = provider_catalog(config, "deepseek")

    assert active.base_url == "https://opencode.ai/zen/go"
    assert active.models == ["glm-5.2"]
    assert other.base_url == "https://api.deepseek.com"
    assert other.models == ["deepseek-v4-flash", "deepseek-v4-pro"]


def test_slash_new_and_resume_roundtrip_session():
    async def scenario():
        with tempfile.TemporaryDirectory() as tmp:
            session_path = os.path.join(tmp, "session.json")
            store = SessionStore(session_path, cwd=tmp)
            config = AgentConfig(
                provider=ProviderConfig(api_key="test-key", format="openai"),
                cwd=tmp,
            )
            agent = AgentLoop(config)
            old_console = renderer.console
            output = io.StringIO()
            renderer.console = Console(file=output, force_terminal=False, theme=renderer.THEME)
            try:
                agent.messages.append({"role": "user", "content": "old"})
                store.save(agent.to_snapshot())
                agent.messages.append({"role": "assistant", "content": "dirty"})
                assert handle_slash_command("/resume", agent, store) is True
                assert agent.messages == [{"role": "user", "content": "old"}, {"role": "assistant", "content": "dirty"}]
                assert "Sessions" in output.getvalue()
                assert "old" in output.getvalue()
                assert str(store.path) not in output.getvalue()
                assert handle_slash_command("/resume 1", agent, store) is True
                assert agent.messages == [{"role": "user", "content": "old"}]
                assert handle_slash_command("/new", agent, store) is True
                assert agent.messages == []
                assert store.exists()
                assert store.load()["messages"] == []

                sessions_dir = os.path.join(tmp, ".thinkflow", "sessions")
                os.makedirs(sessions_dir, exist_ok=True)
                first = SessionStore(os.path.join(sessions_dir, "a.json"), cwd=tmp)
                second = SessionStore(os.path.join(sessions_dir, "b.json"), cwd=tmp)
                first.save({"version": 1, "messages": [{"role": "user", "content": "first"}], "context": {}, "executed_ids": [], "turn_count": 1})
                second.save({"version": 1, "messages": [{"role": "user", "content": "second"}], "context": {}, "executed_ids": [], "turn_count": 2})
                assert handle_slash_command("/resume", agent, store) is True
                assert agent.messages == []
                assert "first" in output.getvalue()
                assert "second" in output.getvalue()
                assert handle_slash_command(f"/resume {second.path}", agent, store) is True
                assert agent.messages == [{"role": "user", "content": "second"}]
                assert "Restored Transcript" in output.getvalue()
            finally:
                renderer.console = old_console
                await agent.close()

    asyncio.run(scenario())


def test_session_store_keeps_history_snapshots_for_resume_choices():
    with tempfile.TemporaryDirectory() as tmp:
        store = SessionStore(os.path.join(tmp, "session.json"), cwd=tmp)
        first = {
            "version": 1,
            "messages": [{"role": "user", "content": "first task"}],
            "context": {},
            "executed_ids": [],
            "turn_count": 1,
        }
        second = {
            "version": 1,
            "messages": [{"role": "user", "content": "second task"}],
            "context": {},
            "executed_ids": [],
            "turn_count": 2,
        }
        store.save(first)
        store.save(second)
        sessions = store.discover()
        starts = [store.inspect(path).get("start") for path in sessions]
        assert "first task" in starts
        assert "second task" in starts
        assert len([path for path in sessions if "history" in path.parts]) >= 1


def test_slash_completer_lists_matching_commands_and_sandbox_options():
    from prompt_toolkit.document import Document

    completer = build_slash_completer()

    command_texts = [
        item.text
        for item in completer.get_completions(Document("/s"), None)
    ]
    assert "/sandbox" in command_texts
    assert "/save" in command_texts
    assert all(item.startswith("/s") for item in command_texts)
    assert any(item.text == "/btw" for item in completer.get_completions(Document("/b"), None))
    assert any(item.text == "/cancel" for item in completer.get_completions(Document("/c"), None))

    sandbox_texts = [
        item.text
        for item in completer.get_completions(Document("/sandbox o"), None)
    ]
    assert sandbox_texts == ["open"]

    assert list(completer.get_completions(Document("plain text"), None)) == []

    agent = create_agent({
        "active_provider": "opencode-go",
        "provider": "openai",
        "base_url": "https://opencode.ai/zen/go",
        "api_key": "test-key",
        "model": "glm-5.2",
        "providers": {
            "opencode-go": {
                "provider": "openai",
                "base_url": "https://opencode.ai/zen/go",
                "api_key": "test-key",
                "model": "glm-5.2",
                "models": ["glm-5.2"],
            },
            "deepseek": {
                "provider": "openai",
                "base_url": "https://api.deepseek.com",
                "api_key": "deepseek-key",
                "model": "deepseek-v4-flash",
                "models": ["deepseek-v4-flash"],
            },
        },
    }, "")
    try:
        model_completer = build_slash_completer(agent)
        model_texts = [
            item.text
            for item in model_completer.get_completions(Document("/model deepseek/"), None)
        ]
        assert model_texts == ["deepseek/deepseek-v4-flash"]
    finally:
        asyncio.run(agent.close())


def run_all():
    tests = [
        test_executor_resolves_relative_paths_to_cwd,
        test_executor_append_touch_and_copy,
        test_executor_rejects_empty_write_and_append,
        test_executor_read_truncates_large_files,
        test_executor_blocks_outside_cwd_by_default,
        test_executor_blocks_sensitive_reads_by_default,
        test_executor_blocks_config_json_and_redacts_secret_fields,
        test_executor_can_explicitly_allow_sensitive_reads,
        test_bash_safe_policy_blocks_dangerous_commands,
        test_bash_env_does_not_leak_api_key_by_default,
        test_approval_request_all_blocks_high_risk_tools,
        test_approval_approve_all_allows_high_risk_tools_through_policy,
        test_permission_modes_map_and_read_only_blocks_side_effects,
        test_security_open_profile_is_explicitly_permissive,
        test_context_snapshot_roundtrip_and_mark_injected,
        test_command_ledger_injection_includes_hash_flow_risk_and_summary,
        test_agent_snapshot_roundtrip,
        test_agent_keeps_system_prompt_stable_and_appends_runtime_status,
        test_traditional_read_tool_uses_executor_cwd,
        test_executor_list_glob_and_grep_tools,
        test_traditional_tools_cover_main_agent_actions,
        test_tool_registry_exposes_open_interfaces,
        test_skill_manager_reads_codex_and_claude_style_skills,
        test_interfaces_block_private_fetch_and_explain_disabled_image_generation,
        test_interfaces_block_dns_private_hosts,
        test_configured_custom_tool_executes_as_native_tool,
        test_openai_stream_split_tool_arguments,
        test_openai_stream_bad_json_reports_error_event,
        test_agent_sets_last_error_on_stream_event_error,
        test_anthropic_message_delta_preserves_stop_reason,
        test_env_defaults_do_not_override_config,
        test_global_session_and_context_injection_use_thinkflow_home,
        test_system_prompt_defaults_to_builtin_and_context_is_appended,
        test_compaction_keeps_recent_messages_and_summarizes_old,
        test_compaction_keeps_tool_result_with_tool_call,
        test_agent_manual_compaction_updates_snapshot_count,
        test_context_injection_clips_large_write_content,
        test_write_config_template_creates_starter_config_without_key,
        test_named_provider_profile_merges_without_overwriting_legacy_shape,
        test_named_provider_profile_keeps_env_or_cli_overrides_when_profile_field_is_empty,
        test_auto_discovery_skips_unrelated_or_malformed_config_json,
        test_auto_model_prefers_glm_then_kimi_then_deepseek,
        test_provider_request_defaults_are_protocol_neutral,
        test_create_agent_accepts_native_tool_string_or_list_config,
        test_safe_text_filter_streams_text_and_removes_command_blocks,
        test_safe_text_filter_removes_self_closing_commands,
        test_safe_text_filter_preserves_non_tool_angle_text,
        test_filters_preserve_fenced_tool_examples_and_strip_executable_text,
        test_strict_parser_requires_tf_prefix,
        test_renderer_flushes_streamed_markdown,
        test_renderer_tool_summary_lists_command_paths,
        test_renderer_distinguishes_native_tool_kinds_and_running_state,
        test_agent_notices_streaming_tool_hint_before_command_closes,
        test_agent_executes_text_channel_command_without_history_leak,
        test_agent_executes_text_commands_fifo_even_when_stream_is_fast,
        test_agent_records_skipped_commands_after_queue_failure,
        test_agent_does_not_execute_fenced_text_channel_command,
        test_agent_auto_continues_after_length_finish_reason,
        test_agent_auto_continues_after_blocking_text_command_result,
        test_agent_auto_continues_after_text_read_command_result,
        test_agent_auto_continues_after_delivery_verification_failure,
        test_agent_auto_continues_after_runnable_script_write_without_execution,
        test_runnable_script_write_does_not_auto_continue_by_default,
        test_parser_error_continues_with_successful_receipts_injected,
        test_agent_auto_continues_incomplete_text_command_block,
        test_agent_auto_continues_after_max_tokens_finish_reason,
        test_agent_auto_continues_after_transport_error,
        test_slash_inspection_commands_do_not_fail,
        test_slash_model_and_thinking_switch_current_session,
        test_selection_items_include_channel_and_session_metadata,
        test_inline_selector_style_html_is_valid,
        test_model_selection_spans_provider_profiles_and_switches_endpoint,
        test_provider_catalog_does_not_overlay_active_profile_onto_other_profiles,
        test_slash_new_and_resume_roundtrip_session,
        test_session_store_keeps_history_snapshots_for_resume_choices,
        test_slash_completer_lists_matching_commands_and_sandbox_options,
    ]
    for test in tests:
        test()
        print(f"✓ {test.__name__}")


if __name__ == "__main__":
    run_all()
