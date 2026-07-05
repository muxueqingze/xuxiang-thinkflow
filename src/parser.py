"""
ThinkFlow Parser — 流式命令块解析器

逐字符扫描 thinking 流，提取 XML 格式的命令块。
状态机处理流式输入，支持不完整命令的缓冲等待。
"""

import re
from dataclasses import dataclass
from enum import Enum
from typing import Optional


# 支持的工具类型。append/touch/copy 属于可预测的确定性流式工具。
TOOLS = ("read", "write", "append", "mkdir", "touch", "copy", "bash", "edit")

# 最大缓冲区大小（防止格式错误导致无限增长）
MAX_BUFFER_SIZE = 2 * 1024 * 1024  # 2MB


class ParseState(Enum):
    """解析器状态"""
    IDLE = "idle"              # 扫描正常文本，寻找命令开始标签
    IN_TAG = "in_tag"          # 读取开标签属性
    IN_CONTENT = "in_content"  # 读取命令正文，等待结束标签


@dataclass
class Command:
    """解析出的命令"""
    id: str
    tool: str                          # read / write / append / mkdir / touch / copy / bash / edit
    path: Optional[str] = None
    dest: Optional[str] = None         # copy 目标路径
    cmd: Optional[str] = None          # bash 命令
    content: Optional[str] = None      # write 正文
    old_text: Optional[str] = None     # edit 旧文本
    new_text: Optional[str] = None     # edit 新文本
    need_result: bool = False
    injected: int = 0
    raw: str = ""                      # 原始命令块文本


@dataclass
class ParseError:
    """解析错误"""
    message: str
    raw: str = ""
    position: int = 0


def build_open_tag_re(allow_legacy_tags: bool = True) -> re.Pattern:
    """Build command open-tag regex.

    Canonical executable tags are namespaced with ``tf-`` (``<tf-write>``).
    Legacy tags are still available for migration tests, but the agent runtime
    defaults to canonical-only mode to avoid executing XML examples by accident.
    """
    prefix = r'(?P<prefix>tf-)'
    if allow_legacy_tags:
        prefix = r'(?P<prefix>tf-)?'
    return re.compile(
        r'<' + prefix + r'(?P<tool>' + '|'.join(TOOLS) + r')\s+'
        r'(?P<attrs>[^>]*?)(?P<self_close>/?)>',
        re.DOTALL,
    )


# Backward-compatible regex for helper modules/tests that import OPEN_TAG_RE.
OPEN_TAG_RE = build_open_tag_re(allow_legacy_tags=True)


EDIT_OLD_RE = re.compile(r'<old>(?P<old>.*?)</old>', re.DOTALL)
EDIT_NEW_RE = re.compile(r'<new>(?P<new>.*?)</new>', re.DOTALL)


