"""Best-effort native console titles for interactive CLI sessions."""

import ctypes
import sys
from contextlib import contextmanager, suppress


def _open_console():
    if sys.platform != 'win32':
        return None
    try:
        if not (sys.stdin.isatty() and sys.stdout.isatty()):
            return None
        kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        get_title = kernel.GetConsoleTitleW
        get_title.argtypes = [ctypes.POINTER(ctypes.c_wchar), ctypes.c_uint32]
        get_title.restype = ctypes.c_uint32
        set_title = kernel.SetConsoleTitleW
        set_title.argtypes = [ctypes.c_wchar_p]
        set_title.restype = ctypes.c_int
        buffer = ctypes.create_unicode_buffer(32768)
        ctypes.set_last_error(0)
        length = get_title(buffer, len(buffer))
        if (not length and ctypes.get_last_error()) or length >= len(buffer):
            return None
        return buffer.value, set_title
    except (AttributeError, OSError, ValueError):
        return None


def _topic(text):
    if not isinstance(text, str):
        return ''
    preview = []
    for char in text:
        if char.isspace() or not char.isprintable():
            if preview and preview[-1] != ' ':
                preview.append(' ')
        elif len(preview) >= 48:
            return ''.join(preview[:47]).rstrip() + '…'
        else:
            preview.append(char)
    return ''.join(preview).rstrip()


def _first_topic(agent):
    if agent.conversation.requests:
        for request in agent.conversation.requests:
            if isinstance(request, dict) and (topic := _topic(request.get('text'))):
                return topic
        return ''
    for message in agent.messages:
        if not isinstance(message, dict) or message.get('role') != 'user':
            continue
        content = message.get('content')
        if isinstance(content, list):
            if any(isinstance(block, dict) and block.get('type') == 'tool_result' for block in content):
                continue
            content = ' '.join(block['text'] for block in content if isinstance(block, dict)
                               and block.get('type') == 'text' and isinstance(block.get('text'), str))
        if isinstance(content, str):
            text = content.lstrip()
            suffix = text.find('\n\n[THINKFLOW ')
            if suffix >= 0:
                text = text[:suffix]
            if not text.startswith('[THINKFLOW ') and (topic := _topic(text)):
                return topic
    return ''


@contextmanager
def session_title(agent):
    console = _open_console()
    if console is None:
        yield None
        return
    original, write = console
    current = None

    def set_title(title):
        with suppress(OSError, ValueError):
            write(title)

    def refresh():
        nonlocal current
        topic = _first_topic(agent)
        if topic != current:
            current = topic
            set_title(f'ThinkFlow - {topic}' if topic else 'ThinkFlow')

    def observe(event):
        if not current and event['type'] == 'turn_started':
            refresh()

    agent.events.observers.append(observe)
    try:
        refresh()
        yield refresh
    finally:
        agent.events.observers.remove(observe)
        set_title(original)
