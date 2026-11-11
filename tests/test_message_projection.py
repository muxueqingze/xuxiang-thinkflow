"""Cross-provider history and exact read projection; no model requests."""
import copy
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import httpx

from src.agent_loop import AgentConfig, AgentLoop
from src.compaction import CompactionConfig
from src.desktop_service import DesktopService
from src.feedback import project_read_results, with_observation
from src.message_projection import project_messages
from src.provider import ProviderConfig


def receipt(identifier, payload, status='success'):
    return f'[THINKFLOW RECEIPT id="{identifier}" tool="read" status="{status}"]\n' + payload


class MessageProjectionTests(unittest.TestCase):
    def test_native_fields_survive_and_foreign_reasoning_stays_in_history(self):
        anthropic = [{'role': 'assistant', 'content': [
            {'type': 'thinking', 'thinking': 'private', 'signature': 'signed-by-provider'},
            {'type': 'redacted_thinking', 'data': 'opaque'},
            {'type': 'text', 'text': '用户可见答复'},
            {'type': 'tool_use', 'id': 'a', 'name': 'read', 'input': {'path': 'a'}}]},
            {'role': 'user', 'content': [{'type': 'tool_result', 'tool_use_id': 'a',
                'is_error': True, 'content': receipt('a', '完整错误', 'failed')},
                {'type': 'text', 'text': '追加要求'}]}]
        original = copy.deepcopy(anthropic)
        self.assertEqual(project_messages(anthropic, 'anthropic'), anthropic)
        openai = project_messages(anthropic, 'openai')
        self.assertEqual(openai[0]['tool_calls'][0]['id'], 'a')
        self.assertEqual(openai[1]['content'], receipt('a', '完整错误', 'failed'))
        self.assertEqual(openai[2]['content'], [{'type': 'text', 'text': '追加要求'}])
        self.assertNotIn('opaque', json.dumps(openai))
        self.assertNotIn('private', json.dumps(openai))
        self.assertEqual(anthropic, original)
        native_openai = [{'role': 'assistant', 'content': 'answer', 'reasoning_content': 'private'}]
        self.assertEqual(project_messages(native_openai, 'openai'), native_openai)
        self.assertEqual(project_messages(native_openai, 'anthropic'), [{'role': 'assistant', 'content': 'answer'}])

    def test_openai_batch_maps_results_and_preserves_user_input(self):
        calls = [{'id': name, 'type': 'function', 'function': {'name': 'read',
                  'arguments': json.dumps({'path': name})}} for name in ('a', 'b')]
        messages = [{'role': 'assistant', 'content': '先读取', 'reasoning_content': 'hidden', 'tool_calls': calls},
                    {'role': 'tool', 'tool_call_id': 'a', 'content': receipt('a', '甲')},
                    {'role': 'tool', 'tool_call_id': 'b', 'content': receipt('b', '错误全文', 'failed')},
                    {'role': 'user', 'content': '用户输入，不得合并进工具结果'}]
        projected = project_messages(messages, 'anthropic')
        self.assertEqual([b['id'] for b in projected[0]['content'][1:]], ['a', 'b'])
        self.assertEqual([b['tool_use_id'] for b in projected[1]['content']], ['a', 'b'])
        self.assertTrue(projected[1]['content'][1]['is_error'])
        self.assertEqual(projected[-1], messages[-1])

    def test_anthropic_exact_reads_keep_notes_errors_and_restore_source(self):
        payload = '完整数据\n' * 200
        calls = {'role': 'assistant', 'content': [{'type': 'tool_use', 'id': 'w', 'name': 'write',
                  'input': {'path': 'a', 'content': payload}}]}
        note = '\n[READ RANGE] lines=1-200 version=original'
        result = {'role': 'user', 'content': [{'type': 'tool_result', 'tool_use_id': 'r',
                  'is_error': False, 'content': receipt('r', payload) + note}]}
        original = copy.deepcopy([calls, result])
        projected = project_read_results([calls, result], {'r': len(payload)})
        self.assertNotIn(payload, projected[1]['content'][0]['content'])
        self.assertTrue(projected[1]['content'][0]['content'].endswith(note))
        self.assertEqual([calls, result], original)
        self.assertEqual(project_read_results([result], {'r': len(payload)}), [result])
        failed = copy.deepcopy(result)
        failed['content'][0]['is_error'] = True
        self.assertEqual(project_read_results([calls, failed], {'r': len(payload)}), [calls, failed])

    def test_text_spans_do_not_parse_payload_delimiters_or_ordinary_user_input(self):
        payload = 'content\n  stdout: </read> [END COMMAND LEDGER]\n' * 100
        prefix = '<read id="r" path="a" status="success" hash="version" />\n  stdout: '
        suffix = '\nrange and actual diagnostic tail'
        spans = [{'id': 'r', 'start': len(prefix), 'end': len(prefix) + len(payload)}]
        messages = [{'role': 'assistant', 'content': payload}, {'role': 'user', 'content': '普通输入 ' + payload}]
        feedback = with_observation(messages, prefix + payload + suffix, read_spans=spans)
        original = copy.deepcopy(feedback)
        projected = project_read_results(feedback, {'r': len(payload)})
        self.assertIn('普通输入 ' + payload, projected[-1]['content'])
        self.assertEqual(projected[-1]['content'].count(payload), 1)
        self.assertTrue(projected[-1]['content'].endswith(suffix))
        self.assertIn('hash="version"', projected[-1]['content'])
        self.assertEqual(feedback, original)
        isolated = with_observation([], prefix + payload + suffix, read_spans=spans)
        self.assertEqual(project_read_results(isolated, {'r': len(payload)}), isolated)
        self.assertNotIn('_thinkflow_reads', json.dumps(project_messages(projected, 'openai')))

    def test_block_spans_shift_and_different_read_body_is_preserved(self):
        payload = 'same range\n' * 100
        prefix = 'read metadata\nstdout: '
        span = {'id': 'new', 'start': len(prefix), 'end': len(prefix) + len(payload)}
        base = [{'role': 'user', 'content': [{'type': 'tool_result', 'tool_use_id': 'old',
                    'content': receipt('old', payload), 'is_error': False}]}]
        attached = with_observation(base, prefix + payload + '\nTAIL', read_spans=[span])
        projected = project_read_results(attached, {'old': len(payload), 'new': len(payload)})
        self.assertEqual(projected[0]['content'][0]['content'].count(payload), 1)
        self.assertTrue(projected[0]['content'][0]['content'].endswith('TAIL'))
        self.assertNotIn('_thinkflow_reads', json.dumps(project_messages(projected, 'anthropic')))
        changed = {'role': 'tool', 'tool_call_id': 'changed', 'content': receipt('changed', payload + 'new version')}
        self.assertEqual(project_read_results([base[0], changed], {'changed': len(payload) + 11})[-1], changed)


