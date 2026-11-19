"""Markdown examples and live commands share the production SSE entry point."""

import asyncio
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import httpx
from rich.console import Console

from src import renderer
from src.agent_loop import AgentConfig, AgentLoop
from src.provider import ProviderConfig
from src.text_filter import SafeTextStreamFilter


def frame(**delta):
    return ("data: " + json.dumps({"choices": [{"delta": delta}]}) + "\n\n").encode()


STOP = b'data: {"choices":[{"delta":{},"finish_reason":"stop"}]}\n\n'


INLINE = '<tf-touch id="9" path="example-inline.txt" />'
FENCED = '<tf-touch id="8" path="example-fenced.txt" />'
LEGACY = '<touch id="7" path="example-legacy.txt" />'
LIVE = '<tf-write id="1" path="doc.txt">private payload `tick`\n~~~text\nfile text\n~~~</tf-write>'
EXPECTED = (
    f'Inline `{INLINE}` and ``literal ` {INLINE}``.\n\n'
    f'````xml\n```\n{FENCED}\n````\n\n'
    f'  Final example ````{LEGACY}````'
)
MIXED = EXPECTED.replace('  Final example', f'{LIVE}  Final example')


class MarkdownDisplayFilterTests(unittest.TestCase):
    def test_visible_markdown_survives_every_split(self):
        for allow_legacy in (False, True):
            chunks = [[MIXED], list(MIXED)]
            chunks.extend([MIXED[:index], MIXED[index:]] for index in range(1, len(MIXED)))
            for pieces in chunks:
                display = SafeTextStreamFilter(allow_legacy_tags=allow_legacy)
                self.assertEqual(''.join(display.feed(piece) for piece in pieces) + display.flush(), EXPECTED)


class FixtureStream(httpx.AsyncByteStream):
    def __init__(self, chunks):
        self.chunks = chunks

    async def __aiter__(self):
        async for chunk in self.chunks:
            yield chunk


class StreamMarkdownExampleTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        directory = self.enterContext(tempfile.TemporaryDirectory(prefix="thinkflow-stream-markdown-"))
        self.root = Path(directory)
        self.completed = asyncio.Event()
        self.events = []

        def observe(event):
            self.events.append(event)
            if event["type"] == "tool_completed" and event.get("id") == "1":
                self.completed.set()

        self.agent = AgentLoop(
            AgentConfig(cwd=directory, max_run_turns=1, delivery_verify=False,
                        provider=ProviderConfig(base_url="http://fixture.test", model="fixture")),
            event_sink=observe,
        )
        self.addAsyncCleanup(self.agent.close)

    async def run_stream(self, chunks):
        await self.agent.client.aclose()
        self.agent.client = httpx.AsyncClient(
            base_url="http://fixture.test",
            transport=httpx.MockTransport(lambda _: httpx.Response(200, stream=FixtureStream(chunks))),
        )
        await self.agent.run("Local Markdown example fixture.")
        self.assertFalse([event for event in self.events if event["type"] == "parser_error"])

    async def test_split_fenced_and_inline_examples_are_inert_in_both_channels(self):
        sample = '<tf-touch id="9" path="example.txt" />'
        examples = (
            '    ````xml\n```\n````xml\n' + sample + '\n````\n'
            'Inline `' + sample + '`. Multiple ``' + sample + '``. Unclosed `' + sample + '\n'
            '```' + sample + '```\n'
            '~~~xml\n' + sample + '\n~~~\n'
        )
        payload = 'literal `inline`\n```text\n' + sample + '\n```\n'

        async def chunks():
            for channel in ("reasoning_content", "content"):
                for character in examples:
                    yield frame(**{channel: character})
            for character in '<tf-write id="1" path="doc.md">' + payload + '</tf-write>':
                yield frame(reasoning_content=character)
            yield frame(reasoning_content='\n~~~xml\n')
            yield frame(reasoning_content=sample)
            yield STOP

        await self.run_stream(chunks())
        self.assertFalse((self.root / "example.txt").exists())
        self.assertEqual((self.root / "doc.md").read_bytes(), payload.encode())
        self.assertEqual([record.id for record in self.agent.context.records], ["1"])

    async def test_command_executes_before_next_delta_then_preserves_cross_channel_order(self):
        async def chunks():
            yield frame(content='<tf-write id="1" path="doc.txt">first</tf-write>')
            await asyncio.wait_for(self.completed.wait(), timeout=5)
            self.assertEqual((self.root / "doc.txt").read_text(), "first")
            yield frame(reasoning_content='<tf-append id="2" path="doc.txt">second</tf-append>')
            yield STOP

        await self.run_stream(chunks())
        self.assertEqual((self.root / "doc.txt").read_text(), "firstsecond")
        self.assertEqual([record.id for record in self.agent.context.records], ["1", "2"])

    async def test_next_line_after_unclosed_inline_example_executes_before_stream_end(self):
        async def chunks():
            yield frame(reasoning_content='Example `<tf-touch id="9" path="example.txt" />\n')
            yield frame(reasoning_content='<tf-touch id="1" path="done.txt" />')
            await asyncio.wait_for(self.completed.wait(), timeout=5)
            self.assertTrue((self.root / "done.txt").exists())
            yield STOP

        await self.run_stream(chunks())
        self.assertFalse((self.root / "example.txt").exists())
        self.assertEqual([record.id for record in self.agent.context.records], ["1"])

    async def test_openai_desktop_events_and_assistant_history_keep_inline_examples(self):
        async def chunks():
            for character in MIXED:
                yield frame(content=character)
            yield STOP

        await self.run_stream(chunks())
        visible = ''.join(event['text'] for event in self.events if event['type'] == 'text_delta')
        self.assertEqual(visible, EXPECTED)
        message = next(m for m in self.agent.messages if m['role'] == 'assistant')
        self.assertEqual(message['content'], EXPECTED)
        self.assertEqual([(record.id, record.status) for record in self.agent.context.records], [('1', 'success')])

    async def test_anthropic_terminal_keeps_inline_examples_and_wire_history(self):
        self.enterContext(patch.object(renderer, '_strip_legacy_tool_tags', False))
        agent = AgentLoop(
            AgentConfig(cwd=str(self.root), max_run_turns=1, delivery_verify=False,
                        allow_legacy_tool_tags=True, provider=ProviderConfig(
                            base_url='http://fixture.test', model='fixture', format='anthropic', max_tokens=2048)),
        )
        self.addAsyncCleanup(agent.close)
        legacy_source = MIXED.replace(LIVE, LIVE.replace('tf-write', 'write'))
        events = [{'type': 'content_block_start', 'index': 0, 'content_block': {'type': 'text', 'text': ''}}]
        events.extend({'type': 'content_block_delta', 'index': 0, 'delta': {'type': 'text_delta', 'text': char}}
                      for char in legacy_source)
        events.extend(({'type': 'content_block_stop', 'index': 0},
                       {'type': 'message_delta', 'delta': {'stop_reason': 'end_turn'}}, {'type': 'message_stop'}))

        async def chunks():
            for event in events:
                data = ('event: ' + event['type'] + '\ndata: ' + json.dumps(event) + '\n\n').encode()
                yield data[:11]
                yield data[11:]

        await agent.client.aclose()
        agent.client = httpx.AsyncClient(
            base_url='http://fixture.test',
            transport=httpx.MockTransport(lambda _: httpx.Response(200, stream=FixtureStream(chunks()))),
        )
        output = io.StringIO()
        with patch.object(renderer, 'console', Console(file=output, force_terminal=False, width=160,
                                                       theme=renderer.THEME)):
            await agent.run('Local Markdown example fixture.')
        rendered = output.getvalue()
        for sample in (INLINE, FENCED, LEGACY):
            self.assertIn(sample, rendered)
        self.assertNotIn('private payload', rendered)
        self.assertEqual([(record.id, record.status) for record in agent.context.records], [('1', 'success')])
        message = next(m for m in agent.messages if m['role'] == 'assistant')
        self.assertEqual(message['content'], [{'type': 'text', 'text': legacy_source}])


if __name__ == "__main__":
    unittest.main()
