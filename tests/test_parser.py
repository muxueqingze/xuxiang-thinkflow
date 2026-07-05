"""
ThinkFlow Parser 测试

测试流式解析器的各种场景：
- 完整命令块提取
- 流式增量输入
- 自闭合命令
- 多命令连续输出
- 格式残缺
- edit 子标签
"""

import sys
import os

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

# 添加项目根目录到 path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.parser import StreamingParser, Command


def test_single_write():
    """测试：单个 write 命令（完整输入）"""
    print("测试: 单个 write 命令")
    parser = StreamingParser()

    text = '''让我写一个文件。
<write id="1" path="./test.md">
# 标题
正文内容。
</write>
写完了。'''

    cmds = parser.feed(text)
    assert len(cmds) == 1, f"期望 1 条命令，得到 {len(cmds)}"
    assert cmds[0].tool == "write"
    assert cmds[0].id == "1"
    assert cmds[0].path == "./test.md"
    assert "# 标题" in cmds[0].content
    assert "正文内容" in cmds[0].content
    print(f"  ✓ 提取到 write#{cmds[0].id} path={cmds[0].path}")
    print(f"  ✓ content 长度: {len(cmds[0].content)}")
    print()


def test_streaming_write():
    """测试：write 命令分多次到达"""
    print("测试: 流式输入（分片）")
    parser = StreamingParser()

    chunks = [
        '让我写文件。\n<write id="1" pa',
        'th="./test.md">\n第一行\n',
        '第二行\n</write>\n写完了。',
    ]

    all_cmds = []
    for i, chunk in enumerate(chunks):
        cmds = parser.feed(chunk)
        all_cmds.extend(cmds)
        print(f"  分片{i}: 得到 {len(cmds)} 条命令")

    assert len(all_cmds) == 1, f"期望 1 条命令，得到 {len(all_cmds)}"
    assert "第一行" in all_cmds[0].content
    assert "第二行" in all_cmds[0].content
    print(f"  ✓ 最终提取到 write#{all_cmds[0].id}")
    print()


def test_multiple_commands():
    """测试：多个命令连续输出"""
    print("测试: 多命令连续输出")
    parser = StreamingParser()

    text = '''<write id="1" path="./a.txt">
内容A
</write>
<mkdir id="2" path="./output" />
<bash id="3" cmd="echo hello" />
<write id="4" path="./b.txt">
内容B
</write>'''

    cmds = parser.feed(text)
    assert len(cmds) == 4, f"期望 4 条命令，得到 {len(cmds)}"
    tools = [c.tool for c in cmds]
    assert tools == ["write", "mkdir", "bash", "write"], f"工具顺序错误: {tools}"

    # 验证各自属性
    assert cmds[1].tool == "mkdir" and cmds[1].path == "./output"
    assert cmds[2].tool == "bash" and cmds[2].cmd == "echo hello"

    print(f"  ✓ 提取到 4 条命令: {tools}")
    print()


def test_self_closing():
    """测试：自闭合命令"""
    print("测试: 自闭合命令")
    parser = StreamingParser()

    text = '<mkdir id="1" path="./test" />'
    cmds = parser.feed(text)
    assert len(cmds) == 1
    assert cmds[0].tool == "mkdir"
    assert cmds[0].path == "./test"
    assert cmds[0].content is None
    print(f"  ✓ mkdir#{cmds[0].id} 自闭合")
    print()


def test_append_touch_copy():
    """测试：新增可预测工具 append / touch / copy"""
    print("测试: append / touch / copy")
    parser = StreamingParser()

    text = '''<append id="1" path="./a.txt">
追加
</append>
<touch id="2" path="./empty.txt" />
<copy id="3" path="./a.txt" dest="./b.txt" />'''

    cmds = parser.feed(text)
    assert [cmd.tool for cmd in cmds] == ["append", "touch", "copy"]
    assert cmds[0].content.strip() == "追加"
    assert cmds[1].path == "./empty.txt"
    assert cmds[2].path == "./a.txt"
    assert cmds[2].dest == "./b.txt"
    print("  ✓ append / touch / copy 解析正确")
    print()