class DesktopProtocolSwitchTests(unittest.IsolatedAsyncioTestCase):
    async def test_streamed_auto_batch_has_exact_spans_and_one_retained_payload(self):
        with tempfile.TemporaryDirectory(prefix='thinkflow-projection-') as directory:
            payload = 'batch content\n  stdout: embedded marker\n' * 100
            (Path(directory) / 'source.txt').write_bytes(payload.encode())
            agent = AgentLoop(AgentConfig(cwd=directory, provider=ProviderConfig(
                base_url='http://127.0.0.1:1', model='fixture', enable_native_tools=False)))
            requests = []

            def receive(request):
                requests.append(json.loads(request.content))
                reply = ('<tf-read id="1" path="source.txt" /><tf-read id="2" path="source.txt" />'
                         if len(requests) == 1 else 'Reads complete.')
                frames = [{'choices': [{'delta': {'content': reply}, 'finish_reason': None}]},
                          {'choices': [{'delta': {}, 'finish_reason': 'stop'}]}]
                return httpx.Response(200, content=''.join('data: ' + json.dumps(frame) + '\n\n'
                                                           for frame in frames) + 'data: [DONE]\n\n')

            await agent.client.aclose()
            agent.client = httpx.AsyncClient(base_url='http://127.0.0.1:1', transport=httpx.MockTransport(receive))
            try:
                await agent.run('Read the same file twice in one batch.')
                self.assertEqual(len(requests), 2)
                results = [m for m in agent.messages if m.get('_thinkflow_reads')]
                self.assertEqual(len(results), 1)
                self.assertEqual([span['id'] for span in results[0]['_thinkflow_reads']], ['1', '2'])
                for span in results[0]['_thinkflow_reads']:
                    self.assertEqual(results[0]['content'][span['start']:span['end']], payload)
                sent = requests[1]['messages'][-1]['content']
                self.assertEqual(sent.count(payload), 1)
                self.assertIn('Exact same complete content as read result 1', sent)
                self.assertIn('id="2"', sent)
            finally:
                await agent.close()

    async def test_streamed_tf_reads_need_and_auto_result_survive_projection_and_compaction(self):
        measurements = []
        with tempfile.TemporaryDirectory(prefix='thinkflow-projection-') as directory:
            payload = ''.join(f'line-{i:04d}: complete content\n' for i in range(200))
            (Path(directory) / 'source.txt').write_bytes(payload.encode())
            for protocol in ('openai', 'anthropic'):
                for need_result in (False, True):
                    agent = AgentLoop(AgentConfig(cwd=directory, provider=ProviderConfig(
                        base_url='http://127.0.0.1:1', model='fixture', format=protocol,
                        max_tokens=2048, enable_native_tools=False)))
                    requests = []

                    class TextStream(httpx.AsyncByteStream):
                        def __init__(self, reply):
                            self.reply = reply

                        async def __aiter__(self):
                            if protocol == 'openai':
                                frames = [{'choices': [{'delta': {'content': self.reply}, 'finish_reason': None}]},
                                          {'choices': [{'delta': {}, 'finish_reason': 'stop'}]}]
                            else:
                                frames = [{'type': 'content_block_start', 'index': 0,
                                           'content_block': {'type': 'text', 'text': ''}},
                                          {'type': 'content_block_delta', 'index': 0,
                                           'delta': {'type': 'text_delta', 'text': self.reply}},
                                          {'type': 'content_block_stop', 'index': 0},
                                          {'type': 'message_stop'}]
                            for frame in frames:
                                yield ('data: ' + json.dumps(frame) + '\n\n').encode()
                            yield b'data: [DONE]\n\n'

                    def receive(request):
                        body = json.loads(request.content)
                        requests.append(body)
                        if len(requests) <= 2:
                            flag = ' need_result="true"' if need_result else ''
                            reply = f'<tf-read id="{len(requests)}" path="source.txt"{flag} />'
                        else:
                            reply = 'Reads complete.'
                            baseline = project_messages(with_observation(agent.messages,
                                agent.build_runtime_status_message()['content']), protocol)
                            actual = [m for m in body['messages'] if m['role'] != 'system']
                            size = lambda value: len(json.dumps(value, ensure_ascii=False, separators=(',', ':')))
                            measurements.append((protocol, need_result, size(baseline), size(actual)))
                        return httpx.Response(200, stream=TextStream(reply))

                    await agent.client.aclose()
                    agent.client = httpx.AsyncClient(base_url='http://127.0.0.1:1', transport=httpx.MockTransport(receive))
                    try:
                        await agent.run('Read source.txt twice and retain both successful observations.')
                        self.assertEqual(len(requests), 3)
                        self.assertEqual([(r.id, r.status) for r in agent.context.records],
                                         [('1', 'success'), ('2', 'success')])
                        results = [m for m in agent.messages if m.get('_thinkflow_reads')]
                        self.assertEqual(len(results), 2)
                        for message in results:
                            self.assertEqual(len(message['_thinkflow_reads']), 1)
                            span = message['_thinkflow_reads'][0]
                            self.assertEqual(message['content'][span['start']:span['end']], payload)
                            self.assertIn('[THINKFLOW RESULT' if need_result else '[THINKFLOW RESULTS', message['content'])
                        sent_text = '\n'.join(m['content'] for m in requests[-1]['messages']
                                             if isinstance(m.get('content'), str))
                        self.assertEqual(sent_text.count(payload), 1)
                        self.assertIn('Exact same complete content', sent_text)
                        self.assertNotIn('_thinkflow_reads', json.dumps(requests))
                        original = copy.deepcopy(agent.messages)
                        agent.config.provider.format = 'anthropic' if protocol == 'openai' else 'openai'
                        switched = agent.build_request_body('')['messages']
                        self.assertEqual(agent.messages, original)
                        self.assertEqual(sum(m.get('content', '').count(payload) for m in switched
                                             if isinstance(m.get('content'), str)), 1)
                        agent.config.compaction = CompactionConfig(keep_recent_messages=2,
                                                                   max_summary_chars=300, max_item_chars=100)
                        self.assertTrue(agent.compact(force=True).changed)
                        agent.load_snapshot(json.loads(json.dumps(agent.to_snapshot())))
                        retained = [m for m in agent.messages if m.get('_thinkflow_reads')]
                        self.assertEqual([s['id'] for m in retained for s in m['_thinkflow_reads']], ['2'])
                        materialized = agent.build_request_body('')['messages']
                        latest = next(m['content'] for m in materialized if isinstance(m.get('content'), str)
                                      and 'id="2"' in m['content'])
                        self.assertIn(payload, latest)
                        self.assertNotIn('Exact same complete content', latest)
                        self.assertNotIn('_thinkflow_reads', json.dumps(materialized))
                    finally:
                        await agent.close()
        for protocol, need_result, before, after in measurements:
            self.assertGreater(before - after, len(payload) - 200)
        print('Exact read projection: protocol, need_result, baseline JSON chars, projected JSON chars:', measurements)

    async def test_native_read_reference_materializes_after_actual_compaction(self):
        with tempfile.TemporaryDirectory(prefix='thinkflow-projection-') as directory:
            payload = ''.join(f'unique-line-{i:04d}\n' for i in range(200))
            path = Path(directory) / 'source.txt'
            path.write_bytes(payload.encode())
            for protocol in ('openai', 'anthropic'):
                agent = AgentLoop(AgentConfig(cwd=directory, provider=ProviderConfig(
                    base_url='http://127.0.0.1:1', model='fixture', format=protocol, max_tokens=2048)))
                try:
                    agent.messages = [{'role': 'user', 'content': 'Read twice'}]
                    for identifier in ('first', 'second'):
                        await agent._handle_traditional_tools([{'id': identifier, 'name': 'read',
                            'input': json.dumps({'path': 'source.txt'})}])
                    original = copy.deepcopy(agent.messages)
                    projected = agent.build_request_body('')['messages']
                    text = (projected[-1]['content'] if protocol == 'openai'
                            else projected[-1]['content'][0]['content'])
                    self.assertIn('Exact same complete content', text)
                    self.assertNotIn(payload, text)
                    self.assertEqual(agent.messages, original)
                    agent.config.compaction = CompactionConfig(keep_recent_messages=2,
                                                               max_summary_chars=300, max_item_chars=100)
                    self.assertTrue(agent.compact(force=True).changed)
                    snapshot = json.loads(json.dumps(agent.to_snapshot()))
                    agent.load_snapshot(snapshot)
                    projected = agent.build_request_body('')['messages']
                    text = (projected[-1]['content'] if protocol == 'openai'
                            else projected[-1]['content'][0]['content'])
                    self.assertIn(payload, text)
                    for identifier, args, expected in (
                        ('range', {'path': 'source.txt', 'offset': 2, 'limit': 2},
                         'unique-line-0001\nunique-line-0002\n'),
                        ('new', {'path': 'source.txt'}, 'new version\n' * 100)):
                        if identifier == 'new':
                            path.write_bytes(expected.encode())
                        await agent._handle_traditional_tools([{'id': identifier, 'name': 'read',
                            'input': json.dumps(args)}])
                        original_result = (agent.messages[-1]['content'] if protocol == 'openai'
                                           else agent.messages[-1]['content'][0]['content'])
                        projected = agent.build_request_body('')['messages']
                        text = (projected[-1]['content'] if protocol == 'openai'
                                else projected[-1]['content'][0]['content'])
                        # A range may reference retained full text; its actual
                        # range/version note must remain byte for byte.
                        note = agent.context.records[-1].read_note
                        self.assertIn(note, text)
                        self.assertIn(expected, original_result)
                        if identifier == 'new':
                            self.assertIn(expected, text)
                finally:
                    await agent.close()
                    path.write_bytes(payload.encode())

    async def test_real_configure_both_directions_after_compaction_and_snapshot(self):
        with tempfile.TemporaryDirectory(prefix='thinkflow-projection-') as directory:
            root = Path(directory)
            workspace = root / 'workspace'
            workspace.mkdir()
            payload = '完整输入\n' * 250
            (workspace / 'source.txt').write_bytes(payload.encode('utf-8'))
            with patch.dict(os.environ, {'THINKFLOW_HOME': str(root / 'home')}):
                service = DesktopService()
                try:
                    await service.dispatch('initialize', {'data_dir': str(root / 'data'), 'config': {
                        'base_url': 'http://127.0.0.1:1', 'model': 'fixture', 'max_tokens': 2048,
                        'enable_native_tools': True, 'use_builtin_system_prompt': False}})
                    await service.open_workspace({'cwd': str(workspace)})
                    for source, target in (('openai', 'anthropic'), ('anthropic', 'openai')):
                        await service.configure({'provider': source})
                        agent = service.agent
                        agent.config.security.approval_mode = 'approve_all'
                        agent.messages = [{'role': 'user', 'content': '旧要求'},
                                          {'role': 'assistant', 'content': '旧答复'},
                                          {'role': 'user', 'content': '保留此轮真实用户原文'}]
                        for identifier, name, args in (
                            ('r', 'read', {'path': 'source.txt'}),
                            ('w', 'write', {'path': f'copy-{source}.txt', 'content': payload}),
                            ('e', 'read', {'path': 'missing.txt'})):
                            await agent._handle_traditional_tools([{'id': source + identifier, 'name': name,
                                'input': json.dumps(args, ensure_ascii=False)}])
                        self.assertEqual((workspace / f'copy-{source}.txt').read_text(encoding='utf-8'), payload)
                        error_body = agent.context.records[-1].error
                        self.assertTrue(error_body)
                        agent.config.compaction = CompactionConfig(keep_recent_messages=7, max_summary_chars=500)
                        self.assertTrue(agent.compact(force=True).changed)
                        persisted = copy.deepcopy(agent.to_snapshot())
                        await service.configure({'provider': target})
                        self.assertEqual(service.agent.messages, persisted['messages'])
                        wire = service.agent.build_request_body('')['messages']
                        self.assertIn('保留此轮真实用户原文', json.dumps(wire, ensure_ascii=False))
                        if target == 'anthropic':
                            self.assertFalse(any(m.get('role') == 'tool' or 'tool_calls' in m for m in wire))
                            calls = [b['id'] for m in wire if isinstance(m.get('content'), list)
                                     for b in m['content'] if b['type'] == 'tool_use']
                            results = [b for m in wire if isinstance(m.get('content'), list)
                                       for b in m['content'] if b['type'] == 'tool_result']
                            self.assertEqual(calls, [b['tool_use_id'] for b in results])
                            self.assertTrue(results[-1]['is_error'])
                        else:
                            calls = [c['id'] for m in wire for c in m.get('tool_calls', [])]
                            results = [m for m in wire if m['role'] == 'tool']
                            self.assertEqual(calls, [m['tool_call_id'] for m in results])
                            self.assertFalse(any(b['type'] in ('tool_use', 'tool_result')
                                for m in wire if isinstance(m.get('content'), list) for b in m['content']))
                        self.assertEqual(calls, [source + identifier for identifier in ('r', 'w', 'e')])
                        self.assertIn(payload, results[0]['content'])
                        self.assertIn(error_body, results[-1]['content'])
                        self.assertEqual(service.agent.messages, persisted['messages'])
                finally:
                    await service.close()


if __name__ == '__main__':
    unittest.main()
