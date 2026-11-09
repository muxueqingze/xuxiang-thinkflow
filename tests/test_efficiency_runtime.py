"""Execution/feedback regressions: actual files and shell, no model API calls."""
import asyncio
import copy
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

import httpx

from src.agent_loop import AgentConfig, AgentLoop
from src.context import ContextManager
from src.compaction import CompactionConfig
from src.executor import ExecutionResult
from src.feedback import project_read_results, with_observation
from src.parser import Command
from src.provider import ProviderConfig
from src.security import SecurityPolicy


class FeedbackTests(unittest.TestCase):
    def test_pending_information_batch_survives_snapshot(self):
        ctx = ContextManager()
        for number, body in (('1', 'FULL_FIRST_RESULT'), ('2', 'FULL_SECOND_RESULT')):
            ctx.record(Command(number, 'read', path=number),
                       ExecutionResult(True, tool='read', content=body), flow='blocking')
        restored = ContextManager.from_dict(ctx.to_dict())
        message = restored.build_auto_result_message()
        self.assertEqual(message.count('FULL_FIRST_RESULT'), 1)
        self.assertEqual(message.count('FULL_SECOND_RESULT'), 1)
        self.assertEqual([r.id for r in restored.pending_auto_results], ['1', '2'])
        for record in restored.pending_auto_results:
            restored.mark_injected(record.id)
        self.assertIsNone(restored.build_auto_result_message())

    def test_blocking_output_is_complete_and_present_once(self):
        output = 'UNIQUE-BEGIN\n' + 'x' * 11000 + '\nUNIQUE-END'
        for need in (False, True):
            ctx = ContextManager()
            ctx.record(Command('1', 'bash', cmd='test', need_result=need),
                       ExecutionResult(True, tool='bash', stdout=output, stderr='diagnostic', exit_code=0),
                       flow='blocking')
            rendered = ctx.build_need_result_message() if need else ctx.build_auto_result_message()
            self.assertEqual(rendered.count('UNIQUE-BEGIN'), 1)
            self.assertEqual(rendered.count('UNIQUE-END'), 1)
            self.assertEqual(rendered.count('diagnostic'), 1)
            self.assertIn(output, rendered)
            self.assertIn('exit_code="0"', rendered)

    def test_only_exact_success_payload_in_reasoning_is_deduplicated(self):
        for status, reasoning, omitted in ((True, 'unique-body', True), (True, '', False),
                                           (True, 'unique', False), (False, 'unique-body', False)):
            ctx = ContextManager()
            ctx.record(Command('1', 'write', path='x.py', content='unique-body'),
                       ExecutionResult(status, tool='write', error='' if status else 'failed'))
            body = ctx.build_injection(retained_text=reasoning)
            self.assertEqual('unique-body' not in body, omitted)
            self.assertIn('receipt:1', body)
            self.assertEqual(ctx.records[0].content, 'unique-body')

    def test_edit_requires_both_payloads_and_snapshot_keeps_pending_body(self):
        ctx = ContextManager()
        ctx.record(Command('1', 'edit', path='x', old_text='old-value', new_text='new-value'),
                   ExecutionResult(True, tool='edit'))
        restored = ContextManager.from_dict(ctx.to_dict())
        self.assertIn('<old>old-value</old>', ctx.build_injection(retained_text='old-value'))
        self.assertIn('<new>new-value</new>', restored.build_injection())

    def test_long_write_is_deduplicated_before_receipt_compaction(self):
        payload = 'START\n' + 'long payload\n' * 1000 + '\nEND'
        ctx = ContextManager()
        ctx.record(Command('1', 'write', path='large.py', content=payload),
                   ExecutionResult(True, tool='write', bytes_written=len(payload)))
        restored = ContextManager.from_dict(ctx.to_dict())
        self.assertIn(payload, restored.build_injection())
        feedback = ctx.build_injection(retained_text=payload)
        self.assertNotIn('long payload', feedback)
        self.assertIn('receipt:1', feedback)
        ctx.compact_history()
        self.assertLess(len(ctx.records[0].content), len(payload))

    def test_failure_and_pending_snapshot_keep_middle_diagnostic_once(self):
        output = 'begin\n' + 'x' * 5500 + '\nACTUAL-CAUSE\n' + 'y' * 5500 + '\nend'
        for success in (False, True):
            ctx = ContextManager()
            ctx.record(Command('1', 'bash', cmd='python check.py'),
                       ExecutionResult(success, tool='bash', stderr=output,
                                       error='' if success else output, exit_code=0 if success else 1),
                       flow='blocking')
            restored = ContextManager.from_dict(ctx.to_dict())
            feedback = restored.build_injection() if success else restored.build_failure_message()
            self.assertIn(output, feedback)
            self.assertEqual(feedback.count('ACTUAL-CAUSE'), 1)
            self.assertEqual(feedback.count('begin'), 1)

    def test_observation_preserves_provider_structure_and_history(self):
        for messages in (
            [{'role': 'assistant', 'reasoning_content': 'keep', 'tool_calls': [{'id': 'x'}]},
             {'role': 'tool', 'tool_call_id': 'x', 'content': 'output'}],
            [{'role': 'user', 'content': [{'type': 'tool_result', 'tool_use_id': 'x',
                                          'is_error': True, 'content': 'failure'}]}],
        ):
            old = copy.deepcopy(messages)
            updated = with_observation(messages, 'runtime')
            self.assertEqual(len(updated), len(messages))
            self.assertEqual(messages, old)
            self.assertIn('runtime', json.dumps(updated[-1]))
        self.assertEqual(with_observation([], 'runtime'), [{'role': 'user', 'content': 'runtime'}])

    def test_read_projection_keeps_originals_suffixes_and_materializes_after_compaction(self):
        payload = '完整正文\n' * 2000
        header = '[THINKFLOW RECEIPT id="read-1" tool="read" status="success"]\n'
        suffix = '\n\n[THINKFLOW COMMAND LEDGER]receipt metadata'
        source = {'role': 'assistant', 'tool_calls': [{'id': 'write-1', 'function': {
            'name': 'write', 'arguments': json.dumps({'path': 'large.py', 'content': payload})}}]}
        observation = {'role': 'tool', 'tool_call_id': 'read-1', 'content': header + payload + suffix}
        messages = [source, observation]
        original = copy.deepcopy(messages)
        projected = project_read_results(messages, {'read-1': len(payload)})
        self.assertEqual(messages, original)
        self.assertEqual(projected[0], source)
        self.assertIn('write-1', projected[1]['content'])
        self.assertTrue(projected[1]['content'].endswith(suffix))
        self.assertNotIn(payload, projected[1]['content'])
        restored = project_read_results([observation], {'read-1': len(payload)})
        self.assertEqual(restored, [observation])


