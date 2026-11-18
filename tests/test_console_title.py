import asyncio
import ctypes
import io
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import httpx
from prompt_toolkit import Application, PromptSession
from prompt_toolkit.application import create_app_session
from prompt_toolkit.input import DummyInput
from prompt_toolkit.output import DummyOutput
from rich.console import Console

from src import cli, console_title, renderer
from src.agent_loop import AgentConfig, AgentLoop
from src.events import RuntimeEvents
from src.provider import ProviderConfig
from src.session import SessionStore


class ConsoleTitleTests(unittest.TestCase):
    def setUp(self):
        self.agent = SimpleNamespace(conversation=SimpleNamespace(requests=[]), messages=[], events=RuntimeEvents())

    def test_title_pins_original_request_and_restores_even_on_exception(self):
        writes = []
        with patch.object(console_title, '_open_console', return_value=('', writes.append)):
            with self.assertRaisesRegex(RuntimeError, 'fixture exit'), console_title.session_title(self.agent):
                self.agent.messages.append({'role': 'user', 'content': 'expanded input'})
                self.agent.conversation.requests.extend([
                    {'text': ' \t'}, {'text': '例题\n\t标题\x00 ' + 'x' * 200, 'execution_input': 'expanded input'},
                ])
                self.agent.events.emit('turn_started')
                self.agent.conversation.requests.append({'text': 'second request'})
                self.agent.events.emit('turn_started')
                raise RuntimeError('fixture exit')
        self.assertEqual(len(writes), 3)
        self.assertEqual((writes[0], writes[-1]), ('ThinkFlow', ''))
        self.assertTrue(writes[1].startswith('ThinkFlow - 例题 标题 '))
        self.assertLess(len(writes[1]), 100)
        self.assertFalse(self.agent.events.observers)

    def test_legacy_plaintext_excludes_attached_data(self):
        self.agent.messages = [{'role': 'user', 'content': 'Original task\n\n[THINKFLOW ATTACHED FILES]\nfixture body'}]
        writes = []
        with (patch.object(console_title, '_open_console', return_value=('shell', writes.append)),
              console_title.session_title(self.agent)):
            self.assertEqual(writes, ['ThinkFlow - Original task'])

    def test_redirection_and_unsupported_platform_never_open_kernel(self):
        for platform, stdin, stdout in [('linux', True, True), ('win32', False, True), ('win32', True, False)]:
            with (self.subTest(platform=platform, stdin=stdin, stdout=stdout),
                  patch.object(console_title.sys, 'platform', platform),
                  patch.object(console_title.sys, 'stdin', Mock(isatty=lambda: stdin)),
                  patch.object(console_title.sys, 'stdout', Mock(isatty=lambda: stdout)),
                  patch.object(ctypes, 'WinDLL', create=True) as loader,
                  console_title.session_title(self.agent)):
                self.assertFalse(self.agent.events.observers)
                loader.assert_not_called()

    def test_windows_api_capture_set_restore_and_failure(self):
        for original, error in [('shell 窗口', 0), ('', 0), ('', 6)]:
            with (self.subTest(original=original, error=error), patch.object(console_title.sys, 'platform', 'win32'),
                  patch.object(console_title.sys, 'stdin', Mock(isatty=lambda: True)),
                  patch.object(console_title.sys, 'stdout', Mock(isatty=lambda: True)),
                  patch.object(ctypes, 'WinDLL', create=True) as loader,
                  patch.object(ctypes, 'set_last_error', create=True),
                  patch.object(ctypes, 'get_last_error', create=True, return_value=error)):
                kernel = loader.return_value

                def get_title(buffer, capacity):
                    self.assertGreater(capacity, len(original))
                    buffer.value = original
                    return len(original)

                kernel.GetConsoleTitleW.side_effect = get_title
                kernel.SetConsoleTitleW.side_effect = [1, OSError('detached')]
                with console_title.session_title(self.agent):
                    pass
                self.assertEqual([call.args[0] for call in kernel.SetConsoleTitleW.call_args_list],
                                 [] if error else ['ThinkFlow', original])