class StreamingParser:
    """
    流式命令块解析器。

    用法：
        parser = StreamingParser()
        for text in thinking_stream:
            commands = parser.feed(text)
            for cmd in commands:
                # 处理命令
                pass
        # 流结束后
        incomplete = parser.flush()  # 检查残留
    """

    def __init__(self, allow_legacy_tags: bool = True):
        self.buffer: str = ""
        self.errors: list[ParseError] = []
        self._seen_ids: set[str] = set()
        self.allow_legacy_tags = allow_legacy_tags
        self._open_tag_re = build_open_tag_re(allow_legacy_tags)

    def feed(self, text: str) -> list[Command]:
        """
        喂入一段 thinking 文本，返回这段文本中提取到的完整命令列表。

        不完整的命令块会保留在缓冲区，等待后续输入。
        """
        self.buffer += text

        # 防止缓冲区无限增长
        if len(self.buffer) > MAX_BUFFER_SIZE:
            self.errors.append(ParseError(
                message=f"缓冲区超过上限 {MAX_BUFFER_SIZE}，可能有未闭合标签。清空缓冲区。",
                raw=self.buffer[:200],
            ))
            self.buffer = ""
            return []

        commands: list[Command] = []

        while True:
            cmd = self._extract_next()
            if cmd is None:
                break
            commands.append(cmd)

        return commands

    def flush(self) -> Optional[ParseError]:
        """
        流结束后调用。检查缓冲区中是否有残留的不完整命令块。

        返回 None 表示干净。返回 ParseError 表示有残缺命令。
        """
        if not self.buffer.strip():
            self.buffer = ""
            return None

        start = self._find_open_start()
        if start is not None:
            parsed = self._parse_open_tag(start)
            if parsed is None:
                error = ParseError(
                    message="检测到未完成的 ThinkFlow 命令开标签（流被截断）",
                    raw=self.buffer[start:start + 200],
                    position=start,
                )
                self.errors.append(error)
                self.buffer = ""
                return error
            tool, prefix, _attrs_str, self_closing, tag_end = parsed
            close_tag = f"</{prefix}{tool}>"
            if not self_closing and close_tag not in self.buffer[tag_end:]:
                error = ParseError(
                    message=f"检测到未闭合的 <{prefix}{tool}> 命令块（流被截断）",
                    raw=self.buffer[start:start + 200],
                    position=start,
                )
                self.errors.append(error)
                self.buffer = ""
                return error

        self.buffer = ""
        return None

    def _extract_next(self) -> Optional[Command]:
        """尝试从缓冲区提取下一个完整命令块。"""
        start = self._find_open_start()
        if start is None:
            self._keep_possible_tag_tail()
            return None

        if start > 0:
            self.buffer = self.buffer[start:]
            start = 0

        parsed = self._parse_open_tag(start)
        if parsed is None:
            self.buffer = self.buffer[start:]
            return None

        tool, prefix, attrs_str, self_closing, tag_end = parsed

        if self_closing:
            full = self.buffer[start:tag_end]
            cmd = self._build_command(tool, attrs_str, None, full)
            self.buffer = self.buffer[tag_end:]
            if cmd:
                return cmd
            return None

        close_tag = f"</{prefix}{tool}>"
        close_pos = self.buffer.find(close_tag, tag_end)

        if close_pos == -1:
            self.buffer = self.buffer[start:]
            return None

        content = self.buffer[tag_end:close_pos]
        end = close_pos + len(close_tag)
        full = self.buffer[start:end]

        cmd = self._build_command(tool, attrs_str, content, full)
        self.buffer = self.buffer[end:]

        if cmd:
            return cmd
        return None

    def _find_open_start(self) -> Optional[int]:
        """Find the first executable ThinkFlow tag candidate in the buffer."""
        best: Optional[int] = None
        starts = [f"<tf-{tool}" for tool in TOOLS]
        if self.allow_legacy_tags:
            starts.extend(f"<{tool}" for tool in TOOLS)
        for needle in starts:
            pos = self.buffer.find(needle)
            if pos >= 0 and (best is None or pos < best):
                best = pos
        return best

    def _keep_possible_tag_tail(self) -> None:
        last_lt = self.buffer.rfind("<")
        if last_lt < 0:
            self.buffer = ""
            return
        tail = self.buffer[last_lt:]
        self.buffer = tail if self._is_potential_tag_prefix(tail) else ""

    def _is_potential_tag_prefix(self, tail: str) -> bool:
        if not tail or tail == "<":
            return True
        starts = [f"<tf-{tool}" for tool in TOOLS]
        if self.allow_legacy_tags:
            starts.extend(f"<{tool}" for tool in TOOLS)
        return any(start.startswith(tail) or tail.startswith(start) for start in starts)

    def _parse_open_tag(self, start: int) -> Optional[tuple[str, str, str, bool, int]]:
        tag_end = self._find_unquoted_gt(start)
        if tag_end < 0:
            return None
        tag = self.buffer[start:tag_end + 1]
        if not tag.startswith("<") or tag.startswith("</"):
            return None
        inner = tag[1:-1].strip()
        self_closing = inner.endswith("/")
        if self_closing:
            inner = inner[:-1].rstrip()
        if not inner:
            return None
        name, sep, attrs_str = inner.partition(" ")
        if not sep:
            attrs_str = ""
        prefix = ""
        tool = name
        if name.startswith("tf-"):
            prefix = "tf-"
            tool = name[3:]
        elif not self.allow_legacy_tags:
            return None
        if tool not in TOOLS:
            return None
        return tool, prefix, attrs_str.strip(), self_closing, tag_end + 1

    def _find_unquoted_gt(self, start: int) -> int:
        quote = ""
        escaped = False
        for index in range(start + 1, len(self.buffer)):
            ch = self.buffer[index]
            if escaped:
                escaped = False
                continue
            if ch == "\\":
                escaped = True
                continue
            if quote:
                if ch == quote:
                    quote = ""
                continue
            if ch in ('"', "'"):
                quote = ch
                continue
            if ch == ">":
                return index
        return -1
    def _build_command(
        self, tool: str, attrs_str: str,
        content: Optional[str], raw: str
    ) -> Optional[Command]:
        """从提取的标签属性和正文构建 Command 对象。"""
        attrs = _parse_attrs(attrs_str)
        cmd_id = attrs.get("id", "")
        if not cmd_id:
            self.errors.append(ParseError(
                message=f"<{tool}> 缺少 id 属性",
                raw=raw[:100],
            ))
            return None
        if not cmd_id.isdigit():
            self.errors.append(ParseError(
                message=f"<{tool}> id 必须是数字: {cmd_id}",
                raw=raw[:100],
            ))
            return None

        # 检查 id 重复
        if cmd_id in self._seen_ids:
            self.errors.append(ParseError(
                message=f"id={cmd_id} 重复",
                raw=raw[:100],
            ))
            return None
        self._seen_ids.add(cmd_id)

        # 解析其他属性
        path = attrs.get("path")
        dest = attrs.get("dest")
        cmd_str = attrs.get("cmd")
        need_result = attrs.get("need_result") == "true"
        injected = int(attrs.get("injected", "0") or "0")

        # 按工具类型校验必需属性
        if tool in ("read", "write", "append", "mkdir", "touch", "copy", "edit") and not path:
            self.errors.append(ParseError(
                message=f"<{tool} id={cmd_id}> 缺少 path 属性",
                raw=raw[:100],
            ))
            return None

        if tool == "copy" and not dest:
            self.errors.append(ParseError(
                message=f"<copy id={cmd_id}> 缺少 dest 属性",
                raw=raw[:100],
            ))
            return None

        if tool == "bash" and not cmd_str:
            self.errors.append(ParseError(
                message=f"<bash id={cmd_id}> 缺少 cmd 属性",
                raw=raw[:100],
            ))
            return None

        # 构建 Command
        command = Command(
            id=cmd_id,
            tool=tool,
            path=path,
            dest=dest,
            cmd=cmd_str,
            need_result=need_result,
            injected=injected,
            raw=raw,
        )

        # 按工具处理正文
        if tool in ("write", "append"):
            command.content = content or ""

        elif tool == "edit":
            if content:
                old_match = EDIT_OLD_RE.search(content)
                new_match = EDIT_NEW_RE.search(content)
                if not old_match or not new_match:
                    self.errors.append(ParseError(
                        message=f"<edit id={cmd_id}> 缺少 <old> 或 <new> 子标签",
                        raw=raw[:200],
                    ))
                    return None
                command.old_text = old_match.group("old")
                command.new_text = new_match.group("new")
            else:
                self.errors.append(ParseError(
                    message=f"<edit id={cmd_id}> 缺少正文（<old>/<new>）",
                    raw=raw[:100],
                ))
                return None

        # mkdir / touch / copy / bash 自闭合，无正文

        return command

    def reset(self):
        """重置解析器（新会话时调用）。"""
        self.buffer = ""
        self.errors.clear()
        self._seen_ids.clear()


def _parse_attrs(attrs: str) -> dict[str, str]:
    """Parse quoted XML-like attributes without treating escaped quotes as terminators."""
    result: dict[str, str] = {}
    i = 0
    n = len(attrs or "")
    while i < n:
        while i < n and attrs[i].isspace():
            i += 1
        name_start = i
        if i >= n or not (attrs[i].isalpha() or attrs[i] == "_"):
            i += 1
            continue
        i += 1
        while i < n and (attrs[i].isalnum() or attrs[i] in "_-"):
            i += 1
        name = attrs[name_start:i]
        while i < n and attrs[i].isspace():
            i += 1
        if i >= n or attrs[i] != "=":
            continue
        i += 1
        while i < n and attrs[i].isspace():
            i += 1
        if i >= n or attrs[i] not in ('"', "'"):
            continue
        quote = attrs[i]
        i += 1
        value_chars: list[str] = []
        escaped = False
        while i < n:
            ch = attrs[i]
            i += 1
            if escaped:
                value_chars.append(ch)
                escaped = False
                continue
            if ch == "\\":
                escaped = True
                value_chars.append(ch)
                continue
            if ch == quote:
                break
            value_chars.append(ch)
        result[name] = "".join(value_chars)
    return result
