"""Markdown fences inside live file commands must survive text SSE intact."""
import json
from pathlib import Path
import sys
import tempfile
import unittest

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src.agent_loop import AgentLoop, AgentConfig
from src.provider import ProviderConfig
from src.text_filter import MarkdownFenceCommandGate, SafeTextStreamFilter
from src.parser import StreamingParser


PAYLOAD = '# Demo\n```python\nprint(1)\n```\n'
COMMAND = '<tf-write id="1" path="doc.md">' + PAYLOAD + '</tf-write>'


class MarkdownPayloadTests(unittest.IsolatedAsyncioTestCase):
    async def execute_stream(self, chunks, expected, *, forbidden=()):
        with tempfile.TemporaryDirectory(prefix="thinkflow-markdown-payload-") as name:
            cwd = Path(name).resolve()
            events, requests = [], []
            agent = AgentLoop(AgentConfig(cwd=str(cwd), max_run_turns=3,
                provider=ProviderConfig(base_url="http://127.0.0.1:1", model="local-fixture")),
                event_sink=events.append)
            async def reply(request):
                requests.append(json.loads(request.content))
                frames = [{"delta": {"content": chunk}, "finish_reason": None} for chunk in chunks]
                frames.append({"delta": {}, "finish_reason": "stop"})
                return httpx.Response(200, text="".join("data: " + json.dumps({"choices": [frame]}) + "\n\n"
                    for frame in frames) + "data: [DONE]\n\n")
            await agent.client.aclose()
            agent.client = httpx.AsyncClient(base_url="http://127.0.0.1:1", transport=httpx.MockTransport(reply))
            try:
                await agent.run("Local markdown fixture")
                self.assertEqual(len(requests), 1, agent.last_error)
                self.assertEqual((cwd / "doc.md").read_bytes(), expected.encode("utf8"))
                for path in forbidden:
                    self.assertFalse((cwd / path).exists())
                rendered = "".join(event.get("text", "") for event in events if event["type"] == "text_delta")
                self.assertNotIn("print(1)", rendered)
                self.assertNotIn("print(1)", "".join(message.get("content") or "" for message in agent.messages if message["role"] == "assistant"))
                return rendered
            finally:
                await agent.close()

    async def test_full_command_in_one_text_sse_chunk(self):
        self.assertEqual(await self.execute_stream([COMMAND], PAYLOAD), "")

    async def test_open_tags_fences_and_closing_tags_split_at_every_character(self):
        self.assertEqual(await self.execute_stream(list(COMMAND), PAYLOAD), "")

    async def test_append_and_edit_preserve_fenced_payload(self):
        after = '\n~~~python\nprint(2)\n~~~\n'
        text = (COMMAND + '\n<tf-append id="2" path="doc.md">' + after + '</tf-append>\n'
            '<tf-edit id="3" path="doc.md"><old>```python\nprint(1)\n```</old>'
            '<new>```python\nprint(3)\n```</new></tf-edit>')
        await self.execute_stream([text[index:index+7] for index in range(0, len(text), 7)],
                                 PAYLOAD.replace('print(1)', 'print(3)') + after)

    async def test_top_level_examples_still_do_not_execute_before_or_after_payload(self):
        example = '```xml\n<tf-write id="8" path="example.txt">demo</tf-write>\n```\n'
        ending = '\n~~~xml\n<tf-touch id="9" path="example-after.txt" />\n~~~\n'
        rendered = await self.execute_stream([example, COMMAND, ending], PAYLOAD,
                                             forbidden=("example.txt", "example-after.txt"))
        self.assertIn('path="example.txt"', rendered)
        self.assertIn('path="example-after.txt"', rendered)


class FenceBoundaryTests(unittest.TestCase):
    def test_unfinished_open_tag_does_not_enable_top_level_fenced_example(self):
        gate = MarkdownFenceCommandGate()
        parser = StreamingParser(allow_legacy_tags=False)
        text = '<tf-write id="1"\n```xml\n<tf-write id="2" path="example">demo</tf-write>\n```\n'
        commands = parser.feed(gate.feed(text) + gate.flush())
        self.assertEqual(commands, [])
        self.assertNotIn('path="example"', parser.buffer)

    def test_legacy_payload_tracking_requires_explicit_legacy_mode(self):
        text = COMMAND.replace('<tf-write', '<write').replace('</tf-write>', '</write>')
        legacy = MarkdownFenceCommandGate(allow_legacy_tags=True)
        strict = MarkdownFenceCommandGate(allow_legacy_tags=False)
        self.assertEqual(legacy.feed(text) + legacy.flush(), text)
        self.assertNotIn('print(1)', strict.feed(text) + strict.flush())
        filt = SafeTextStreamFilter(allow_legacy_tags=True)
        self.assertEqual(filt.feed(text) + filt.flush(), '')

    def test_unclosed_payload_never_leaks_code_to_visible_text(self):
        filt = SafeTextStreamFilter(allow_legacy_tags=False)
        text = COMMAND.removesuffix('</tf-write>')
        visible = "".join(filt.feed(char) for char in text) + filt.flush()
        self.assertEqual(visible, "")

    def test_fence_like_lines_inside_body_do_not_hide_following_command(self):
        text = ('<tf-write id="1" path="a">```python\nexample without a closing fence</tf-write>\n'
                '<tf-touch id="2" path="b" />\n')
        gate = MarkdownFenceCommandGate()
        parser = StreamingParser(allow_legacy_tags=False)
        commands = parser.feed("".join(gate.feed(char) for char in text) + gate.flush())
        self.assertEqual([command.id for command in commands], ["1", "2"])
        self.assertIn('```python', commands[0].content)


if __name__ == "__main__":
    unittest.main(verbosity=2)
