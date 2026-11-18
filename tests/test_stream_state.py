"""Exercise predictable state updates through real SSE, FIFO and persistence."""
import asyncio
import json
from pathlib import Path
import tempfile
import unittest

import httpx

from src.agent_loop import AgentConfig, AgentLoop
from src.compaction import CompactionConfig
from src.parser import StreamingParser
from src.provider import ProviderConfig
from src.security import SecurityPolicy
from src.session import SessionStore


def plan(status='in_progress', evidence=(), **extra):
    return {'explanation': '阶段选择', 'steps': [{'id': 's1', 'title': '生成文件',
            'acceptance': '文件包含目标正文', 'status': status, 'evidence': list(evidence)}], **extra}


def tag(identifier, name, arguments, attributes=''):
    return f'<tf-{name} id="{identifier}"{attributes}>' + json.dumps(arguments, ensure_ascii=False) + f'</tf-{name}>\n'


class Wire(httpx.AsyncByteStream):
    def __init__(self, chunks, protocol='openai', after_chunk=None):
        self.chunks, self.protocol, self.after_chunk = chunks, protocol, after_chunk

    async def __aiter__(self):
        def event(value):
            return ('data: ' + json.dumps(value, ensure_ascii=False) + '\n\n').encode('utf8')
        if self.protocol == 'anthropic':
            yield event({'type': 'message_start', 'message': {'usage': {'input_tokens': 10}}})
            yield event({'type': 'content_block_start', 'index': 0, 'content_block': {'type': 'text', 'text': ''}})
        for index, chunk in enumerate(self.chunks):
            value = ({'type': 'content_block_delta', 'index': 0, 'delta': {'type': 'text_delta', 'text': chunk}}
                     if self.protocol == 'anthropic' else {'choices': [{'delta': {'content': chunk}, 'finish_reason': None}]})
            yield event(value)
            if self.after_chunk:
                await self.after_chunk(index)
            else:
                await asyncio.sleep(0)
        if self.protocol == 'anthropic':
            yield event({'type': 'content_block_stop', 'index': 0})
            yield event({'type': 'message_delta', 'delta': {'stop_reason': 'end_turn'}, 'usage': {'output_tokens': 10}})
            yield event({'type': 'message_stop'})
        else:
            yield event({'choices': [{'delta': {}, 'finish_reason': 'stop'}],
                         'usage': {'prompt_tokens': 10, 'completion_tokens': 10}})
            yield b'data: [DONE]\n\n'


