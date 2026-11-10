"""Optional engineering guidance with a runtime-owned execution protocol."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.cli import apply_provider_profile, create_agent, resolve_system_prompt
from src.prompts import BUILTIN_SYSTEM_PROMPT, NATIVE_PROTOCOL_PROMPT, STREAM_PROTOCOL_PROMPT
from src.provider import ProviderProfileConfig


class PromptLayerTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.config = {'base_url': 'https://fixture.invalid', 'model': 'deepseek-flash',
                       'context': {'enabled': False},
                       'security': {'profile': 'open'}}

    async def asyncTearDown(self):
        self.temp.cleanup()

    async def test_default_custom_and_disabled_guides_execute_the_same_protocol(self):
        for number, setting in enumerate(({}, {'system_prompt': 'CUSTOM_STYLE'},
                                           {'use_builtin_system_prompt': False})):
            config = {**self.config, **setting}
            guidance = resolve_system_prompt(config, cwd=str(self.root))
            agent = create_agent(config, guidance, str(self.root), event_sink=lambda _: None)
            requests = []
            filename = f'proof{number}.txt'
            reply = (f'<tf-write id="1" path="{filename}">42</tf-write>\n'
                     '<tf-python id="2">from pathlib import Path\n'
                     f'assert Path("{filename}").read_text() == "42"\nprint("CHECKED")</tf-python>')
            def receive(request):
                body = json.loads(request.content)
                requests.append(body)
                content = reply if len(requests) == 1 else 'Done.'
                frame = {'choices': [{'delta': {'content': content}, 'finish_reason': 'stop'}]}
                return httpx.Response(200, content=('data: ' + json.dumps(frame) + '\n\ndata: [DONE]\n\n').encode())
            await agent.client.aclose()
            agent.client = httpx.AsyncClient(base_url='https://fixture.invalid', transport=httpx.MockTransport(receive))
            try:
                await agent.run('Create the file and check it.')
                self.assertEqual(len(requests), 2)
                system = requests[0]['messages'][0]['content']
                self.assertEqual(system.count(STREAM_PROTOCOL_PROMPT), 1)
                self.assertEqual(BUILTIN_SYSTEM_PROMPT in system, number == 0)
                self.assertEqual('CUSTOM_STYLE' in system, number == 1)
                self.assertIn('CHECKED', requests[1]['messages'][-1]['content'])
                self.assertEqual((self.root / filename).read_text(), '42')
            finally:
                await agent.close()

    async def test_switching_native_mode_changes_protocol_without_rewriting_custom_guidance(self):
        agent = create_agent(self.config, 'CUSTOM_STYLE', str(self.root), event_sink=lambda _: None)
        try:
            for enabled, names, available in ((True, ['read'], True),
                                               (False, ['read'], False),
                                               (True, ['unregistered_tool'], False)):
                profile = ProviderProfileConfig(name='fixture', base_url='https://fixture.invalid',
                                                model='deepseek-flash', enable_native_tools=enabled,
                                                native_tools=names)
                apply_provider_profile(agent, profile, profile.model)
                for protocol in ('openai', 'anthropic'):
                    agent.config.provider.format = protocol
                    agent.config.provider.max_tokens = 8192
                    body = agent.build_request_body(agent.build_system_prompt())
                    system = body['system'] if protocol == 'anthropic' else body['messages'][0]['content']
                    self.assertEqual(system.count(STREAM_PROTOCOL_PROMPT), 1)
                    self.assertEqual(NATIVE_PROTOCOL_PROMPT in system, available)
                    self.assertEqual(bool(body.get('tools')), available)
                    self.assertNotIn('load_tools(names)', system)
                    self.assertNotIn('read_skill(name=', system)
                    self.assertEqual(system.count('CUSTOM_STYLE'), 1)
            self.assertEqual(agent.config.system_prompt, 'CUSTOM_STYLE')
        finally:
            await agent.close()

    def test_disabling_guidance_keeps_independent_workspace_context(self):
        (self.root / 'AGENTS.md').write_text('WORKSPACE_CONTRACT', encoding='utf8')
        with patch.dict(os.environ, THINKFLOW_HOME=str(self.root / 'home')):
            guidance = resolve_system_prompt({'disable_system_prompt': True}, cwd=str(self.root))
        self.assertIn('WORKSPACE_CONTRACT', guidance)
        self.assertNotIn(BUILTIN_SYSTEM_PROMPT, guidance)

    def test_cli_prints_the_actual_composed_protocol_with_guidance_disabled(self):
        config = self.root / 'config.json'
        config.write_text(json.dumps(self.config), encoding='utf8')
        result = subprocess.run([sys.executable, '-B', '-m', 'src.cli', '--config', str(config),
                                 '--no-system-prompt', '--print-system-prompt'],
                                cwd=Path(__file__).resolve().parents[1],
                                env={**os.environ, 'THINKFLOW_HOME': str(self.root / 'home')},
                                capture_output=True, text=True, encoding='utf8')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.count(STREAM_PROTOCOL_PROMPT), 1)
        self.assertNotIn(BUILTIN_SYSTEM_PROMPT, result.stdout)


if __name__ == '__main__':
    unittest.main()
