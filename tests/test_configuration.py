"""User defaults, project overrides, CLI edits and the public runtime agree."""
import contextlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.configuration import (Configuration, load_configuration, merge_settings,
                               read_config, redact_config, write_config)


class ConfigurationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='thinkflow-configuration-')
        self.root = Path(self.temp.name).resolve()
        self.home = self.root / 'home'
        self.project = self.root / 'project'
        self.project.mkdir()
        self.environment = patch.dict(os.environ, {'THINKFLOW_HOME': str(self.home)})
        self.environment.start()

    def tearDown(self):
        self.environment.stop()
        self.temp.cleanup()
        self.assertFalse(self.root.exists())

    def global_settings(self):
        value = {'active_provider': 'local', 'providers': {'local': {
            'provider': 'openai', 'base_url': 'https://example.test', 'api_key': 'fixture-secret', 'model': 'first'}},
            'context': {'enabled': False}, 'compaction': {'enabled': True, 'max_chars': 150000},
            'native_tools': ['read', 'python']}
        write_config(self.home / 'config.json', value)
        return value

    def test_project_inherits_provider_and_replaces_only_requested_settings(self):
        self.global_settings()
        write_config(self.project / '.thinkflow/config.json', {
            'model': 'project-model', 'compaction': {'enabled': False},
            'native_tools': [], 'max_auto_continues': 0, 'max_run_turns': None})
        config, sources = load_configuration(self.project).resolve(use_env=False)
        self.assertEqual(config['model'], 'project-model')
        self.assertEqual(config['api_key'], 'fixture-secret')
        self.assertEqual(config['compaction'], {'enabled': False, 'max_chars': 150000})
        self.assertEqual(config['native_tools'], [])
        self.assertEqual(config['max_auto_continues'], 0)
        self.assertIsNone(config['max_run_turns'])
        self.assertEqual(sources['model'], str(self.project / '.thinkflow/config.json'))
        self.assertEqual(sources['compaction.max_chars'], str(self.home / 'config.json'))
        self.assertNotIn('fixture-secret', json.dumps(redact_config(config)))

    def test_explicit_file_is_isolated_and_missing_or_broken_dedicated_file_is_an_error(self):
        self.global_settings()
        isolated = self.root / 'isolated.json'
        write_config(isolated, {'compaction': {'enabled': False}})
        values, _ = load_configuration(self.project, config_path=isolated).resolve(use_env=False)
        self.assertNotIn('providers', values)
        self.assertNotIn('api_key', values)
        with self.assertRaises(FileNotFoundError):
            load_configuration(self.project, config_path=self.root / 'missing.json')
        write_config(self.project / 'config.json', {'application': 'unrelated'})
        self.assertEqual(len(load_configuration(self.project).layers), 1)
        (self.project / '.thinkflow').mkdir()
        (self.project / '.thinkflow/config.json').write_bytes(b'{broken')
        with self.assertRaises(json.JSONDecodeError):
            load_configuration(self.project)

    def test_profile_selection_invocation_override_and_source_map(self):
        stack = Configuration(self.project, [('user', {
            'active_provider': 'a', 'model': 'flat',
            'providers': {'a': {'model': 'model-a'}, 'b': {'model': 'model-b'}},
            'compaction': {'max_chars': 1000}}), ('project', {'compaction': None})])
        values, sources = stack.resolve({'active_provider': 'b', 'model': 'once'}, use_env=False)
        self.assertEqual(values['model'], 'once')
        self.assertEqual(sources['model'], 'command line')
        self.assertNotIn('compaction.max_chars', sources)
        values, _ = stack.resolve({'active_provider': ''}, use_env=False)
        self.assertEqual(values['model'], 'flat')
        self.assertEqual(values['active_provider'], '')
        self.assertEqual(stack.raw()['active_provider'], 'a')
        values, _ = stack.resolve({'providers': {'a': {'model': 'overridden'}}}, use_env=False)
        self.assertEqual(values['model'], 'overridden')
        env_stack = Configuration(self.project, [('user', {'providers': {'a': {'model': 'A'}}})])
        with patch.dict(os.environ, {'THINKFLOW_ACTIVE_PROVIDER': 'a'}):
            _, origins = env_stack.resolve()
        self.assertEqual(origins['active_provider'], 'env:THINKFLOW_ACTIVE_PROVIDER')

    def test_cli_changes_are_sparse_and_unset_restores_inheritance(self):
        from src.config_cli import main
        self.global_settings()
        def command(*args):
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                self.assertEqual(main([*args, '--cwd', str(self.project)]), 0)
            return output.getvalue()
        command('set', 'compaction.max_chars', '9000', '--project')
        self.assertEqual(read_config(self.project / '.thinkflow/config.json'), {'compaction': {'max_chars': 9000}})
        data = json.loads(command('get', 'compaction.max_chars', '--sources'))
        self.assertEqual(data['value'], 9000)
        self.assertEqual(data['source'], str(self.project / '.thinkflow/config.json'))
        command('unset', 'compaction.max_chars', '--project')
        self.assertEqual(json.loads(command('get', 'compaction.max_chars')), 150000)
        self.assertNotIn('fixture-secret', command('show'))
        before = (self.home / 'config.json').read_bytes()
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            command('set', 'max_read_chars', 'invalid')
        self.assertEqual((self.home / 'config.json').read_bytes(), before)

    def test_public_runtime_has_no_cli_dependency_and_preview_matches_config(self):
        from src.runtime import build_agent_config, effective_config
        sparse = {'security': {'profile': 'read-only'}, 'compaction': {'max_chars': 5000},
                  'context': {'enabled': False}, 'enable_native_tools': False}
        runtime = build_agent_config(sparse, system_prompt='', cwd=str(self.project))
        view = effective_config(sparse, cwd=str(self.project))
        self.assertTrue(runtime.security.read_only)
        self.assertEqual(view['security']['bash_policy'], runtime.security.bash_policy)
        self.assertEqual(view['compaction']['max_chars'], runtime.compaction.max_chars)
        profile = build_agent_config({'active_provider': 'a', 'native_tools': [], 'thinking_mode': 'disabled',
            'providers': {'a': {'native_tools': ['read'], 'thinking_mode': 'enabled'}}}, system_prompt='')
        self.assertEqual(profile.provider.provider_profiles[0].native_tools, [])
        self.assertEqual(profile.provider.provider_profiles[0].thinking_mode, 'disabled')
        for context in ({'files': 7}, {'max_chars': 'invalid'}, {'enabled': 'false'}):
            with self.subTest(context=context), self.assertRaises((ValueError, TypeError)):
                build_agent_config({'context': context}, system_prompt='')
        completed = subprocess.run([sys.executable, '-B', '-c',
            "import sys; import src.runtime; assert 'src.cli' not in sys.modules; print('runtime independent')"],
            cwd=Path(__file__).resolve().parents[1], capture_output=True, text=True,
            env={**os.environ, 'PYTHONDONTWRITEBYTECODE': '1'}, timeout=20)
        self.assertEqual(completed.returncode, 0, completed.stderr)

    def test_public_cli_config_runs_without_model_or_credentials(self):
        env = {**os.environ, 'THINKFLOW_HOME': str(self.home), 'PYTHONDONTWRITEBYTECODE': '1', 'PYTHONUTF8': '1'}
        root = Path(__file__).resolve().parents[1]
        command = [sys.executable, '-B', '-m', 'src.cli', 'config', 'set',
                   'compaction.enabled', 'false', '--project', '--cwd', str(self.project)]
        result = subprocess.run(command, cwd=root, env=env, capture_output=True, text=True, timeout=20)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(read_config(self.project / '.thinkflow/config.json'), {'compaction': {'enabled': False}})
        self.assertFalse((self.home / 'sessions').exists())


if __name__ == '__main__':
    unittest.main()