class StreamStateTests(unittest.IsolatedAsyncioTestCase):
    async def test_command_markup_after_an_id_collision_cannot_finish_the_next_release(self):
        with tempfile.TemporaryDirectory() as directory:
            bare = '<read id="3" path="a" />\n<read id="4" path="b" />'
            agent, requests = await self.scripted_agent(directory, [
                '<tf-write id="10" path="a">one</tf-write>Delivered A.',
                '<tf-write id="10" path="b">two</tf-write>',
                bare,
                '<tf-write id="11" path="b">two</tf-write>Delivered B.',
            ], require_final_response=True)
            await agent.run('Release A')
            await agent.run('Release B')
            self.assertEqual(len(requests), 4)
            self.assertEqual(agent.stopped_reason, 'completed')
            self.assertEqual(Path(directory, 'b').read_bytes(), b'two')
            self.assertNotIn('read', [r.tool for r in agent.context.records])
            self.assertIn('id="11"', requests[1]['messages'][-1]['content'])
            self.assertIn('id="11"', requests[2]['messages'][-1]['content'])
            self.assertIn('没有执行', requests[3]['messages'][-1]['content'])

    async def test_unfinished_followup_tag_cannot_hide_a_real_file_conflict(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory, 'flag')
            target.write_bytes(b'changed externally')
            first = ('<tf-edit id="1" path="flag" match="file"><old>draft</old><new>ready</new></tf-edit>\n'
                     '<tf-write id="2" path="marker">')
            agent, requests = await self.scripted_agent(directory, [first, 'The old text did not match.'])
            await agent.run('If the old text differs, leave everything in place and report.')
            observation = '\n'.join(m['content'] for m in requests[1]['messages'] if m['role'] == 'user')
            self.assertIn('revision_conflict', observation)
            self.assertNotIn('THINKFLOW PARSER ERROR', observation)
            self.assertFalse(agent.native_write_fallback)
            self.assertEqual(target.read_bytes(), b'changed externally')
            self.assertFalse(Path(directory, 'marker').exists())

    async def test_visible_delivery_continues_a_tool_only_partial_result(self):
        with tempfile.TemporaryDirectory() as directory:
            agent, requests = await self.scripted_agent(directory, [
                '<tf-write id="1" path="a">A</tf-write>',
                '<tf-write id="2" path="b">B</tf-write>Both files are ready.',
            ], require_final_response=True)
            await agent.run('Write both files and tell me the result.')
            self.assertEqual(len(requests), 2)
            self.assertEqual(agent.stopped_reason, 'completed')
            self.assertEqual(Path(directory, 'b').read_bytes(), b'B')
            self.assertIn('THINKFLOW CONTINUE', json.dumps(requests[1]))

    async def test_visible_delivery_reports_incomplete_after_second_silent_turn(self):
        with tempfile.TemporaryDirectory() as directory:
            action = '<tf-touch id="1" path="marker" />'
            agent, requests = await self.scripted_agent(directory, [action, action], require_final_response=True)
            await agent.run('Create the marker and reply.')
            self.assertEqual(len(requests), 2)
            self.assertEqual(agent.stopped_reason, 'incomplete_response')
            self.assertEqual(len(agent.context.records), 1)

    async def test_stream_replay_keeps_one_effect_and_continues_the_same_request(self):
        with tempfile.TemporaryDirectory() as directory:
            first = '<tf-write id="1" path="log">A</tf-write>'
            append = '<tf-append id="2" path="log">B</tf-append>'
            next_write = '<tf-write id="3" path="marker">ok</tf-write>'
            agent, requests = await self.scripted_agent(directory, [first + append + append + next_write + 'done'])
            await agent.run('prepare the log')
            self.assertEqual(len(requests), 1)
            self.assertEqual(Path(directory, 'log').read_bytes(), b'AB')
            self.assertEqual(Path(directory, 'marker').read_bytes(), b'ok')
            self.assertEqual([r.id for r in agent.context.records], ['1', '2', '3'])

    async def test_stream_id_collision_blocks_the_following_write(self):
        with tempfile.TemporaryDirectory() as directory:
            first = '<tf-write id="1" path="log">A</tf-write>'
            second = '<tf-write id="1" path="log">B</tf-write>'
            marker = '<tf-write id="2" path="marker">premature</tf-write>'
            agent, requests = await self.scripted_agent(directory, [first + second + marker, 'conflict'])
            await agent.run('prepare the log')
            self.assertEqual(len(requests), 2)
            self.assertEqual(Path(directory, 'log').read_bytes(), b'A')
            self.assertFalse(Path(directory, 'marker').exists())
            self.assertEqual([r.status for r in agent.context.records], ['success', 'failed', 'skipped'])

    async def scripted_agent(self, directory, replies, *, protocol='openai', **settings):
        agent = AgentLoop(AgentConfig(cwd=directory, **settings, provider=ProviderConfig(
            format=protocol, model='fixture', max_tokens=4096, enable_native_tools=False)), event_sink=lambda _: None)
        requests = []
        async def request(req):
            requests.append(json.loads(req.content))
            return httpx.Response(200, stream=Wire([replies[len(requests) - 1]], protocol))
        await agent.client.aclose()
        agent.client = httpx.AsyncClient(transport=httpx.MockTransport(request), base_url='http://fixture')
        self.addAsyncCleanup(agent.close)
        return agent, requests

    async def test_unfinished_plan_continues_across_delayed_work_until_completed(self):
        for protocol in ('openai', 'anthropic'):
            with self.subTest(protocol=protocol), tempfile.TemporaryDirectory() as directory:
                replies = [
                    tag('1', 'update_plan', plan('pending')) + '等待工具回执。',
                    '<tf-write id="2" path="one.txt">1</tf-write>',
                    '<tf-write id="3" path="two.txt">2</tf-write>',
                    '<tf-write id="4" path="three.txt">3</tf-write>\n' +
                    tag('5', 'update_plan', plan('completed', ['2', '3', '4'])) + '完成。',
                ]
                agent, requests = await self.scripted_agent(directory, replies, protocol=protocol)
                await agent.run('按计划生成三个文件。')
                self.assertEqual(len(requests), 4)
                self.assertEqual(agent.stopped_reason, 'completed')
                self.assertEqual(agent.task_plan['steps'][0]['evidence'], ['2', '3', '4'])
                self.assertEqual(sorted(p.read_text() for p in Path(directory).glob('*.txt')), ['1', '2', '3'])

    async def test_blocked_question_can_stop_with_later_pending_work(self):
        with tempfile.TemporaryDirectory() as directory:
            waiting = plan('blocked')
            waiting['steps'].append({**plan('pending')['steps'][0], 'id': 'later'})
            agent, requests = await self.scripted_agent(directory, [
                tag('1', 'update_plan', waiting) + '您想采用哪种格式？'])
            await agent.run('先问我需要的格式，等我回复。')
            self.assertEqual(len(requests), 1)
            self.assertEqual(agent.task_plan, waiting)

    async def test_pending_plan_respects_budget_and_stops_on_repeated_empty_turns(self):
        for budget, expected, reason in ((0, 1, 'max_auto_continues'), (1, 2, 'max_auto_continues'), (None, 3, 'incomplete_plan')):
            with self.subTest(budget=budget), tempfile.TemporaryDirectory() as directory:
                agent, requests = await self.scripted_agent(directory, [
                    tag('1', 'update_plan', plan()), '等待执行回执。', '等待执行回执。',
                ], max_auto_continues=budget)
                await agent.run('生成目标文件。')
                self.assertEqual(len(requests), expected)
                self.assertEqual(agent.stopped_reason, reason)

    async def test_no_repeat_without_progress_or_carryover_to_next_request(self):
        with tempfile.TemporaryDirectory() as directory:
            agent, requests = await self.scripted_agent(directory, [
                tag('1', 'update_plan', plan()),
                tag('2', 'update_plan', {**plan(), 'explanation': '仍在等待'}) + '请提供所需名称。',
                '<tf-write id="3" path="unrelated.txt">other</tf-write>\n完成。',
                '已发出命令，正在等待回执。',
            ], max_auto_continues=None, max_run_turns=None)
            await agent.run('先写计划。')
            self.assertEqual(len(requests), 2)
            await agent.run('另建一个不相关的文件。')
            await agent.run('原样复述一句等待回执的话。')
            self.assertEqual(len(requests), 4)
            self.assertEqual((Path(directory) / 'unrelated.txt').read_text(), 'other')

    async def test_plan_write_and_completion_share_one_live_response(self):
        for protocol in ('openai', 'anthropic'):
            with self.subTest(protocol=protocol), tempfile.TemporaryDirectory(prefix='tf-stream-plan-') as directory:
                completed = [asyncio.Event() for _ in range(3)]
                events, requests = [], []

                def observe(event):
                    events.append(event)
                    if event['type'] == 'tool_completed':
                        completed[int(event['id']) - 1].set()

                agent = AgentLoop(AgentConfig(cwd=directory, compaction=CompactionConfig(enabled=False),
                    provider=ProviderConfig(format=protocol, model='fixture', enable_native_tools=False,
                                            max_tokens=4096)), event_sink=observe)
                store = SessionStore(str(Path(directory) / 'session.json'), cwd=directory)
                store.bind(agent)
                stage = {'summary': '已选定正文', 'decisions': ['使用UTF-8'],
                         'files': [{'path': '结果.txt', 'note': '交付文件'}]}
                chunks = [tag('1', 'update_plan', plan(context=stage)),
                          '<tf-write id="2" path="结果.txt">目标正文</tf-write>\n',
                          tag('3', 'update_plan', plan('completed', ['2'])), '完成。']

                async def after(index):
                    if index < 3:
                        await asyncio.wait_for(completed[index].wait(), 3)
                        self.assertFalse(any(e['type'] == 'run_finished' for e in events))

                async def request(req):
                    requests.append(json.loads(req.content))
                    return httpx.Response(200, stream=Wire(chunks, protocol, after))

                await agent.client.aclose()
                agent.client = httpx.AsyncClient(transport=httpx.MockTransport(request), base_url='http://fixture')
                try:
                    await agent.run('生成中文文件并更新阶段状态')
                    self.assertEqual(len(requests), 1)
                    self.assertEqual((Path(directory) / '结果.txt').read_text(encoding='utf8'), '目标正文')
                    self.assertEqual([r.status for r in agent.context.records], ['success'] * 3)
                    self.assertEqual(agent.task_plan, plan('completed', ['2'], context=stage))
                    self.assertEqual(store.load()['task_plan'], agent.task_plan)
                    self.assertEqual(agent.usage.totals()['api_calls'], 1)
                finally:
                    await agent.close()

    async def test_verified_prediction_continues_only_after_exact_match(self):
        for protocol in ('openai', 'anthropic'):
            for matches in (True, False):
                with self.subTest(protocol=protocol, matches=matches), tempfile.TemporaryDirectory() as directory:
                    root = Path(directory)
                    original = b'on\n' if matches else b'on\nkeep this note\n'
                    (root/'flag').write_bytes(original)
                    command = '<tf-edit id="1" path="flag" match="file"><old>on\n</old><new>off\n</new></tf-edit>'
                    command += '\n<tf-write id="2" path="done">done</tf-write>\n'
                    agent, requests = await self.scripted_agent(directory, [command, 'The file differed.'], protocol=protocol)
                    await agent.run('Replace only the exact expected file, then mark done.')
                    self.assertEqual((root/'flag').read_bytes(), b'off\n' if matches else original)
                    self.assertEqual((root/'done').exists(), matches)
                    self.assertEqual(len(requests), 1 if matches else 2)
                    self.assertEqual(agent.context.records[0].match, 'file')
                    if not matches:
                        self.assertIn('revision_conflict', json.dumps(requests[1]))

    async def test_failed_plan_stops_dependent_write_and_returns_actual_error(self):
        with tempfile.TemporaryDirectory(prefix='tf-plan-failure-') as directory:
            agent = AgentLoop(AgentConfig(cwd=directory, provider=ProviderConfig(enable_native_tools=False)),
                              event_sink=lambda _: None)
            requests = []
            async def request(req):
                requests.append(json.loads(req.content))
                text = (tag('1', 'update_plan', plan('completed', ['missing'])) +
                        '<tf-touch id="2" path="must-not-exist" />\n' if len(requests) == 1 else '已收到失败原因。')
                return httpx.Response(200, stream=Wire([text]))
            await agent.client.aclose()
            agent.client = httpx.AsyncClient(transport=httpx.MockTransport(request), base_url='http://fixture')
            try:
                await agent.run('fixture')
                self.assertEqual(len(requests), 2)
                self.assertFalse((Path(directory) / 'must-not-exist').exists())
                self.assertEqual(agent.task_plan, {'explanation': '', 'steps': []})
                self.assertIn('missing', json.dumps(requests[1], ensure_ascii=False))
                self.assertEqual([r.status for r in agent.context.records], ['failed', 'skipped'])
            finally:
                await agent.close()

    async def test_failed_plan_receipt_can_be_corrected_before_completion(self):
        with tempfile.TemporaryDirectory() as directory:
            agent, requests = await self.scripted_agent(directory, [
                tag('1', 'update_plan', plan()) +
                '<tf-write id="2" path="proof.txt">ok</tf-write>\n' +
                tag('3', 'update_plan', plan('completed', ['missing'])),
                tag('4', 'update_plan', plan('completed', ['2'])) + '完成。',
            ])
            await agent.run('生成文件，并根据回执更新计划。')
            self.assertEqual(len(requests), 2)
            self.assertIn('最近成功回执', json.dumps(requests[1], ensure_ascii=False))
            self.assertEqual([r.status for r in agent.context.records], ['success', 'success', 'failed', 'success'])
            self.assertEqual((Path(directory) / 'proof.txt').read_text(), 'ok')
            self.assertEqual(agent.task_plan, plan('completed', ['2']))

    async def test_explicit_result_boundary_and_read_only_state_update(self):
        with tempfile.TemporaryDirectory(prefix='tf-plan-boundary-') as directory:
            agent = AgentLoop(AgentConfig(cwd=directory, security=SecurityPolicy(read_only=True),
                              provider=ProviderConfig(enable_native_tools=False)), event_sink=lambda _: None)
            requests = []
            async def request(req):
                requests.append(json.loads(req.content))
                text = tag('1', 'update_plan', plan('blocked'), ' need_result="true"') if len(requests) == 1 else '计划已保存，等您确认。'
                return httpx.Response(200, stream=Wire([text]))
            await agent.client.aclose()
            agent.client = httpx.AsyncClient(transport=httpx.MockTransport(request), base_url='http://fixture')
            try:
                await agent.run('只读分析也可以保存计划')
                self.assertEqual(len(requests), 2)
                native = await agent._execute_traditional_result('update_plan', plan(), tool_id='native-plan')
                self.assertTrue(native.success, native.error)
                self.assertEqual(agent.task_plan, plan())
            finally:
                await agent.close()

    async def test_information_search_batch_and_partial_json_command(self):
        parser = StreamingParser(allow_legacy_tags=False)
        self.assertEqual(parser.feed('<tf-update_plan id="1">{"steps":[]'), [])
        self.assertIsNotNone(parser.flush())
        with tempfile.TemporaryDirectory(prefix='tf-search-stream-') as directory:
            root = Path(directory)
            (root / '.gitignore').write_text('node_modules/\n', encoding='utf8')
            (root / 'node_modules').mkdir()
            (root / 'node_modules/hidden.txt').write_text('MATCH hidden', encoding='utf8')
            (root / 'visible.txt').write_text('MATCH visible', encoding='utf8')
            agent = AgentLoop(AgentConfig(cwd=directory, provider=ProviderConfig(enable_native_tools=False)),
                              event_sink=lambda _: None)
            requests = []
            async def request(req):
                requests.append(json.loads(req.content))
                text = (tag('1', 'grep', {'pattern': 'MATCH'}) + tag('2', 'glob', {'pattern': '*.txt'}) +
                        tag('3', 'list_files', {'path': '.'}) if len(requests) == 1 else '完成。')
                return httpx.Response(200, stream=Wire([text]))
            await agent.client.aclose()
            agent.client = httpx.AsyncClient(transport=httpx.MockTransport(request), base_url='http://fixture')
            try:
                await agent.run('查找项目文件')
                self.assertEqual(len(requests), 2)
                self.assertEqual([r.tool for r in agent.context.records], ['grep', 'glob', 'list_files'])
                self.assertEqual([r.status for r in agent.context.records], ['success'] * 3)
                self.assertIn('MATCH visible', json.dumps(requests[1]))
                self.assertNotIn('MATCH hidden', json.dumps(requests[1]))
            finally:
                await agent.close()


if __name__ == '__main__':
    unittest.main()
