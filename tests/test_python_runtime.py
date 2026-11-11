"""Interpreter selection and opt-in checks against the actual desktop Python bundle."""
import asyncio
from contextlib import ExitStack
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import AsyncMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src.executor import Executor, ExecutionCancelled, ExecutionResult
from src.security import SecurityPolicy


class PythonSelection(unittest.IsolatedAsyncioTestCase):
    async def test_source_frozen_and_explicit_interpreters(self):
        executor = Executor()
        self.addCleanup(executor.close)
        frozen_executable = str(Path('bundle').resolve() / 'thinkflow-service.exe')
        bundled = Path(frozen_executable).parent / 'python'
        for frozen, configured, expected in (
            (False, '', ['python', '-X', 'utf8', '-']),
            (True, '', [str(bundled / 'python.exe'), '-X', 'utf8', str(bundled / 'thinkflow_stdin.py')]),
            (False, 'chosen-python', ['chosen-python', '-X', 'utf8', '-']),
            (True, 'chosen-python', ['chosen-python', '-X', 'utf8', '-']),
        ):
            with self.subTest(frozen=frozen, configured=configured), \
                    patch.object(sys, 'frozen', frozen, create=True), \
                    patch.object(sys, 'executable', frozen_executable), \
                    patch.dict(os.environ, {'THINKFLOW_PYTHON': configured}), \
                    patch.object(executor, '_run_process', new_callable=AsyncMock) as run:
                run.return_value = ExecutionResult(True)
                await executor.python('print(42)')
                run.assert_awaited_once_with(expected, shell=False, tool='python', stdin='print(42)')


RUNTIME = os.environ.get('THINKFLOW_TEST_PYTHON_RUNTIME')


@unittest.skipUnless(RUNTIME and os.name == 'nt', 'set THINKFLOW_TEST_PYTHON_RUNTIME to a built backend/python directory')
class BundledPython(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.runtime = Path(RUNTIME).resolve()
        self.host_python = sys.executable
        temporary = tempfile.TemporaryDirectory(prefix='workspace-', dir=self.runtime.parent.parent)
        self.addCleanup(temporary.cleanup)
        self.workspace = Path(temporary.name)
        self.executor = Executor(str(self.workspace), security=SecurityPolicy(
            allowed_roots=[str(self.workspace)], max_bash_output_chars=100))
        self.addCleanup(self.executor.close)
        stack = self.enterContext(ExitStack())
        stack.enter_context(patch.object(sys, 'frozen', True, create=True))
        stack.enter_context(patch.object(sys, 'executable', str(self.runtime.parent / 'thinkflow-service.exe')))
        clean_env = os.environ.copy()
        for name in ('THINKFLOW_PYTHON', 'PYTHONHOME', 'PYTHONPATH'):
            clean_env.pop(name, None)
        clean_env['PATH'] = os.pathsep.join([os.path.join(os.environ['SystemRoot'], 'System32'), os.environ['SystemRoot']])
        stack.enter_context(patch.dict(os.environ, clean_env, clear=True))

    async def test_stdlib_utf8_stdin_and_workspace_import(self):
        (self.workspace / 'local_module.py').write_text('value = "工作区"\n', encoding='utf8')
        result = await self.executor.python('''from __future__ import annotations
import csv, io, json, sqlite3, statistics, pathlib, ssl, hashlib, bz2, lzma, ctypes
import sys, os, __main__, local_module
assert statistics.mean([1, 2, 3]) == 2
assert list(csv.reader(io.StringIO("a,b\\n1,2")))[1] == ["1", "2"]
assert sqlite3.connect(":memory:").execute("select 42").fetchone()[0] == 42
assert lzma.decompress(lzma.compress(b"hello")) == b"hello"
assert bz2.decompress(bz2.compress(b"hello")) == b"hello"
assert sys.argv == ["-"] and __file__ == "<stdin>"
assert __main__.__dict__ is globals()
assert sys.flags.isolated and sys.flags.utf8_mode
assert pathlib.Path.cwd().name.startswith("workspace-")
pathlib.Path("中文.txt").write_text(local_module.value)
assert pathlib.Path("中文.txt").read_text() == "工作区"
pathlib.Path("child").mkdir()
pathlib.Path("child/after_chdir.py").write_text("value = 42")
os.chdir("child")
import after_chdir
assert after_chdir.value == 42
print("中文输入输出:" + local_module.value)
print(json.dumps({"executable": sys.executable, "pid": os.getpid()}))
''')
        self.assertTrue(result.success, result.error)
        output = self.executor.outputs.recall(result.output_refs['stdout'])
        self.assertIn('中文输入输出:工作区', output)
        details = json.loads(output.splitlines()[-1])
        self.assertEqual(Path(details['executable']), self.runtime / 'python.exe')
        self.assertNotEqual(details['pid'], os.getpid())

    async def test_explicit_override_uses_chosen_interpreter(self):
        with patch.dict(os.environ, {'THINKFLOW_PYTHON': self.host_python}):
            result = await self.executor.python('import sys; print(sys.executable); print("显式覆盖")')
        self.assertTrue(result.success, result.error)
        output = self.executor.outputs.recall(result.output_refs['stdout'])
        self.assertIn(self.host_python, output)
        self.assertIn('显式覆盖', output)

    async def test_nonzero_output_and_traceback(self):
        result = await self.executor.python('import sys\nprint("x" * 500 + "输出尾部")\nprint("错误输出", file=sys.stderr)\nsys.exit(7)')
        self.assertFalse(result.success)
        self.assertEqual(result.exit_code, 7)
        self.assertTrue(result.truncated)
        self.assertTrue(result.stdout.rstrip().endswith('输出尾部'))
        self.assertEqual(result.stderr.strip(), '错误输出')
        self.assertEqual(len(self.executor.outputs.path_for(result.output_refs['stdout']).read_text(encoding='utf8').strip()), 504)
        traceback = await self.executor.python('raise ValueError("中文异常")')
        self.assertEqual(traceback.exit_code, 1)
        self.assertIn('中文异常', traceback.stderr)
        self.assertIn('<stdin>', self.executor.outputs.recall(traceback.output_refs['stderr']))

    async def test_cancel_stops_process_and_retains_output(self):
        task = asyncio.create_task(self.executor.python('''import pathlib, time, sys
print("取消前输出", flush=True)
pathlib.Path("ready").touch()
time.sleep(0.5)
pathlib.Path("late").touch()
time.sleep(30)
'''))
        try:
            async with asyncio.timeout(5):
                while not (self.workspace / 'ready').exists():
                    await asyncio.sleep(.01)
            task.cancel()
            with self.assertRaises(ExecutionCancelled) as raised:
                await task
            self.assertEqual(raised.exception.result.status_str, 'cancelled')
            self.assertIn('取消前输出', raised.exception.result.stdout)
            await asyncio.sleep(.6)
            self.assertFalse((self.workspace / 'late').exists())
        finally:
            if not task.done():
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)


if __name__ == '__main__':
    unittest.main()
