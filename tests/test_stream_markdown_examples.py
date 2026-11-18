"""Markdown examples and live commands share the production SSE entry point."""

import asyncio
import json
from pathlib import Path
import tempfile
import unittest

import httpx

from src.agent_loop import AgentConfig, AgentLoop
from src.provider import ProviderConfig


def frame(**delta):
    return ("data: " + json.dumps({"choices": [{"delta": delta}]}) + "\n\n").encode()


STOP = b'data: {"choices":[{"delta":{},"finish_reason":"stop"}]}\n\n'


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


if __name__ == "__main__":
    unittest.main()