class InteractiveTitleTests(unittest.TestCase):
    def run_cli(self, root, agent, *options):
        config = {'base_url': 'https://fixture.invalid', 'model': 'fixture', 'api_key': 'fixture'}
        with (patch.dict(os.environ, THINKFLOW_HOME=str(root / 'home')),
              patch.object(cli, 'load_configuration') as settings,
              patch.object(cli, 'resolve_system_prompt', return_value=''),
              patch.object(cli, 'create_agent', return_value=agent),
              patch.object(cli.sys, 'argv', ['thinkflow', '--cwd', str(root), '--session', str(root / 'start.json'),
                                             '--no-autosave', *options])):
            settings.return_value.resolve.return_value = (config, {})
            cli.main()

    def test_all_views_resume_switch_run_clear_and_exit(self):
        for mode in [[], ['--legacy-ui'], ['--tui']]:
            with (self.subTest(mode=mode), tempfile.TemporaryDirectory() as directory,
                  patch.object(renderer, 'console', Console(file=io.StringIO(), theme=renderer.THEME)),
                  create_app_session(input=DummyInput(), output=DummyOutput())):
                root = Path(directory)
                SessionStore(str(root / 'start.json'), cwd=directory).save({
                    'conversation': {'requests': [{'ref': 'request:1', 'text': 'Saved request'}]},
                    'messages': [{'role': 'user', 'content': 'Expanded input'}],
                }, history=False)
                SessionStore(str(root / 'old.json'), cwd=directory).save({'messages': [
                    {'role': 'system', 'content': 'System prompt'},
                    {'role': 'user', 'content': '[THINKFLOW COMPACTED CONTEXT]\nSummary'},
                    {'role': 'user', 'content': [{'type': 'tool_result', 'tool_use_id': 'call-1', 'content': 'Tool output'}]},
                    {'role': 'user', 'content': [{'type': 'text', 'text': 'Old request'}]},
                ]}, history=False)
                commands = iter(['/resume missing.json', '/clear', *(['/resume old.json'] if mode else ['/resume', str(root / 'old.json')]),
                                 'Follow-up', '/new', 'Fresh request', '/exit'])
                completed = asyncio.Event()
                writes = []

                run_queue = cli.InteractiveController._run_queue

                async def drain(controller):
                    await run_queue(controller)
                    completed.set()

                async def after_input(line):
                    if line in ('Follow-up', 'Fresh request'):
                        await asyncio.wait_for(completed.wait(), 3)
                        completed.clear()

                previous = ''

                async def prompt(*args, **kwargs):
                    nonlocal previous
                    await after_input(previous)
                    previous = next(commands)
                    return previous

                async def tui(app):
                    for line in commands:
                        buffer = app.layout.current_buffer
                        buffer.text = line
                        buffer.accept_handler(buffer)
                        await after_input(line)

                def new_client():
                    return httpx.AsyncClient(base_url='https://fixture.invalid', transport=httpx.MockTransport(
                        lambda _: httpx.Response(200, text='data: {"choices":[{"delta":{"content":"ok"},"finish_reason":"stop"}]}\n\ndata: [DONE]\n\n')))

                with (patch.object(AgentLoop, '_new_http_client', side_effect=new_client),
                      patch.object(cli.InteractiveController, '_run_queue', drain),
                      patch.object(console_title, '_open_console', return_value=('shell', writes.append)),
                      patch.object(PromptSession, 'prompt', side_effect=commands),
                      patch.object(PromptSession, 'prompt_async', side_effect=prompt),
                      patch.object(Application, 'run_async', tui), patch.object(Application, 'exit')):
                    agent = AgentLoop(AgentConfig(cwd=directory, provider=ProviderConfig(model='fixture')),
                                      event_sink=lambda _: None)
                    self.run_cli(root, agent, '--resume', *mode)
                self.assertEqual(writes, ['ThinkFlow - Saved request', 'ThinkFlow', 'ThinkFlow - Old request',
                                          'ThinkFlow', 'ThinkFlow - Fresh request', 'shell'])

    def test_single_run_never_touches_console(self):
        with (tempfile.TemporaryDirectory() as directory, patch.object(cli, 'single_run', return_value=0),
              patch.object(console_title, '_open_console') as capture):
            agent = Mock(close=unittest.mock.AsyncMock())
            root = Path(directory)
            self.run_cli(root, agent, '--prompt', 'one shot')
            prompt = root / 'prompt.txt'
            prompt.write_text('one shot', encoding='utf-8')
            self.run_cli(root, agent, '--prompt-file', str(prompt))
            capture.assert_not_called()