class SlowSSE(httpx.AsyncByteStream):
    def __init__(self, frames):
        self.frames = frames

    async def __aiter__(self):
        for frame in self.frames:
            yield ('data: ' + json.dumps({'choices': [frame]}) + '\n\n').encode()
            await asyncio.sleep(.025)
        yield b'data: [DONE]\n\n'


class RuntimeEfficiencyTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        asyncio.get_running_loop().slow_callback_duration = 2
        self.directory = tempfile.TemporaryDirectory()
        self.root = Path(self.directory.name)
        self.events = []
        self.agent = AgentLoop(AgentConfig(cwd=str(self.root),
            provider=ProviderConfig(base_url='https://fixture.invalid', model='deepseek-flash'),
            security=SecurityPolicy(allowed_roots=[str(self.root)], bash_policy='unrestricted', approval_mode='approve_all')),
            event_sink=self.events.append)

    async def asyncTearDown(self):
        await self.agent.close()
        self.directory.cleanup()

    async def test_same_stream_writes_then_real_shell_check(self):
        requests = []
        reasoning = ('<tf-write id="1" path="app.py">ANSWER = 42\n</tf-write>\n'
                     '<tf-write id="2" path="check.py">from app import ANSWER\nassert ANSWER == 42\nprint("passed")\n</tf-write>')
        def receive(request):
            body = json.loads(request.content)
            requests.append(body)
            if len(requests) == 1:
                return httpx.Response(200, stream=SlowSSE([
                    {'delta': {'reasoning_content': reasoning}},
                    {'delta': {'tool_calls': [{'index': 0, 'id': 'check', 'function': {
                        'name': 'bash', 'arguments': json.dumps({'cmd': 'python check.py'})}}]}},
                    {'delta': {}, 'finish_reason': 'tool_calls'},
                ]))
            return httpx.Response(200, stream=SlowSSE([{'delta': {'content': 'Done, check passed.'}, 'finish_reason': 'stop'}]))
        await self.agent.client.aclose()
        self.agent.client = httpx.AsyncClient(base_url='https://fixture.invalid', transport=httpx.MockTransport(receive))
        await self.agent.run('Implement and verify')
        self.assertEqual(len(requests), 2)
        self.assertEqual(self.agent.stopped_reason, 'completed')
        self.assertEqual([r.status for r in self.agent.context.records], ['success'] * 3)
        self.assertEqual(requests[1]['messages'][-1]['role'], 'tool')
        self.assertIn('passed', requests[1]['messages'][-1]['content'])
        self.assertIn('receipt:1', requests[1]['messages'][-1]['content'])
        self.assertNotIn('ANSWER = 42', requests[1]['messages'][-1]['content'])
        self.assertEqual(requests[1]['messages'][-2]['reasoning_content'], reasoning)
        self.assertNotIn('max_tokens', requests[1])
        finish = next(i for i, e in enumerate(self.events) if e['type'] == 'stream_finished')
        writes = [i for i, e in enumerate(self.events) if e['type'] == 'tool_completed' and e.get('tool') == 'write']
        self.assertTrue(all(i < finish for i in writes))

    async def test_plan_evidence_survives_receipt_retention(self):
        self.agent.context.record(Command('v1', 'bash', cmd='python check.py'),
                                  ExecutionResult(True, tool='bash', exit_code=0))
        self.agent.context.mark_injected('v1')
        for i in range(4):
            self.agent.context.record(Command(str(i), 'read', path='app.py'), ExecutionResult(True, tool='read'))
            self.agent.context.mark_injected(str(i))
        self.agent.context.compact_history(keep_records=1, protected_ids={'v1'})
        self.assertEqual([r.id for r in self.agent.context.records], ['v1', '3'])

    async def test_reasoning_ignored_without_native_tools_does_not_remove_payload(self):
        self.agent.config.provider.enable_native_tools = False
        self.agent.messages = [{'role': 'assistant', 'content': '', 'reasoning_content': 'FULL-CODE'}]
        self.agent.context.record(Command('1', 'write', path='app.py', content='FULL-CODE'),
                                  ExecutionResult(True, tool='write'))
        self.agent._append_pending_injection()
        self.assertIn('FULL-CODE', self.agent.messages[-1]['content'])

    async def test_compaction_then_resume_preserves_the_only_pending_payload(self):
        payload = 'UNIQUE_CODE_FULL_BODY'
        self.agent.config.compaction = CompactionConfig(max_messages=1, keep_recent_messages=1)
        self.agent.messages = [{'role': 'assistant', 'content': '', 'reasoning_content': payload}]
        self.agent.context.record(Command('1', 'write', path='app.py', content=payload),
                                  ExecutionResult(True, tool='write'))
        requests = []
        def receive(request):
            requests.append(json.loads(request.content))
            return httpx.Response(200, stream=SlowSSE([
                {'delta': {'content': 'Done.'}, 'finish_reason': 'stop'}]))
        await self.agent.client.aclose()
        self.agent.client = httpx.AsyncClient(base_url='https://fixture.invalid', transport=httpx.MockTransport(receive))
        await self.agent.run('continue')
        self.assertIn(payload, requests[0]['messages'][-1]['content'])
        self.assertIn('receipt:1', requests[0]['messages'][-1]['content'])

    async def test_shell_preserves_python_utf8_environment(self):
        (self.root / 'unicode_check.py').write_text("print('Zo\\u00eb \\u4e2d\\u6587')\n")
        with patch.dict(os.environ, PYTHONUTF8='1', PYTHONIOENCODING='utf-8', PYTHONDONTWRITEBYTECODE='1'):
            result = await self.agent._execute_traditional_result('bash',
                {'cmd': f'"{sys.executable}" -B unicode_check.py'}, tool_id='unicode')
        self.assertTrue(result.success, result.stderr)
        self.assertEqual(result.stdout.strip(), 'Zoë 中文')

    async def test_python_batches_files_and_checks_without_shell_quoting(self):
        code = ("from pathlib import Path\n"
                "Path('a.py').write_text('ANSWER = 42\\n', encoding='utf8')\n"
                "Path('b.py').write_text('from a import ANSWER\\n', encoding='utf8')\n"
                "from b import ANSWER\nassert ANSWER == 42\n"
                "print('quote: \\\" and apostrophe: \\' and \\u4e2d\\u6587')\n")
        # A real code block larger than the Windows command-line limit goes via stdin.
        code += '# payload\n' * 4000
        with patch.dict(os.environ, PYTHONIOENCODING='utf-8', PYTHONDONTWRITEBYTECODE='1'):
            result = await self.agent._execute_traditional_result('python', {'code': code}, tool_id='code')
        self.assertTrue(result.success, result.stderr)
        self.assertEqual(result.exit_code, 0)
        self.assertIn('中文', result.stdout)
        self.assertEqual(sorted(p.name for p in self.root.iterdir()), ['a.py', 'b.py'])
        self.assertIn('exit_code: 0', self.agent._native_result_text(result))

    async def test_python_failure_returns_traceback_and_respects_execution_policy(self):
        invalid = await self.agent._execute_traditional_result('python', {'code': 123}, tool_id='invalid')
        self.assertFalse(invalid.success)
        self.assertIn('code must be a string', invalid.error)
        self.assertEqual(self.agent.context.records[-1].status, 'failed')
        failure = await self.agent._execute_traditional_result('python',
            {'code': "raise ValueError('ACTUAL_FAILURE')"}, tool_id='failure')
        self.assertFalse(failure.success)
        self.assertNotEqual(failure.exit_code, 0)
        self.assertEqual(self.agent._native_result_text(failure).count('ACTUAL_FAILURE'), 1)
        self.agent.config.security.read_only = True
        denied = await self.agent._execute_traditional_result('python',
            {'code': "from pathlib import Path; Path('should-not-exist').touch()"}, tool_id='denied')
        self.assertFalse(denied.success)
        self.assertFalse((self.root / 'should-not-exist').exists())

    async def test_valid_write_before_parse_error_survives_coalesced_chunk(self):
        prefix = '<tf-write id="1" path="marker.txt">EXECUTED</tf-write>'
        invalid = '<tf-write id="2">bad</tf-write>'
        async def stream():
            from src.streaming import StreamEvent, EventType
            yield StreamEvent(type=EventType.THINKING_DELTA, thinking_text=prefix + invalid)
        async def send(*args):
            return httpx.Response(200)
        self.agent._send_stream_request = send
        self.agent._events_until_failure = lambda *args: stream()
        await self.agent._run_one_turn()
        self.assertEqual((self.root / 'marker.txt').read_text(), 'EXECUTED')
        self.assertEqual([(r.id, r.status) for r in self.agent.context.records], [('1', 'success')])

    async def test_dsml_tail_recovers_without_replaying_successful_stream_write(self):
        tail = '</｜｜DSML｜｜ calls>'
        requests = []
        def receive(request):
            requests.append(json.loads(request.content))
            if len(requests) == 1:
                return httpx.Response(200, stream=SlowSSE([{'delta': {'content':
                    '<tf-write id="1" path="a.txt">first</tf-write>\n' + tail}, 'finish_reason': 'stop'}]))
            if len(requests) == 2:
                return httpx.Response(200, stream=SlowSSE([{'delta': {'tool_calls': [{'index': 0,
                    'id': 'finish-file', 'function': {'name': 'write', 'arguments': json.dumps({
                        'path': 'b.txt', 'content': 'second'})}}]}, 'finish_reason': 'tool_calls'}]))
            return httpx.Response(200, stream=SlowSSE([{'delta': {'content': 'Done.'}, 'finish_reason': 'stop'}]))
        await self.agent.client.aclose()
        self.agent.client = httpx.AsyncClient(base_url='https://fixture.invalid', transport=httpx.MockTransport(receive))
        await self.agent.run('Create both files')
        self.assertEqual(len(requests), 3)
        self.assertIn('DSML', requests[1]['messages'][-1]['content'])
        self.assertEqual([(r.id, r.status) for r in self.agent.context.records], [('1', 'success'), ('finish-file', 'success')])
        self.assertEqual((self.root / 'b.txt').read_text(), 'second')

    async def test_empty_provider_stop_after_read_recovers_instead_of_finishing(self):
        self.agent.config.provider.enable_native_tools = False
        (self.root / 'a.txt').write_text('before')
        requests = []
        replies = [
            {'content': '<tf-read id="1" path="a.txt" />'},
            {'content': '', 'reasoning_content': 'Need to edit the existing file.'},
            {'content': '<tf-edit id="2" path="a.txt"><old>before</old><new>after</new></tf-edit>Updated.'},
        ]
        def receive(request):
            requests.append(json.loads(request.content))
            return httpx.Response(200, stream=SlowSSE([
                {'delta': replies[len(requests) - 1], 'finish_reason': 'stop'}]))
        await self.agent.client.aclose()
        self.agent.client = httpx.AsyncClient(base_url='https://fixture.invalid', transport=httpx.MockTransport(receive))
        await self.agent.run('Read and update the file')
        self.assertEqual(len(requests), 3)
        self.assertEqual((self.root / 'a.txt').read_text(), 'after')
        self.assertEqual([(r.id, r.status) for r in self.agent.context.records], [('1', 'success'), ('2', 'success')])
        self.assertEqual(self.agent.usage.turns[1].abort_reason, 'tool_failed')
        self.assertIn('本轮没有执行操作', requests[2]['messages'][-1]['content'])
        self.assertEqual([e['kind'] for e in self.events if e['type'] == 'provider_protocol_error'],
                         ['empty_assistant_response'])

    async def test_dsml_inside_fence_or_file_is_literal_content(self):
        tail = '</｜DSML｜ parameter>\n</｜DSML｜ invoke>\n</｜DSML｜ calls>'
        reply = '<tf-write id="1" path="protocol.txt">' + tail + '</tf-write>\n```xml\n' + tail + '\n```'
        requests = []
        def receive(request):
            requests.append(request)
            return httpx.Response(200, stream=SlowSSE([{'delta': {'content': reply}, 'finish_reason': 'stop'}]))
        await self.agent.client.aclose()
        self.agent.client = httpx.AsyncClient(base_url='https://fixture.invalid', transport=httpx.MockTransport(receive))
        await self.agent.run('Write a protocol example')
        self.assertEqual(len(requests), 1)
        self.assertEqual((self.root / 'protocol.txt').read_text(), tail)
        self.assertFalse(any(e['type'] == 'provider_protocol_error' for e in self.events))

    async def test_optional_tools_load_once_execute_and_survive_snapshot(self):
        names = lambda: {t['function']['name'] for t in self.agent.build_request_body('')['tools']}
        self.assertNotIn('grep', names())
        self.assertTrue({'read', 'write', 'edit', 'bash', 'python', 'read_skill', 'load_tools'} <= names())
        result = await self.agent._execute_traditional_result('load_tools', {'names': ['grep', 'glob']}, tool_id='load')
        self.assertTrue(result.success)
        (self.root / 'a.py').write_text('ANSWER = 42\n')
        found = await self.agent._execute_traditional_result('grep', {'pattern': 'ANSWER'}, tool_id='find')
        self.assertIn('ANSWER = 42', found.content)
        snapshot = self.agent.to_snapshot()
        self.agent.additional_native_tools.clear()
        self.agent.load_snapshot(snapshot)
        self.assertTrue({'grep', 'glob'} <= names())
        self.agent.config.provider.native_tools = ['read']
        denied = await self.agent._execute_traditional_result('load_tools', {'names': ['grep']}, tool_id='not-enabled')
        self.assertFalse(denied.success)
        self.assertEqual(names(), {'read'})

    async def test_streamed_writes_then_python_check_share_one_generation(self):
        self.agent.config.provider.enable_native_tools = False
        code = 'from pathlib import Path\nassert Path("a.txt").read_text() == "first"\nprint("checked")\n'
        reply = ('<tf-write id="1" path="a.txt">first</tf-write>\n'
                 '<tf-write id="2" path="b.txt">second</tf-write>\n'
                 '<tf-python id="3">' + code + '</tf-python>\n')
        requests = []
        def receive(request):
            requests.append(json.loads(request.content))
            text = reply if len(requests) == 1 else 'Files created and checked.'
            return httpx.Response(200, stream=SlowSSE([{'delta': {'content': text}, 'finish_reason': 'stop'}]))
        await self.agent.client.aclose()
        self.agent.client = httpx.AsyncClient(base_url='https://fixture.invalid', transport=httpx.MockTransport(receive))
        await self.agent.run('Create both files and check their content')
        self.assertEqual(len(requests), 2)
        self.assertTrue(all('tools' not in request for request in requests))
        self.assertEqual([(r.tool, r.status) for r in self.agent.context.records],
                         [('write', 'success'), ('write', 'success'), ('python', 'success')])
        self.assertIn('checked', requests[1]['messages'][-1]['content'])
        self.assertIn(code.rstrip(), requests[1]['messages'][-1]['content'])
        self.assertEqual((self.root / 'b.txt').read_text(), 'second')

    async def test_two_reads_are_returned_together_before_edits(self):
        self.agent.config.provider.enable_native_tools = False
        (self.root / 'a.txt').write_text('alpha')
        (self.root / 'b.txt').write_text('beta')
        requests = []
        def receive(request):
            requests.append(json.loads(request.content))
            if len(requests) == 1:
                reply = '<tf-read id="1" path="a.txt" /><tf-read id="2" path="b.txt" />'
            else:
                feedback = requests[1]['messages'][-1]['content']
                self.assertEqual(feedback.count('alpha'), 1)
                self.assertEqual(feedback.count('beta'), 1)
                reply = ('<tf-edit id="3" path="a.txt"><old>alpha</old><new>ALPHA</new></tf-edit>'
                         '<tf-edit id="4" path="b.txt"><old>beta</old><new>BETA</new></tf-edit>')
            return httpx.Response(200, stream=SlowSSE([{'delta': {'content': reply}, 'finish_reason': 'stop'}]))
        await self.agent.client.aclose()
        self.agent.client = httpx.AsyncClient(base_url='https://fixture.invalid', transport=httpx.MockTransport(receive))
        await self.agent.run('Read both files and capitalize their contents')
        self.assertEqual(len(requests), 2)
        self.assertEqual([(r.tool, r.status) for r in self.agent.context.records],
                         [('read', 'success'), ('read', 'success'), ('edit', 'success'), ('edit', 'success')])
        self.assertTrue(all(r.injected for r in self.agent.context.records[:2]))
        self.assertEqual((self.root / 'a.txt').read_text(), 'ALPHA')
        self.assertEqual((self.root / 'b.txt').read_text(), 'BETA')


if __name__ == '__main__':
    unittest.main()
