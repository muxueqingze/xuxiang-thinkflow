import asyncio
import os
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src.parser import StreamingParser
from src.executor import Executor


class AttributeEncodingTests(unittest.TestCase):
    def parse(self, text, *, split=False):
        parser=StreamingParser(allow_legacy_tags=False)
        commands=[]
        for chunk in text if split else [text]:
            commands.extend(parser.feed(chunk))
        self.assertFalse(parser.errors)
        self.assertEqual(len(commands),1)
        return commands[0]

    def test_xml_entities_decode_once_and_unknown_entities_stay_literal(self):
        command=self.parse('<tf-bash id="1" cmd="echo &quot;x&apos;y&lt;z&gt;&amp;&quot; &amp;quot; &unknown;" />',split=True)
        self.assertEqual(command.cmd, 'echo "x\'y<z>&" &quot; &unknown;')

    def test_escaped_delimiter_and_windows_path_preservation(self):
        command=self.parse(r'<tf-bash id="1" cmd="python -c \"print(123)\"" />',split=True)
        self.assertEqual(command.cmd,'python -c "print(123)"')
        command=self.parse(r'<tf-write id="2" path="C:\work\new\test.txt">ok</tf-write>')
        self.assertEqual(command.path,r'C:\work\new\test.txt')
        command=self.parse(r'''<tf-bash id='3' cmd='echo \'quoted\'' />''')
        self.assertEqual(command.cmd,"echo 'quoted'")

    def test_decoded_command_executes_through_actual_shell(self):
        executable=sys.executable.replace('&','&amp;').replace('"','&quot;')
        command=self.parse(f'<tf-bash id="1" cmd="&quot;{executable}&quot; -c &quot;print(&apos;ATTR_OK&apos;)&quot;" />')
        with tempfile.TemporaryDirectory() as directory:
            result=asyncio.run(Executor(cwd=directory).execute(command))
        self.assertTrue(result.success,result.error)
        self.assertEqual(result.stdout.strip(),'ATTR_OK')


if __name__=='__main__':
    unittest.main()
