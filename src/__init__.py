# ThinkFlow
"""
续想 ThinkFlow — 流式执行 Agent 框架

让大模型在 thinking/text 流中输出工具命令，由流式解析器实时执行，不中断推理。
"""

from .parser import StreamingParser, Command
from .executor import Executor, ExecutionResult
from .context import ContextManager, CommandRecord
from .agent_loop import AgentLoop, AgentConfig
from .compaction import CompactionConfig
from .text_filter import SafeTextStreamFilter

__version__ = "0.5.0-beta.2"