def test_read_command():
    """Test canonical tf-read command parsing."""
    print("测试: read 命令")
    parser = StreamingParser()
    cmds = parser.feed('<tf-read id="1" path="./prompt.txt" />')
    assert len(cmds) == 1
    assert cmds[0].tool == "read"
    assert cmds[0].path == "./prompt.txt"
    print("  ✓ read#1 解析正确")
    print()


def test_need_result():
    """测试：need_result 标记"""
    print("测试: need_result 标记")
    parser = StreamingParser()

    text = '<bash id="1" cmd="npm install" need_result="true" />'
    cmds = parser.feed(text)
    assert len(cmds) == 1
    assert cmds[0].need_result is True
    print(f"  ✓ bash#{cmds[0].id} need_result=True")

    # 没有 need_result 的
    parser2 = StreamingParser()
    text2 = '<bash id="1" cmd="mkdir test" />'
    cmds2 = parser2.feed(text2)
    assert cmds2[0].need_result is False
    print(f"  ✓ bash#{cmds2[0].id} need_result=False（默认）")
    print()


def test_edit():
    """测试：edit 命令"""
    print("测试: edit 命令")
    parser = StreamingParser()

    text = '''<edit id="1" path="./app.py">
<old>print("hello")</old>
<new>print("hello world")</new>
</edit>'''

    cmds = parser.feed(text)
    assert len(cmds) == 1
    assert cmds[0].tool == "edit"
    assert cmds[0].old_text == 'print("hello")'
    assert cmds[0].new_text == 'print("hello world")'
    print(f"  ✓ edit#{cmds[0].id}: old={cmds[0].old_text!r}")
    print()


def test_incomplete_command():
    """测试：不完整命令（流截断）"""
    print("测试: 不完整命令（flush 检测）")
    parser = StreamingParser()

    # 只喂了开始标签，没有结束标签
    text = '<write id="1" path="./test.md">\n正文内容'
    cmds = parser.feed(text)
    assert len(cmds) == 0, "不应提取到不完整命令"

    error = parser.flush()
    assert error is not None, "flush 应检测到不完整命令"
    assert "未闭合" in error.message
    print(f"  ✓ flush 检测到: {error.message}")
    print()


def test_id_duplicate():
    """测试：id 重复"""
    print("测试: id 重复")
    parser = StreamingParser()

    text = '''<write id="1" path="./a.txt">A</write>
<write id="1" path="./b.txt">B</write>'''

    cmds = parser.feed(text)
    assert len(cmds) == 1, f"重复 id 应只提取 1 条，得到 {len(cmds)}"
    assert len(parser.errors) > 0
    print(f"  ✓ 重复 id 被拒绝: {parser.errors[0].message}")
    print()


def test_chinese_content():
    """测试：中文正文"""
    print("测试: 中文正文")
    parser = StreamingParser()

    text = '''<write id="1" path="./novel/ch01.md">
天空灰蒙蒙的，主角站在十字路口。
红绿灯闪烁着，像某种倒计时。
她深吸一口气，迈出了第一步。
</write>'''

    cmds = parser.feed(text)
    assert len(cmds) == 1
    assert "天空灰蒙蒙的" in cmds[0].content
    assert len(cmds[0].content) > 20
    print(f"  ✓ 中文正文提取: {len(cmds[0].content)} 字符")
    print()


def test_attributes_any_order():
    """测试：属性顺序不固定"""
    print("测试: 属性顺序不固定")
    parser = StreamingParser()

    text = '<write path="./test.md" id="1" need_result="true">x</write>'
    cmds = parser.feed(text)
    assert len(cmds) == 1
    assert cmds[0].id == "1"
    assert cmds[0].path == "./test.md"
    assert cmds[0].need_result is True
    print(f"  ✓ 属性乱序正确解析")
    print()
