"""Dynamic tool registry for ThinkFlow.

The registry keeps provider schemas and execution handlers together. It lets
ThinkFlow expose more interfaces without turning the agent loop into a long
if/elif chain.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Awaitable, Callable
import uuid

from .executor import ExecutionResult


class ToolResult(str):
    """Text-compatible tool output with explicit execution status."""

    def __new__(cls, content: str, *, success: bool = True):
        result = super().__new__(cls, content)
        result.success = success
        result.error = "" if success else content
        return result

    @classmethod
    def failure(cls, error: str) -> "ToolResult":
        return cls(error, success=False)


ToolHandler = Callable[..., Awaitable[str | ToolResult | ExecutionResult]]

TOOL_KIND_INPUT = "input"
TOOL_KIND_OUTPUT = "output"
TOOL_KIND_EXEC = "exec"
TOOL_KIND_GENERATE = "generate"
TOOL_KIND_STATE = "state"

TOOL_FLOW_DELAYED = "delayed"
TOOL_FLOW_BLOCKING = "blocking"
TOOL_FLOW_CONFIRM = "confirm"

TOOL_RISK_LOW = "low"
TOOL_RISK_MEDIUM = "medium"
TOOL_RISK_HIGH = "high"

TOOL_KIND_LABELS = {
    TOOL_KIND_INPUT: "INPUT",
    TOOL_KIND_OUTPUT: "OUTPUT",
    TOOL_KIND_EXEC: "EXEC",
    TOOL_KIND_GENERATE: "GEN",
    TOOL_KIND_STATE: "状态",
}

TOOL_FLOW_LABELS = {
    TOOL_FLOW_DELAYED: "DELAY",
    TOOL_FLOW_BLOCKING: "BLOCK",
    TOOL_FLOW_CONFIRM: "ASK",
}

TOOL_RISK_LABELS = {
    TOOL_RISK_LOW: "LOW",
    TOOL_RISK_MEDIUM: "MED",
    TOOL_RISK_HIGH: "HIGH",
}


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str | Callable[[], str]
    parameters: dict
    kind: str = TOOL_KIND_INPUT
    flow: str = TOOL_FLOW_BLOCKING
    risk: str = TOOL_RISK_LOW
    handler: ToolHandler | None = None
    pass_call_id: bool = False

    def without_handler(self) -> dict:
        return {
            "name": self.name,
            "description": self.description() if callable(self.description) else self.description,
            "parameters": self.parameters,
        }


class ToolRegistry:
    """Provider-neutral tool registry."""

    def __init__(self):
        self._tools: dict[str, ToolSpec] = {}

    def register(self, spec: ToolSpec, handler: ToolHandler, *, pass_call_id: bool = False) -> None:
        self._tools[spec.name] = ToolSpec(
            name=spec.name,
            description=spec.description,
            parameters=spec.parameters,
            kind=spec.kind,
            flow=spec.flow,
            risk=spec.risk,
            handler=handler,
            pass_call_id=pass_call_id,
        )

    def has(self, name: str) -> bool:
        return name in self._tools

    def schemas(self, *, exclude=()) -> list[dict]:
        return [tool.without_handler() for name, tool in self._tools.items() if name not in exclude]

    def kind(self, name: str) -> str:
        spec = self._tools.get(name)
        if spec:
            return spec.kind
        return classify_tool_kind(name)

    def kind_map(self) -> dict[str, str]:
        return {name: spec.kind for name, spec in self._tools.items()}

    def information_only(self, name: str) -> bool:
        return (self.kind(name) == TOOL_KIND_INPUT and self.flow(name) == TOOL_FLOW_BLOCKING
                and name not in {'update_plan', 'load_tools'})

    def flow(self, name: str) -> str:
        spec = self._tools.get(name)
        if spec:
            return spec.flow
        return classify_tool_flow(name)

    def flow_map(self) -> dict[str, str]:
        return {name: spec.flow for name, spec in self._tools.items()}

    def risk(self, name: str) -> str:
        spec = self._tools.get(name)
        if spec:
            return spec.risk
        return classify_tool_risk(name)

    def risk_map(self) -> dict[str, str]:
        return {name: spec.risk for name, spec in self._tools.items()}

    def openai_tools(self) -> list[dict]:
        return [
            {
                "type": "function",
                "function": {
                    "name": schema["name"],
                    "description": schema["description"],
                    "parameters": schema["parameters"],
                },
            }
            for schema in self.schemas()
        ]

    def anthropic_tools(self) -> list[dict]:
        return [
            {
                "name": schema["name"],
                "description": schema["description"],
                "input_schema": schema["parameters"],
            }
            for schema in self.schemas()
        ]

    async def execute(self, name: str, tool_input: dict, *, call_id: str = "") -> ToolResult | ExecutionResult:
        spec = self._tools.get(name)
        if not spec or not spec.handler:
            return ToolResult.failure(f"未知工具: {name}")
        try:
            result = (await spec.handler(tool_input, call_id=call_id or uuid.uuid4().hex)
                      if spec.pass_call_id else await spec.handler(tool_input))
        except Exception as exc:
            return ToolResult.failure(f"{name} failed: {exc}")
        if isinstance(result, (ToolResult, ExecutionResult)):
            return result
        if isinstance(result, str):
            return ToolResult(result)
        return ToolResult.failure(f"{name} returned an unsupported result type: {type(result).__name__}")


BUILTIN_TOOL_SPECS = [
    ToolSpec(
        name="read",
        description="读取文件；offset为从1开始的行号，limit为行数，省略两者读取全文。",
        parameters={
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "文件路径"},
                "offset": {"type": "integer", "minimum": 1, "description": "起始行号，从1开始"},
                "limit": {"type": "integer", "minimum": 1, "description": "读取行数"},
            },
            "required": ["path"],
        },
    ),
    ToolSpec(
        name="recall",
        description="按引用读取原始用户要求或已保存工具输出的字符区间。",
        parameters={
            "type": "object",
            "properties": {
                "ref": {"type": "string", "minLength": 1, "description": "原文引用，如request:1、request:1:input或receipt:3:stdout"},
                "offset": {"type": "integer", "minimum": 0, "default": 0},
                "limit": {"type": "integer", "minimum": 1, "maximum": 200000, "default": 8000},
                "query": {"type": "string", "minLength": 1,
                          "description": "可选字面关键词；从offset找首个匹配，返回匹配行片段"},
            },
            "required": ["ref"],
        },
    ),
    ToolSpec(
        name="pwd",
        description="显示当前工作目录。用于确认相对路径会从哪里解析。",
        parameters={
            "type": "object",
            "properties": {},
        },
    ),
    ToolSpec(
        name="list_files",
        description="列出目录内容，适合快速了解项目结构。遵守工作区 .gitignore。",
        parameters={
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "目录路径，默认当前工作区"},
                "recursive": {"type": "boolean", "description": "是否递归列出"},
                "max_entries": {"type": "integer", "description": "最多返回多少条"},
            },
        },
    ),
    ToolSpec(
        name="glob",
        description="按 glob 模式查找文件路径，遵守工作区 .gitignore。",
        parameters={
            "type": "object",
            "properties": {
                "pattern": {"type": "string", "description": "glob 模式，如 src/**/*.py"},
                "path": {"type": "string", "description": "搜索根目录，默认当前工作区"},
                "max_results": {"type": "integer", "description": "最多返回多少条"},
            },
            "required": ["pattern"],
        },
    ),
    ToolSpec(
        name="grep",
        description="用 Python 正则搜索文本，返回路径、行号和命中行。目录搜索遵守工作区 .gitignore，也可直接指定文件。",
        parameters={
            "type": "object",
            "properties": {
                "pattern": {"type": "string", "description": "正则表达式"},
                "path": {"type": "string", "description": "文件或目录路径"},
                "file_glob": {"type": "string", "description": "文件过滤，如 *.py"},
                "case_sensitive": {"type": "boolean", "description": "是否大小写敏感"},
                "max_results": {"type": "integer", "description": "最多返回多少条"},
            },
            "required": ["pattern"],
        },
    ),
    ToolSpec(
        name="bash",
        description="执行 shell 命令并返回 stdout/stderr/exit_code。",
        kind=TOOL_KIND_EXEC,
        flow=TOOL_FLOW_BLOCKING,
        risk=TOOL_RISK_HIGH,
        parameters={
            "type": "object",
            "properties": {"cmd": {"type": "string", "description": "要执行的命令"}},
            "required": ["cmd"],
        },
    ),
    ToolSpec(
        name="python",
        description="直接执行多行Python代码，返回stdout/stderr/exit_code。适合数据变换和集中检查；产物修改使用文件工具以保留版本记录。一段代码可完成多个已知步骤，无须shell转义、heredoc或临时脚本。每次调用是新的Python进程，工作目录与bash相同。",
        kind=TOOL_KIND_EXEC,
        flow=TOOL_FLOW_BLOCKING,
        risk=TOOL_RISK_HIGH,
        parameters={
            "type": "object",
            "properties": {"code": {"type": "string", "description": "多行Python源代码；print需要反馈的结果"}},
            "required": ["code"],
        },
    ),
    ToolSpec(
        name="write",
        description="覆盖写入文件。输出式写文件优先用 tf-write 流式协议；此工具用于兼容传统 tool_call。",
        kind=TOOL_KIND_OUTPUT,
        flow=TOOL_FLOW_DELAYED,
        risk=TOOL_RISK_LOW,
        parameters={
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "文件路径"},
                "content": {"type": "string", "description": "完整文件内容"},
            },
            "required": ["path", "content"],
        },
    ),
    ToolSpec(
        name="append",
        description="追加写入文件。输出式追加优先用 tf-append 流式协议；此工具用于兼容传统 tool_call。",
        kind=TOOL_KIND_OUTPUT,
        flow=TOOL_FLOW_DELAYED,
        risk=TOOL_RISK_LOW,
        parameters={
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "文件路径"},
                "content": {"type": "string", "description": "要追加的内容"},
            },
            "required": ["path", "content"],
        },
    ),
    ToolSpec(
        name="edit",
        description="精确替换当前版本已读取范围内的唯一文本片段。",
        kind=TOOL_KIND_OUTPUT,
        flow=TOOL_FLOW_DELAYED,
        risk=TOOL_RISK_MEDIUM,
        parameters={
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "文件路径"},
                "old_text": {"type": "string", "description": "要替换的唯一旧文本"},
                "new_text": {"type": "string", "description": "替换后的新文本"},
            },
            "required": ["path", "old_text", "new_text"],
        },
    ),
    ToolSpec(
        name="mkdir",
        description="创建目录，包括父目录。",
        kind=TOOL_KIND_OUTPUT,
        flow=TOOL_FLOW_DELAYED,
        risk=TOOL_RISK_LOW,
        parameters={
            "type": "object",
            "properties": {"path": {"type": "string", "description": "目录路径"}},
            "required": ["path"],
        },
    ),
    ToolSpec(
        name="touch",
        description="创建空文件或更新文件时间戳。",
        kind=TOOL_KIND_OUTPUT,
        flow=TOOL_FLOW_DELAYED,
        risk=TOOL_RISK_LOW,
        parameters={
            "type": "object",
            "properties": {"path": {"type": "string", "description": "文件路径"}},
            "required": ["path"],
        },
    ),
    ToolSpec(
        name="copy",
        description="复制文件到目标路径。",
        kind=TOOL_KIND_OUTPUT,
        flow=TOOL_FLOW_DELAYED,
        risk=TOOL_RISK_LOW,
        parameters={
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "源文件路径"},
                "dest": {"type": "string", "description": "目标文件路径"},
            },
            "required": ["path", "dest"],
        },
    ),
]


def classify_tool_kind(name: str) -> str:
    if name == 'update_plan':
        return TOOL_KIND_STATE
    if name in {"write", "append", "edit", "mkdir", "touch", "copy"}:
        return TOOL_KIND_OUTPUT
    if name in {"bash"} or name.startswith("custom_"):
        return TOOL_KIND_EXEC
    if name in {"image_generate"}:
        return TOOL_KIND_GENERATE
    return TOOL_KIND_INPUT


def classify_tool_flow(name: str) -> str:
    if name in {"write", "append", "mkdir", "touch", "copy", "edit", "update_plan"}:
        return TOOL_FLOW_DELAYED
    if name in {"bash"} or name.startswith("custom_"):
        return TOOL_FLOW_CONFIRM
    return TOOL_FLOW_BLOCKING


def classify_tool_risk(name: str) -> str:
    if name in {"bash"} or name.startswith("custom_"):
        return TOOL_RISK_HIGH
    if name in {"edit", "image_generate"}:
        return TOOL_RISK_MEDIUM
    return TOOL_RISK_LOW
