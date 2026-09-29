"""Private NDJSON desktop transport for the shared ThinkFlow runtime.

No network listener and no renderer-side execution. Commands are journaled before
execution; unresolved intents require explicit acknowledgement after a restart.
"""
from __future__ import annotations

import asyncio
import contextlib
import copy
import hashlib
import json
import math
import os
from pathlib import Path
import re
import sys
import time
from urllib.parse import urlsplit
import uuid

from .cli import create_agent, resolve_system_prompt
from .provider import normalize_max_tokens
from .session import SessionStore


DEFAULT_CONFIG = {
    "provider": "openai", "base_url": "", "api_path": "", "model": "", "api_key": "",
    "max_tokens": None, "max_run_turns": 40, "max_run_seconds": 1800,
    "security_profile": "balanced",
    "thinking_mode": "disabled", "reasoning_effort": "high", "stream_options_include_usage": True,
}
SESSION_ID = re.compile(r"^[a-f0-9]{32}$")
COMMAND_ID = re.compile(r"^[A-Za-z0-9_-]{1,128}$")
INPUT_PENDING = {"received", "queued"}
INPUT_TERMINAL = {"completed", "failed", "cancelled", "interrupted"}


class DesktopService:
    def __init__(self, emit=None):
        self.emit = emit or (lambda event: None)
        self.data_dir: Path | None = None
        self.cwd = ""
        self.config = dict(DEFAULT_CONFIG)
        self.agent = None
        self.store = None
        self.session_id = ""
        self.title = "新会话"
        self.title_custom = False
        self.pinned = False
        self.archived = False
        self.navigation_warning = ""
        self.transcript: list[dict] = []
        self.status = "idle"
        self.last_error = ""
        self.task: asyncio.Task | None = None
        self.pending_approval = None
        self.approval_future = None
        self.recovery: list[dict] = []
        self.active_intents: dict[str, dict] = {}
        self._reply_index = None
        self._last_event_state = 0.0
        self._shutdown = False
        self._secrets: set[str] = set()
        self.stream_id = uuid.uuid4().hex
        self.seq = 0
        self.input_commands: list[dict] = []
        self._durable_inputs: list[dict] = []
        self.queue_paused = False
        self.queue_pause_reason = ""
        self._input_lock = asyncio.Lock()
        self._active_command = None
        self._closing = False
        self._workspace_mutating = False

    def _clean(self, value):
        # Exact configured secrets never enter events, persisted snapshots or exports.
        if isinstance(value, str):
            for key in sorted(self._secrets, key=len, reverse=True):
                value = value.replace(key, "[REDACTED]")
            return value
        if isinstance(value, dict):
            return {k: self._clean(v) for k, v in value.items() if k != "api_key"}
        if isinstance(value, list):
            return [self._clean(v) for v in value]
        return value

    def _emit(self, event):
        self.seq += 1
        event = {**event, "stream_id": self.stream_id, "seq": self.seq, "session_id": self.session_id}
        if event.get("type") == "state" and event.get("state"):
            event["state"] = {**event["state"], "stream_id": self.stream_id, "seq": self.seq, "session_id": self.session_id}
        self.emit(self._clean(event))

    def _publish_state(self):
        self._emit({"type": "state", "state": self.state()})
        return self.state()

    def _require_idle(self):
        if self._workspace_mutating:
            raise ValueError("文件恢复正在进行，请稍后再操作。")
        if self.task and not self.task.done():
            raise ValueError("请先停止当前运行，再切换工作区、会话或配置。")

    @property
    def session_root(self):
        canonical = os.path.normcase(self.cwd)
        digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:24]
        return self.data_dir / "workspaces" / digest / "sessions"

    def _session_path(self, session_id):
        if not SESSION_ID.fullmatch(str(session_id)):
            raise ValueError("无效会话编号。")
        return self.session_root / f"{session_id}.json"

    @property
    def journal_path(self):
        return self._session_path(self.session_id).with_suffix(".jsonl")

    def _journal(self, event):
        self.journal_path.parent.mkdir(parents=True, exist_ok=True)
        payload = self._clean({"time": time.time(), **event})
        with self.journal_path.open("a+b") as stream:
            if stream.tell():
                stream.seek(-1, os.SEEK_END)
                if stream.read(1) != b"\n":
                    stream.write(b"\n")
            stream.write((json.dumps(payload, ensure_ascii=False) + "\n").encode("utf-8"))
            stream.flush()
            os.fsync(stream.fileno())

    def _save(self, *, history=False):
        if not self.agent or not self.store:
            return
        self._assign_message_ids()
        snapshot = self.agent.to_snapshot()
        snapshot["desktop"] = {
            "id": self.session_id, "cwd": self.cwd, "title": self.title,
            "title_custom": self.title_custom,
            "pinned": self.pinned, "archived": self.archived,
            "updated_at": time.time(), "status": self.status,
            "transcript": self.transcript, "recovery": self.recovery,
            "last_error": self.last_error,
            "input_commands": self.input_commands, "queue_paused": self.queue_paused,
            "queue_pause_reason": self.queue_pause_reason,
            "stream_id": self.stream_id, "seq": self.seq,
        }
        self.store.save(self._clean(snapshot), history=history)
        self._durable_inputs = copy.deepcopy(self.input_commands)

    def _config_for_agent(self):
        config = dict(self.config)
        config["security"] = {"profile": config.pop("security_profile")}
        if config["security"]["profile"] == "balanced":
            config["security"]["approval_mode"] = "request_all"
        config["verbose"] = False
        return config

    async def _replace_agent(self, snapshot=None, *, close_previous=True):
        config = self._config_for_agent()
        system = resolve_system_prompt(config, cwd=self.cwd)
        candidate = create_agent(config, system, self.cwd,
                                 event_sink=self._on_agent_event, approval_handler=self._approval)
        try:
            if snapshot:
                candidate.load_snapshot(snapshot)
                # A syntactically valid snapshot can still contain unusable runtime
                # state. Check presentation inputs before closing the current agent.
                candidate.message_stats()
                candidate.usage.to_dict()["totals"]
        except Exception:
            await candidate.close()
            raise
        if self.agent and close_previous:
            await self.agent.close()
        self.agent = candidate

    def _on_agent_event(self, event):
        kind = event.get("type")
        if kind in ("run_finished", "approval_required"):
            # The service emits these only after its own lifecycle state is ready.
            return
        if kind == "text_delta":
            if self._reply_index is None:
                self._reply_index = len(self.transcript)
                self.transcript.append({"id": uuid.uuid4().hex, "role": "assistant", "content": ""})
            self.transcript[self._reply_index]["content"] += event.get("text", "")
            event = {**event, "message_id": self.transcript[self._reply_index]["id"]}
        if kind in ("tool_started", "tool_completed"):
            intent_key = f"{event.get('channel', 'stream')}:{event.get('id', '')}"
            if kind == "tool_started":
                self._journal(event)
                self.active_intents[intent_key] = event
                self._save()
            else:
                # Snapshot must precede the completed journal marker: after a crash
                # an extra unresolved intent is safer than losing a committed receipt.
                self._save()
                self._journal(event)
                self.active_intents.pop(intent_key, None)
        elif kind == "turn_finished":
            self._save()
        elif kind == "plan_updated":
            self._save()
        self._emit(event)
        if kind == "plan_updated":
            self._emit({"type": "state", "state": self.state()})

    async def _approval(self, tool, tool_input):
        request_id = uuid.uuid4().hex
        self.pending_approval = {"request_id": request_id, "tool": tool, "input": tool_input}
        self.status = "approval"
        self.approval_future = asyncio.get_running_loop().create_future()
        self._emit({"type": "approval_required", **self.pending_approval})
        self._emit({"type": "state", "state": self.state()})
        try:
            return await self.approval_future
        finally:
            self.pending_approval = None
            self.approval_future = None
            self.status = "running"

    def sessions(self):
        if not self.cwd or not self.data_dir:
            return []
        result = []
        for path in self.session_root.glob("*.json"):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                meta = data.get("desktop", {})
                if meta.get("cwd") != self.cwd:
                    continue
                title = meta.get("title", "新会话")
                if not isinstance(title, str):
                    continue
                updated_at = meta.get("updated_at", path.stat().st_mtime)
                if (not SESSION_ID.fullmatch(path.stem) or isinstance(updated_at, bool)
                        or not isinstance(updated_at, (int, float)) or not math.isfinite(updated_at)):
                    continue
                result.append({"id": path.stem, "title": title,
                               "pinned": meta.get("pinned") is True,
                               "archived": meta.get("archived") is True,
                               "updated_at": updated_at})
            except (OSError, ValueError, TypeError, AttributeError):
                continue
        return sorted(result, key=lambda item: item["updated_at"], reverse=True)

    def state(self):
        self._assign_message_ids()
        usage = {}
        ledger = []
        context = {"messages": 0, "chars": 0, "compactions": 0}
        if self.agent:
            totals = self.agent.usage.to_dict()["totals"]
            usage = {**totals, "commands": len(self.agent.context.records) + self.agent.context.archived_records,
                     "reported": bool(self.agent.usage.turns) and all(t.usage_reported for t in self.agent.usage.turns),
                     "reported_turns": sum(t.usage_reported for t in self.agent.usage.turns)}
            fields = ("id", "tool", "path", "dest", "status", "flow", "risk", "error",
                      "bytes_written", "output_summary", "content_hash", "exit_code")
            ledger = [{name: (getattr(record, name, None)[:4000] if isinstance(getattr(record, name, None), str)
                              else getattr(record, name, None)) for name in fields}
                      for record in self.agent.context.records[-500:]]
            context = self.agent.message_stats()
            context["archived_records"] = self.agent.context.archived_records
        # A long transcript must never exceed the private transport's frame cap.
        # This is a presentation window only; persisted history remains complete.
        visible_messages = []
        for message in self.transcript[-120:]:
            content = message.get("content", "")
            if len(content) > 32000:
                content = "[此条内容较长，界面仅显示末尾32000字符；完整内容保留在本机，导出上限1600万字符。]\n\n" + content[-32000:]
            visible_messages.append({"id": message["id"], "role": message["role"], "content": content,
                                     **({"attachments": message["attachments"]} if message.get("attachments") else {})})
        return self._clean({
            "cwd": self.cwd, "status": self.status, "config": {
                **{k: v for k, v in self.config.items() if k != "api_key"},
                "has_api_key": bool(self.config.get("api_key")),
            },
            "session_id": self.session_id, "sessions": self.sessions(),
            "stream_id": self.stream_id, "seq": self.seq,
            "input_queue": [self._input_receipt(item) for item in self.input_commands
                            if item["status"] in INPUT_PENDING | {"running"}],
            "queue_paused": self.queue_paused, "queue_pause_reason": self.queue_pause_reason,
            "navigation_warning": self.navigation_warning,
            "messages": visible_messages, "ledger": ledger, "usage": usage,
            "transcript_window": {"total": len(self.transcript), "shown": len(visible_messages)},
            "context": context, "pending_approval": self.pending_approval,
            "task_plan": copy.deepcopy(getattr(self.agent, "task_plan", {"explanation": "", "steps": []})),
            "last_error": self.last_error, "recovery_required": bool(self.recovery),
            "recovery": self.recovery,
        })

    @staticmethod
    def _validate_config(config):
        if config["provider"] not in ("openai", "anthropic"):
            raise ValueError("模型协议须为 openai 或 anthropic。")
        if config["security_profile"] not in ("balanced", "read-only", "open"):
            raise ValueError("无效权限模式。")
        if config["thinking_mode"] not in ("disabled", "enabled") or config["reasoning_effort"] not in ("low", "high", "max"):
            raise ValueError("无效思考模式或推理强度。")
        if type(config["stream_options_include_usage"]) is not bool:
            raise ValueError("用量流选项须为布尔值。")
        for field in ("base_url", "api_path", "model", "api_key"):
            if not isinstance(config[field], str):
                raise ValueError(f"{field} 须为文本。")
        if config["base_url"]:
            url = urlsplit(config["base_url"])
            if url.scheme not in ("http", "https") or not url.hostname or url.username or url.password:
                raise ValueError("端点须为不含账号密码的 HTTP(S) URL。")
            if url.query or url.fragment:
                raise ValueError("端点不可包含查询参数或片段。")
        if config["api_path"] and (not config["api_path"].startswith("/") or config["api_path"].startswith("//")):
            raise ValueError("API 路径须以单个 / 开头。")
        budget = normalize_max_tokens(config["max_tokens"])
        if config["provider"] == "anthropic" and budget is None:
            raise ValueError("Anthropic 必须设置正整数 max_tokens，不支持省略输出预算。")
        config["max_tokens"] = budget
        for name, maximum in (("max_run_turns", 1000), ("max_run_seconds", 86400)):
            value = config[name]
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not 1 <= value <= maximum:
                raise ValueError(f"{name} 须在 1–{maximum} 之间。")
            if name != "max_run_seconds" and int(value) != value:
                raise ValueError(f"{name} 须为整数。")

    async def configure(self, params):
        self._require_idle()
        candidate = dict(self.config)
        for key, value in params.items():
            if key in DEFAULT_CONFIG:
                if key == "api_key" and value == "" and not params.get("clear_api_key"):
                    continue
                candidate[key] = value
        if params.get("clear_api_key"):
            candidate["api_key"] = ""
        self._validate_config(candidate)
        if candidate.get("api_key"):
            self._secrets.add(candidate["api_key"])
        previous = self.config
        self.config = candidate
        try:
            if self.agent:
                await self._replace_agent(self.agent.to_snapshot())
                self._attach_change_store()
        except Exception:
            self.config = previous
            raise
        return self._publish_state()

    async def _new_session(self, *, snapshot=None, transcript=None, close_previous=True):
        self._require_idle()
        if not self.cwd:
            raise ValueError("请先选择工作区。")
        await self._replace_agent(snapshot, close_previous=close_previous)
        self.session_id = uuid.uuid4().hex
        self.store = SessionStore(str(self._session_path(self.session_id)), cwd=self.cwd)
        self.transcript = copy.deepcopy(transcript or [])
        self._assign_message_ids()
        self.input_commands = []
        self._durable_inputs = []
        self.queue_paused = False
        self.queue_pause_reason = ""
        self._active_command = None
        self.title = "新会话" if not transcript else self.title + " · 分支"
        self.title_custom = False
        self.pinned = False
        self.archived = False
        self.status = "idle"
        self.last_error = ""
        self.recovery = []
        self.active_intents = {}
        self._reply_index = None
        self._attach_change_store()
        self._save()
        return self._publish_state()

    def _unresolved_intents(self, session_id=None):
        pending = {}
        journal_path = self._session_path(session_id).with_suffix(".jsonl") if session_id else self.journal_path
        if not journal_path.exists():
            return []
        with journal_path.open(encoding="utf-8") as stream:
            for line in stream:
                try:
                    event = json.loads(line)
                    if not isinstance(event, dict):
                        raise ValueError("执行日志条目不是对象")
                except ValueError:
                    pending["journal"] = {"tool": "unknown", "path": "执行日志尾部损坏，须核对工作区"}
                    continue
                key = f"{event.get('channel', 'stream')}:{event.get('id', '')}"
                if event.get("type") == "tool_started":
                    pending[key] = event
                elif event.get("type") == "tool_completed":
                    if event.get("status") in ("cancelled", "unknown"):
                        pending[key] = {**pending.get(key, {}), **event}
                    else:
                        pending.pop(key, None)
                elif event.get("type") == "recovery_acknowledged":
                    pending.clear()
        return list(pending.values())

    async def resume(self, session_id, *, close_previous=True):
        self._require_idle()
        path = self._session_path(session_id)
        store = SessionStore(str(path), cwd=self.cwd)
        data = store.load()
        if data.get("desktop", {}).get("cwd") != self.cwd:
            raise ValueError("该会话不属于当前工作区。")
        meta = data["desktop"]
        transcript = meta.get("transcript", [])
        if (not isinstance(meta.get("title", "恢复的会话"), str)
                or not isinstance(transcript, list)
                or any(not isinstance(item, dict) or item.get("role") not in ("user", "assistant")
                       or not isinstance(item.get("content"), str) for item in transcript)):
            raise ValueError("会话显示记录损坏；原文件仍保留在本机。")
        recovery = self._unresolved_intents(session_id)
        inputs = self._load_inputs(meta)
        await self._replace_agent(data, close_previous=close_previous)
        self.store = store
        self.session_id = session_id
        self.title = meta.get("title", "恢复的会话")
        self.title_custom = meta.get("title_custom") is True
        self.pinned = meta.get("pinned") is True
        self.archived = meta.get("archived") is True
        self.transcript = copy.deepcopy(meta.get("transcript", []))
        self._assign_message_ids()
        self.input_commands = inputs
        self._durable_inputs = copy.deepcopy(self.input_commands)
        self.queue_paused = meta.get("queue_paused") is True
        self.queue_pause_reason = str(meta.get("queue_pause_reason", ""))
        self._active_command = None
        changed = False
        for item in self.input_commands:
            if item["status"] == "running":
                self._transition_input(item, "interrupted", error="执行结果不确定；不会自动重放，请核对历史与文件。")
                changed = True
        if changed or any(item["status"] in INPUT_PENDING for item in self.input_commands):
            self.queue_paused = True
            self.queue_pause_reason = "会话已恢复，请核对上次执行并手动继续队列。"
        self.recovery = recovery
        self.active_intents = {}
        self.status = "error" if self.recovery else "idle"
        self.last_error = "上次执行意外中断。请核对实际文件后继续，不会自动重放操作。" if self.recovery else ""
        self._reply_index = None
        self._attach_change_store()
        if changed:
            self._save()
        return self._publish_state()

    async def open_workspace(self, params):
        self._require_idle()
        path = Path(params.get("cwd", "")).expanduser()
        if not path.is_absolute() or not path.is_dir():
            raise ValueError("请选择存在的绝对目录。")
        self._save()
        previous_cwd, previous_warning = self.cwd, self.navigation_warning
        runtime_fields = ("agent", "store", "session_id", "title", "title_custom", "pinned", "archived",
                          "transcript", "status", "last_error", "recovery", "active_intents", "_reply_index",
                          "input_commands", "_durable_inputs", "queue_paused", "queue_pause_reason", "_active_command")
        previous_runtime = {name: getattr(self, name) for name in runtime_fields}

        async def restore_runtime():
            if self.agent is not previous_runtime["agent"] and self.agent:
                with contextlib.suppress(Exception):
                    await self.agent.close()
            for name, value in previous_runtime.items():
                setattr(self, name, value)

        async def finish(state):
            previous_agent = previous_runtime["agent"]
            if previous_agent and previous_agent is not self.agent:
                with contextlib.suppress(Exception):
                    await previous_agent.close()
            return state

        self.cwd = str(path.resolve())
        self.navigation_warning = ""
        sessions = [item for item in self.sessions() if not item["archived"]]
        preferred = params.get("session_id")
        candidates = [item["id"] for item in sessions]
        if preferred in candidates:
            candidates.remove(preferred)
            candidates.insert(0, preferred)
        elif preferred:
            self.navigation_warning = ("上次会话已归档、丢失或无法读取，已恢复其他可用会话。" if candidates
                                       else "上次会话已归档、丢失或无法读取，已新建会话。")
        try:
            for identifier in candidates:
                try:
                    state = await self.resume(identifier, close_previous=False)
                    return await finish(state)
                except (OSError, ValueError, TypeError, AttributeError, KeyError):
                    await restore_runtime()
                    self.navigation_warning = "部分历史会话无法读取，已跳过；原文件仍保留在本机。"
            state = await self._new_session(close_previous=False)
            return await finish(state)
        except Exception:
            await restore_runtime()
            self.cwd, self.navigation_warning = previous_cwd, previous_warning
            raise

    def update_session(self, params):
        self._require_idle()
        if set(params) - {"session_id", "title", "pinned", "archived"}:
            raise ValueError("不支持的会话参数。")
        identifier = params.get("session_id", "")
        path = self._session_path(identifier)
        changes = {}
        if "title" in params:
            title = params["title"]
            if not isinstance(title, str) or not title.strip() or len(title.strip()) > 120:
                raise ValueError("会话标题须为 1–120 字符。")
            changes["title"] = title.strip()
            changes["title_custom"] = True
        for name in ("pinned", "archived"):
            if name in params:
                if type(params[name]) is not bool:
                    raise ValueError("置顶和归档须为布尔值。")
                changes[name] = params[name]
        if not changes:
            raise ValueError("请提供要修改的会话信息。")
        store = SessionStore(str(path), cwd=self.cwd)
        snapshot = store.load()
        meta = snapshot.get("desktop", {})
        if meta.get("cwd") != self.cwd:
            raise ValueError("该会话不属于当前工作区。")
        snapshot["desktop"] = {**meta, **changes}
        store.save(self._clean(snapshot), history=False)
        if identifier == self.session_id:
            for name, value in changes.items():
                setattr(self, name, value)
        return self._publish_state()

    def _assign_message_ids(self):
        for index, message in enumerate(self.transcript):
            if not isinstance(message.get("id"), str) or not message["id"]:
                message["id"] = uuid.uuid5(uuid.NAMESPACE_URL, f"thinkflow:{self.session_id}:{index}:{message.get('role')}").hex

    def _attach_change_store(self):
        executor = getattr(self.agent, "executor", None)
        if executor and hasattr(executor, "set_change_store") and self.session_id:
            from .changes import ChangeStore
            executor.set_change_store(ChangeStore(root=self.session_root.parent / "changes" / self.session_id, cwd=self.cwd))

    @staticmethod
    def _load_inputs(meta):
        items = meta.get("input_commands", [])
        if not isinstance(items, list):
            raise ValueError("输入回执记录损坏。")
        seen = set()
        for item in items:
            if (not isinstance(item, dict) or not COMMAND_ID.fullmatch(str(item.get("id", "")))
                    or item.get("id") in seen or item.get("status") not in INPUT_PENDING | INPUT_TERMINAL | {"running"}
                    or not isinstance(item.get("prompt"), str) or len(item["prompt"]) > 200000
                    or not re.fullmatch(r"[a-f0-9]{64}", str(item.get("content_hash", "")))):
                raise ValueError("输入回执记录损坏。")
            attachments = item.get("attachments", [])
            if (not isinstance(item.get("attachment_context", ""), str) or not isinstance(attachments, list)
                    or len(attachments) > 8 or any(not isinstance(part, dict) or set(part) != {"path", "revision"}
                        or not isinstance(part["path"], str) or not isinstance(part["revision"], str) for part in attachments)
                    or any(isinstance(item.get(field), bool) or not isinstance(item.get(field), (int, float))
                           or not math.isfinite(item[field]) for field in ("created_at", "updated_at"))):
                raise ValueError("输入回执记录损坏。")
            seen.add(item["id"])
        return copy.deepcopy(items)

    def _input_receipt(self, item):
        fields = ("id", "prompt", "status", "created_at", "updated_at", "content_hash", "attachments", "error")
        return self._clean({field: copy.deepcopy(item[field]) for field in fields if field in item})

    @staticmethod
    def _transition_input(item, status, **fields):
        now = time.time()
        item.update(status=status, updated_at=now, **fields)
        item.setdefault("lifecycle", []).append({"status": status, "time": now})

    def _find_input(self, command_id, items=None):
        if not isinstance(command_id, str) or not COMMAND_ID.fullmatch(command_id):
            raise ValueError("输入命令编号须为 1–128 个字母、数字、_ 或 -。")
        return next((item for item in (self.input_commands if items is None else items) if item["id"] == command_id), None)

    def _require_input_target(self, session_id, *, archived_allowed=False):
        if self._workspace_mutating:
            raise ValueError("文件恢复正在进行，请稍后再发送。")
        if not self.agent or not self.cwd:
            raise ValueError("请先选择工作区。")
        if session_id != self.session_id:
            raise ValueError("输入目标已变化，请恢复原会话再发送。")
        if self.archived and not archived_allowed:
            raise ValueError("该会话已归档，请先取消归档或创建新会话。")

    def _require_run_config(self):
        if self.recovery:
            raise ValueError("请先核对并确认上次中断的执行。")
        if not self.config["base_url"] or not self.config["model"]:
            raise ValueError("请先在设置中填写模型端点和模型名称。")

    @staticmethod
    def _validate_prompt(prompt):
        if not isinstance(prompt, str) or not prompt.strip() or len(prompt) > 200000:
            raise ValueError("输入须为 1–200000 字符的文本。")

    def _persist_inputs(self, previous):
        try:
            self._save()
        except Exception:
            self.input_commands, self.queue_paused, self.queue_pause_reason = previous
            raise

    async def submit_input(self, params):
        async with self._input_lock:
            if set(params) - {"command_id", "prompt", "session_id", "attachments"}:
                raise ValueError("不支持的输入参数。")
            self._require_input_target(params.get("session_id"), archived_allowed=True)
            command_id, prompt = params.get("command_id"), params.get("prompt")
            existing = self._find_input(command_id)
            self._validate_prompt(prompt)
            attachments = params.get("attachments", [])
            if (not isinstance(attachments, list) or len(attachments) > 8
                    or any(not isinstance(item, dict) or set(item) != {"path", "revision"}
                           or not isinstance(item["path"], str) or not isinstance(item["revision"], str) for item in attachments)):
                raise ValueError("附件须为最多 8 个 path/revision 对象。")
            payload = json.dumps({"prompt": prompt, "attachments": attachments}, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            fingerprint = hashlib.sha256(payload.encode("utf8")).hexdigest()
            if existing:
                if existing["content_hash"] != fingerprint:
                    raise ValueError("相同命令编号已用于不同内容，请使用新的编号。")
                durable = self._find_input(command_id, self._durable_inputs)
                if not durable:
                    raise ValueError("此输入尚未成功保存，请先核对本机数据。")
                return {"receipt": self._input_receipt(durable), "state": self.state()}
            self._require_input_target(params.get("session_id"))
            self._require_run_config()
            if sum(item["status"] in INPUT_PENDING for item in self.input_commands) >= 20:
                raise ValueError("待发送队列最多 20 项。")
            attachment_context = ""
            if attachments:
                from .workspace import WorkspaceView
                view = WorkspaceView(self.cwd, executor=getattr(self.agent, "executor", None))
                attachment_context = await asyncio.to_thread(view.prepare_attachments, attachments)
                self._require_input_target(params.get("session_id"))
            now = time.time()
            item = {"id": command_id, "prompt": prompt, "attachments": copy.deepcopy(attachments),
                    "attachment_context": attachment_context, "content_hash": fingerprint,
                    "status": "queued", "created_at": now, "updated_at": now,
                    "lifecycle": [{"status": "received", "time": now}, {"status": "queued", "time": now}]}
            previous = (copy.deepcopy(self.input_commands), self.queue_paused, self.queue_pause_reason)
            self.input_commands = [*self.input_commands, item]
            self._persist_inputs(previous)
            self._emit({"type": "state", "state": self.state()})
            self._schedule_queue()
            return {"receipt": self._input_receipt(item), "state": self.state()}

    def _schedule_queue(self):
        if (not self.queue_paused and not self._closing and not self._workspace_mutating and not self.recovery
                and not (self.task and not self.task.done())
                and any(item["status"] in INPUT_PENDING for item in self.input_commands)):
            self.task = asyncio.create_task(self._queue_worker())

    def _begin_run(self, prompt, command=None):
        self._require_run_config()
        previous = (self.transcript, self.title, self.status, self.last_error, self._reply_index,
                    copy.deepcopy(self.input_commands), self._active_command)
        message = {"id": uuid.uuid4().hex, "role": "user", "content": prompt}
        if command and command.get("attachments"):
            message["attachments"] = copy.deepcopy(command["attachments"])
        self.transcript = [*self.transcript, message]
        if self.title == "新会话" and not self.title_custom:
            self.title = prompt.strip().replace("\n", " ")[:48]
        self.status, self.last_error, self._reply_index = "running", "", None
        if command:
            self._transition_input(command, "running")
            self._active_command = command["id"]
        try:
            self._save()
        except Exception:
            (self.transcript, self.title, self.status, self.last_error, self._reply_index,
             self.input_commands, self._active_command) = previous
            raise

    async def _queue_worker(self):
        while not self.queue_paused and not self._closing:
            command = next((item for item in self.input_commands if item["status"] in INPUT_PENDING), None)
            if not command:
                break
            try:
                self._begin_run(command["prompt"], command)
            except Exception as exc:
                self.queue_paused = True
                self.queue_pause_reason = "启动或保存失败，请修复后手动继续队列。"
                self.last_error, self.status = f"输入未启动：{exc}", "error"
                self._emit({"type": "state", "state": self.state()})
                break
            self._emit({"type": "state", "state": self.state()})
            prompt = command["prompt"]
            if command.get("attachment_context"):
                prompt += "\n\n[用户附加的文件快照；内容可能已被外部编辑，写入前须读取当前版本]\n" + command["attachment_context"]
            await self._run(prompt, command_id=command["id"])

    async def update_input(self, params):
        async with self._input_lock:
            item = self._find_input(params.get("command_id"))
            if not item or item["status"] not in INPUT_PENDING:
                raise ValueError("只能修改或移除尚未运行的输入。")
            action = params.get("action")
            if action == "edit":
                self._validate_prompt(params.get("prompt"))
            elif action != "remove":
                raise ValueError("输入操作须为 edit 或 remove。")
            previous = (copy.deepcopy(self.input_commands), self.queue_paused, self.queue_pause_reason)
            if action == "edit":
                item["prompt"] = params["prompt"]
            else:
                self._transition_input(item, "cancelled", error="用户移除了尚未执行的输入。")
            item["updated_at"] = time.time()
            self._persist_inputs(previous)
            self._emit({"type": "state", "state": self.state()})
            return self.state()

    async def set_queue_paused(self, paused):
        async with self._input_lock:
            if not self.agent:
                raise ValueError("请先选择工作区。")
            if not paused:
                self._require_input_target(self.session_id)
                self._require_run_config()
            previous = (copy.deepcopy(self.input_commands), self.queue_paused, self.queue_pause_reason)
            self.queue_paused = paused
            self.queue_pause_reason = "用户已暂停队列；当前执行继续，后续输入等待。" if paused else ""
            self._persist_inputs(previous)
            self._emit({"type": "state", "state": self.state()})
            if not paused:
                self._schedule_queue()
            return self.state()

    def get_input_receipt(self, params):
        identifier = params.get("session_id")
        if identifier == self.session_id:
            items = self._durable_inputs
        else:
            snapshot = SessionStore(str(self._session_path(identifier)), cwd=self.cwd).load()
            if snapshot.get("desktop", {}).get("cwd") != self.cwd:
                raise ValueError("回执不属于当前工作区。")
            items = self._load_inputs(snapshot.get("desktop", {}))
        item = self._find_input(params.get("command_id"), items)
        return {"receipt": self._input_receipt(item) if item else None}

    async def _run(self, prompt, command_id=None):
        try:
            if command_id:
                command = self._find_input(command_id)
                if command.get("attachments"):
                    from .workspace import WorkspaceView
                    view = WorkspaceView(self.cwd, executor=getattr(self.agent, "executor", None))
                    await asyncio.to_thread(view.grant_attachment_baselines, command["attachments"])
            await self.agent.run(prompt)
            self.last_error = self.agent.last_error
            self.status = "error" if self.last_error else "idle"
        except asyncio.CancelledError:
            self.status = "cancelled"
        except Exception as exc:
            self.status = "error"
            self.last_error = str(exc)
            self._emit({"type": "error", "message": str(exc)})
        finally:
            try:
                self.recovery = self._unresolved_intents()
            except (OSError, ValueError, TypeError, AttributeError):
                self.recovery = [{"tool": "unknown", "path": "执行日志无法读取，请核对工作区与本机数据。"}]
            if self.recovery:
                self.last_error = "存在未确认的执行。请核对文件后继续。"
            self._reply_index = None
            if self.status in ("error", "cancelled") or self.last_error or self.recovery:
                self.queue_paused = True
                self.queue_pause_reason = "上次输入未完成，请核对结果后手动继续队列。"
            if command_id:
                item = self._find_input(command_id)
                outcome = ("cancelled" if self.status == "cancelled" else
                           "interrupted" if self.recovery else "failed" if self.last_error else "completed")
                self._transition_input(item, outcome)
                if self.last_error:
                    item["error"] = self.last_error
                if outcome != "completed":
                    self.queue_paused = True
                    self.queue_pause_reason = "上次输入未完成，请核对结果后手动继续队列。"
                self._active_command = None
            try:
                self._save(history=True)
            except Exception as exc:
                self.status = "error"
                self.last_error = f"会话保存失败：{exc}"
                self.queue_paused = True
                self.queue_pause_reason = "执行结果未成功保存，请核对本机记录后继续。"
                if command_id:
                    self._transition_input(item, "interrupted", error=self.last_error)
            self._emit({"type": "state", "state": self.state()})
            self._emit({"type": "run_finished", "status": self.status, "error": self.last_error})
            if not command_id and not self.queue_paused:
                asyncio.get_running_loop().call_soon(self._schedule_queue)

    async def dispatch(self, method, params=None):
        params = {} if params is None else params
        if not isinstance(params, dict):
            raise ValueError("参数须为对象。")
        if method == "initialize":
            if self.data_dir:
                raise ValueError("服务已经初始化。")
            data_dir = Path(params["data_dir"]).expanduser()
            if not data_dir.is_absolute():
                raise ValueError("数据目录须为绝对路径。")
            initial_config = params.get("config", {})
            if not isinstance(initial_config, dict):
                raise ValueError("配置须为对象。")
            candidate = {**DEFAULT_CONFIG, **{k: v for k, v in initial_config.items() if k in DEFAULT_CONFIG}}
            self._validate_config(candidate)
            data_dir.mkdir(parents=True, exist_ok=True)
            self.data_dir = data_dir.resolve()
            await self.configure(initial_config)
            return self.state()
        if not self.data_dir:
            raise ValueError("请先初始化服务。")
        if method == "get_state":
            return self.state()
        if method == "submit_input":
            return await self.submit_input(params)
        if method == "get_input_receipt":
            return self.get_input_receipt(params)
        if method == "update_input":
            return await self.update_input(params)
        if method in ("resume_queue", "pause_queue"):
            return await self.set_queue_paused(method == "pause_queue")
        if method in ("workspace_files", "read_workspace_file"):
            if not self.cwd:
                raise ValueError("请先选择工作区。")
            from .workspace import WorkspaceView
            view = WorkspaceView(self.cwd, executor=getattr(self.agent, "executor", None))
            if method == "workspace_files":
                return await asyncio.to_thread(view.files, params.get("path", ""), params.get("query", ""))
            return await asyncio.to_thread(view.read_file, params.get("path", ""))
        if method in ("model_probe", "model_catalog"):
            self._require_idle()
            from .provider_diagnostics import probe, catalog
            return self._clean(await (probe if method == "model_probe" else catalog)(dict(self.config)))
        if method in ("workspace_changes", "workspace_diff", "revert_workspace_change"):
            change_store = getattr(getattr(self.agent, "executor", None), "change_store", None)
            if not change_store:
                raise ValueError("请先选择工作区。")
            if method == "workspace_changes":
                return {"changes": await asyncio.to_thread(change_store.list_changes)}
            if method == "workspace_diff":
                return await asyncio.to_thread(change_store.diff, params.get("change_id"))
            async with self._input_lock:
                self._require_idle()
                if self.config["security_profile"] == "read-only":
                    raise ValueError("只读模式不可恢复文件。")
                self._workspace_mutating = True
                try:
                    previous_changes = {item["id"] for item in await asyncio.to_thread(change_store.list_changes)}
                    intent = {"channel": "ui", "id": uuid.uuid4().hex, "tool": "revert",
                              "path": f"文件变更 {params.get('change_id', '')}"}
                    self._journal({"type": "tool_started", **intent})
                    try:
                        result = await asyncio.to_thread(change_store.revert, params.get("change_id"), params.get("expected_revision"))
                        self._save()
                        self._journal({"type": "tool_completed", "status": "success", "change_id": result.get("id", ""), **intent})
                    except Exception:
                        definitely_unstarted = False
                        try:
                            current_changes = await asyncio.to_thread(change_store.list_changes)
                            definitely_unstarted = not any(item["id"] not in previous_changes for item in current_changes)
                        except Exception:
                            pass
                        if definitely_unstarted:
                            self._journal({"type": "tool_completed", "status": "failed", **intent})
                            raise
                        self.recovery = self._unresolved_intents()
                        self.queue_paused = True
                        self.queue_pause_reason = "文件恢复结果尚未确认，请核对文件后继续。"
                        self.status = "error"
                        self.last_error = "文件恢复未完成或回执未保存，请核对实际文件。"
                        with contextlib.suppress(Exception):
                            self._save()
                        self._emit({"type": "state", "state": self.state()})
                        raise
                    return {"change": result, "changes": await asyncio.to_thread(change_store.list_changes)}
                finally:
                    self._workspace_mutating = False
        if method == "configure":
            return await self.configure(params)
        if method == "open_workspace":
            return await self.open_workspace(params)
        if method == "update_session":
            return self.update_session(params)
        if method == "new_session":
            self._require_idle()
            self._save()
            return await self._new_session()
        if method == "resume_session":
            return await self.resume(params.get("session_id", ""))
        if method == "fork_session":
            self._require_idle()
            if not self.agent or self.recovery:
                raise ValueError("请先恢复会话并核对未确认执行。")
            return await self._new_session(snapshot=self.agent.to_snapshot(), transcript=self.transcript)
        if method == "acknowledge_recovery":
            self._require_idle()
            self._journal({"type": "recovery_acknowledged"})
            if self.recovery:
                self.agent.messages.append({"role": "user", "content":
                    "[THINKFLOW RECOVERY] 用户已核对上次中断的工作区。先读取实际状态再继续；不要重放旧命令，使用新 id。"})
            self.recovery = []
            self.last_error = ""
            self.status = "idle"
            self._save()
            return self._publish_state()
        if method == "run":
            self._require_idle()
            if not self.agent or not self.cwd:
                raise ValueError("请先选择工作区。")
            if self.archived:
                raise ValueError("该会话已归档，请先取消归档或创建新会话。")
            if self.recovery:
                raise ValueError("请先核对并确认上次中断的执行。")
            if not self.config["base_url"] or not self.config["model"]:
                raise ValueError("请先在设置中填写模型端点和模型名称。")
            prompt = params.get("prompt", "")
            if not isinstance(prompt, str) or not prompt.strip() or len(prompt) > 200000:
                raise ValueError("输入须为 1–200000 字符的文本。")
            self._begin_run(prompt)
            self.task = asyncio.create_task(self._run(prompt))
            self._emit({"type": "state", "state": self.state()})
            return {"started": True}
        if method == "cancel":
            self.queue_paused = True
            self.queue_pause_reason = "用户已停止执行，请核对结果后手动继续队列。"
            if self.task and not self.task.done():
                self.task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await self.task
            self._save()
            return self._publish_state()
        if method == "approve":
            if not self.pending_approval or params.get("request_id") != self.pending_approval["request_id"]:
                raise ValueError("授权请求已失效。")
            if type(params.get("approved")) is not bool:
                raise ValueError("授权选择须为布尔值。")
            if self.approval_future and not self.approval_future.done():
                self.approval_future.set_result(params["approved"])
            return {"accepted": True}
        if method == "compact":
            self._require_idle()
            if self.agent:
                self.agent.compact(force=True)
                self._save()
            return self._publish_state()
        if method == "export_session":
            content = [f"# {self.title}", f"工作区：{self.cwd}"]
            for message in self.transcript:
                label = "用户" if message["role"] == "user" else "续想"
                content += [f"\n## {label}", message["content"]]
            content.append("\n## 执行账本（最近500条；旧正文可能已摘录）\n")
            for record in self.state()["ledger"]:
                content.append(f"- {record['id']} · {record['tool']} · {record['status']} · {record['path'] or ''}")
            markdown = self._clean("\n\n".join(content))
            if len(markdown) > 16000000:
                raise ValueError("该会话超过单次导出上限；完整记录仍保留在本机数据目录中。")
            return {"markdown": markdown}
        if method == "shutdown":
            await self.close()
            self._shutdown = True
            return {"closed": True}
        raise ValueError(f"未知方法：{method}")

    async def close(self):
        self._closing = True
        if self.task and not self.task.done():
            self.task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self.task
        self._save()
        if self.agent:
            await self.agent.close()


async def serve():
    # Keep the wire on the original stream; legacy diagnostics belong on stderr.
    wire = sys.stdout
    sys.stdout = sys.stderr

    def write(payload):
        wire.write(json.dumps(payload, ensure_ascii=False) + "\n")
        wire.flush()

    service = DesktopService(lambda event: write({"event": event}))
    try:
        while not service._shutdown:
            line = await asyncio.to_thread(sys.stdin.readline)
            if not line:
                break
            request_id = None
            try:
                if len(line) > 1000000:
                    raise ValueError("请求过大。")
                request = json.loads(line)
                if not isinstance(request, dict):
                    raise ValueError("请求须为对象。")
                request_id = request.get("id")
                result = await service.dispatch(request.get("method"), request.get("params"))
                write({"id": request_id, "result": result})
            except Exception as exc:
                write({"id": request_id, "error": {"message": service._clean(str(exc))}})
    finally:
        await service.close()
        sys.stdout = wire


def main():
    for stream in (sys.stdin, sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    asyncio.run(serve())


if __name__ == "__main__":
    main()