def test_attribute_contains_gt_and_single_quotes():
    """测试：属性值包含 > 且支持单引号。"""
    print("测试: 属性中的 > 和单引号")
    parser = StreamingParser()

    text = "<bash id='1' cmd='python -c \"print(1 > 0)\"' need_result='true' />"
    cmds = parser.feed(text)
    assert len(cmds) == 1
    assert cmds[0].cmd == 'python -c "print(1 > 0)"'
    assert cmds[0].need_result is True
    print("  ✓ 属性值中的 > 不截断开标签")
    print()


def test_open_tag_split_inside_quoted_attribute():
    """测试：开标签在带引号属性内跨 chunk。"""
    print("测试: 开标签属性跨分片")
    parser = StreamingParser()

    chunks = [
        '<bash id="1" cmd="python -c \\"print(1 ',
        '> 0)\\"" need_result="true" />',
    ]
    cmds = []
    for chunk in chunks:
        cmds.extend(parser.feed(chunk))
    assert len(cmds) == 1
    assert '1 > 0' in cmds[0].cmd
    print("  ✓ 属性中跨分片的 > 被正确处理")
    print()


def test_special_chars_in_content():
    """测试：正文中包含特殊字符"""
    print("测试: 正文中的特殊字符")
    parser = StreamingParser()

    text = '''<write id="1" path="./code.py">
def hello():
    # 这里有引号 "test"
    # 这里有尖括号 <span>
    # 这里有反斜杠 C:\\\\Users
    pass
</write>'''

    cmds = parser.feed(text)
    assert len(cmds) == 1, f"期望 1 条，得到 {len(cmds)}"
    assert '引号' in cmds[0].content
    print(f"  ✓ 特殊字符正文提取成功")
    print()


def test_large_content():
    """测试：大正文"""
    print("测试: 大正文（模拟 1 万字）")
    parser = StreamingParser()

    # 模拟 1 万字的章节
    paragraph = "这是一段文字，用来测试大正文的解析。"
    content = "\n".join([paragraph] * 500)  # ~1.5 万字

    text = f'<write id="1" path="./big.md">\n{content}\n</write>'
    cmds = parser.feed(text)
    assert len(cmds) == 1
    assert len(cmds[0].content) > 5000
    print(f"  ✓ 大正文提取: {len(cmds[0].content)} 字符")
    print()


def test_mixed_thinking_and_commands():
    """测试：thinking 文本和命令块混合"""
    print("测试: thinking 文本与命令块混合")
    parser = StreamingParser()

    text = '''我来分析一下需求。

首先需要创建项目目录结构。然后写入口文件。

让我开始：

<mkdir id="1" path="./project/src" />

目录创建好了，接下来写主文件。

<write id="2" path="./project/src/main.py">
def main():
    pass
</write>

还需要一个配置文件。

<write id="3" path="./project/config.json">
{"name": "myproject"}
</write>

好，基础结构完成了。'''

    cmds = parser.feed(text)
    assert len(cmds) == 3, f"期望 3 条命令，得到 {len(cmds)}"
    assert cmds[0].tool == "mkdir"
    assert cmds[1].tool == "write"
    assert cmds[2].tool == "write"
    print(f"  ✓ 从混合文本中提取 3 条命令")
    print()


def run_all():
    """运行所有测试。"""
    print("=" * 50)
    print("ThinkFlow Parser 测试")
    print("=" * 50)
    print()

    test_single_write()
    test_streaming_write()
    test_multiple_commands()
    test_self_closing()
    test_append_touch_copy()
    test_read_command()
    test_need_result()
    test_edit()
    test_incomplete_command()
    test_id_duplicate()
    test_chinese_content()
    test_attributes_any_order()
    test_attribute_contains_gt_and_single_quotes()
    test_open_tag_split_inside_quoted_attribute()
    test_special_chars_in_content()
    test_large_content()
    test_mixed_thinking_and_commands()

    print("=" * 50)
    print("全部通过 ✓")
    print("=" * 50)


if __name__ == "__main__":
    run_all()
