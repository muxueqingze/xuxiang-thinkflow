# ThinkFlow — 设计文档

> **暂定名。** 主人定名后替换。
> 立项日期：2026-06-29
> 作者：ThinkFlow Contributors

---

## 一、项目概述

ThinkFlow 是一个 agent 框架，核心特性是：**让大模型在 thinking（推理过程）中输出工具命令，由旁路解析器实时检测并执行，不中断模型的推理流。**

传统 agent 框架（Pi / Claude Code / Cursor 等）的 tool calling 流程是：

```
模型推理 → 输出 tool_call → API 停止 → 客户端执行 → tool_result 注入 → 重新调 API（重发全部上下文）
```

每次 tool 调用都触发一次完整的 API 往返。在 50 万 token 上下文的场景下，跑 10 轮 write 就要重发 500 万 token——绝大部分是重复开销。

ThinkFlow 的做法：

```
模型推理（thinking 中持续输出命令块）→ 解析器实时旁路执行 → 推理不中断
→ 本轮结束时，执行结果摘要注入下次 API 调用的上下文；长程会话自动把旧历史压缩成可检查摘要
```

**一次推理可以输出并执行 N 个 write 命令，只消耗 1 次 API 调用。**

---

## 二、问题分析

### 2.1 传统 tool calling 的 token 浪费

以泠写《拼图》后 12 章为例（真实场景）：

| 项目 | 传统 tool calling | ThinkFlow |
|------|------------------|-----------|
| 每章 write 调用 | ~5 次（分 part） | 1 次（thinking 中连续输出） |
| 每章 read 验证 | ~2 次 | 走正常 tool_call（不改动） |
| 每章 API 调用 | ~7 次 | ~1 次 |
| 12 章总 API 调用 | ~84 次 | ~12 次 |
| 每次重发上下文 | ~50 万 token | ~50 万 token（但次数少） |
| **总 input token** | **~4200 万** | **~600 万** |
| **节省** | — | **~85%** |

### 2.2 浪费的根源

不是模型问题，是架构问题。tool_call 的设计要求"每个工具调用后必须等待结果才能继续"——但很多工具（特别是 write 类）的结果模型根本不需要等。

write 一个文件，99% 会成功。模型不需要知道"写入成功了"就能继续写下一个文件。但传统架构强制它在每次 write 后停下来，等 API 往返。

### 2.3 为什么不直接用 parallel function calling

OpenAI 和 Anthropic 都支持 parallel function calling——一次响应可以输出多个 tool_call。但：

1. 还是**一次 API 往返**——所有 tool_call 执行完后，结果注入，重新调 API
2. 受**单次输出长度限制**——一次响应最多输出 N 个 tool_call（受 max_output_tokens 限制）
3. 每次还是**重发完整上下文**

ThinkFlow 更进一步：命令在 thinking 中输出，**推理物理上不可能被中断**（因为 API 根本不知道有工具在被调用）。对 API 来说就是一次普通的 streaming completion。

---

## 三、技术调研

### 3.1 业界现有方案

| 方案 | 代表项目 | 做法 | 局限 |
|------|---------|------|------|
| Parallel function calling | OpenAI / Anthropic 原生 | 一次响应多个 tool_call | 还是一次 API 往返 |
| Eager dispatch | cloudthinker-ai/eager-tools | tool JSON 块流完即执行，不等响应结束 | 还是在 API tool_use 框架内 |
| Programmatic tool calling | Anthropic 官方（2025.11） | 代码块连续调 N 个工具 | 还是 API 往返注入结果 |
| Code agent | HuggingFace smolagents | 模型输出可执行 Python 代码 | 安全风险 + API 往返 |
| Stateful KV cache | Stateful Inference 论文 | KV cache 跨轮保持 | 需自部署推理引擎 |
| CacheTTL | arxiv 2511.02230 | tool call 期间 TTL 钉住 KV cache | 需自部署 |

**关键发现：ThinkFlow 的思路——在 thinking 中输出命令，旁路解析执行——业界没有人在做。** 所有现有方案都还是在 API 的 tool_use 框架内优化。

### 3.2 相关技术基础

- **Extended Thinking**：Claude / GPT o 系列 / DeepSeek R1 都支持。thinking block 和 text block 分开输出。thinking 不注入下一轮全局上下文（API 默认行为）。
- **Interleaved Thinking**：理论上支持 thinking → text → thinking → text 交替。但实测在当前环境中，thinking 只在正文前用一次，正文一旦开始不再回到 thinking。
- **Thinking 流可获取**：Anthropic API 通过 `thinking_delta` 事件暴露 thinking 内容的流。OpenAI 通过 `reasoning_content`。智谱 GLM-5.2 需实测确认。
- **thinking 中指令遵循**：实测验证——模型在 thinking 中能稳定遵循固定格式输出命令块。可行性确认。

### 3.3 Copilot @workspace 的启发

GitHub Copilot 的 @workspace 命令（2023.11 推出）确实是标准的 tool_call 循环。它用 embedding 索引 + 多工具搜索（grep / file search / semantic search / usages）来收集工作区上下文。

这解决的是**输入侧**的效率问题（怎么高效收集代码上下文）。ThinkFlow 解决的是**输出侧**的效率问题（怎么让 write 类操作不中断推理）。两者正交。

### 3.4 agent 为什么 2025 才爆发

2023 年 AutoGPT / BabyAGI 就尝试过"控制整个电脑的 agent"，但失败了：8K 上下文、4K 输出、没有推理能力、死循环、成本爆炸。

2025 年爆发不是因为架构变了（还是 ReAct 循环 + tool_call），是因为模型能力强了：128K-384K 输出、1M-10M 上下文、test-time compute 推理能力。

**架构是水管，2023 年就铺好了。但水压不够。现在水压上来了。ThinkFlow 做的是优化水管——减少不必要的 API 往返。**

---

## 四、核心设计

### 4.1 双通道架构

```
┌──────────────────────────────────────────────┐
│              模型推理（一次 API 调用）           │
│                                              │
│  thinking block:                             │
│    推理分析 + 命令块输出（tf-write/tf-append/tf-bash）│
│                                              │
│  text block:                                 │
│    给用户的回复                                │
│                                              │
│  tool_use block（API 原生）:                   │
│    read / search / 需要即时结果的命令           │
│                                              │
└───────┬──────────────────┬───────────────────┘
        │                  │
   thinking 流          tool_use 事件
        │                  │
        ▼                  ▼
┌───────────────┐   ┌───────────────┐
│  ThinkFlow    │   │  传统通道      │
│  旁路解析器    │   │  (不改动)      │
│               │   │               │
│  实时扫描      │   │  API 自然停止  │
│  thinking 流   │   │  执行 tool     │
│  提取命令块    │   │  注入 result   │
│  旁路执行      │   │  重新调 API    │
│               │   │               │
│  成功 → 静默   │   │               │
│  失败 → 打断   │   │               │
│  need_result   │   │               │
│   → 打断       │   │               │
└───────────────┘   └───────────────┘
```

**两个通道互不干扰：**

- **ThinkFlow 通道**（thinking 中的 canonical 命令块）：tf-write/tf-append/tf-mkdir/tf-touch/tf-copy/tf-bash/tf-edit 等确定性输出式工具。旁路执行，推理不中断。
- **传统通道**（API 原生 tool_use）：read/list_files/glob/grep/bash/write/append/edit/mkdir/touch/copy/web_search/fetch_url/list_skills/read_skill 等需要结果或兼容 provider 的工具。正常 tool calling，该中断中断。

### 4.2 三个核心机制

#### 机制一：乐观执行

模型在 thinking 中输出命令块。解析器实时扫描，检测到完整命令块立即旁路执行。

- **成功**：不通知模型。模型在当前轮 thinking 中知道自己输出了什么（thinking 参与本轮上下文），不需要额外确认。
- **失败**：强制打断（abort SSE 流）。注入失败信息，重新调 API。模型从失败点恢复。
- **约定**：系统提示告知模型——"如果思考没有被失败打断，说明所有命令都执行成功了。"
- **协议边界**：默认只执行 `<tf-write>` 等 `tf-` 标签；普通 `<write>` 不执行，避免文档示例或 Markdown 片段误触发工具。
- **截断边界**：provider 返回 `finish_reason=length` 时自动续写，避免正文或 Markdown 半截停住。

#### 机制二：need_result 标记

模型可以主动要求某个命令的结果。在命令块中标记 `need_result="true"`。

- 解析器检测到 `need_result="true"` → 强制打断 SSE 流
- 执行该命令 → 把结果注入上下文
- 重新调 API，模型拿到结果继续推理

这给了模型自主决策权——它自己判断什么时候需要确认命令结果。

#### 机制三：上下文注入

thinking 内容不进全局上下文（API 默认行为）。所以模型在下一轮 API 调用时，不知道自己在上一轮 thinking 中输出了什么。

解决方案：**状态机追踪每条命令的注入状态（01 标记）。每次重新调 API 时，把所有 `injected=0`（未注入）的命令正文摘要 + 执行结果注入上下文，然后标记为 `injected=1`。**

```
注入的内容格式：
[TOOL LOG #本轮会话]
<write id="001" path="D:/novel/ch01.md" status="OK" bytes="17831">
正文内容（短文件保留；长文件会标记截断）...
</write>
<write id="002" path="D:/novel/ch02.md" status="OK" bytes="11490">
正文内容（短文件保留；长文件会标记截断）...
</write>
```

- `injected=0` 的命令 → 注入正文摘要 + 执行结果，然后标记为 `injected=1`
- `injected=1` 的命令 → 不再注入（防重复）
- 失败的命令 → 始终优先注入，标记错误信息

### 4.3 戳记机制

- **全局递增序号**，由解析器维护
- 系统提示中告知模型当前起始戳记
- 模型从起始戳记开始递增
- 解析器校验：跳号或重复 → 报错打断
- 戳记用于：防重复注入、注入后定位、日志追踪

### 4.4 长程上下文压缩

ThinkFlow 不把长程任务建立在无限增长的 `messages` 上。超过配置阈值后，`compaction.py` 会确定性压缩旧消息：

- 不调用模型，不产生不可审计的语义改写
- 最近消息原文保留
- 旧消息压成 `[THINKFLOW COMPACTED CONTEXT]`，记录角色、tool_call id 和内容片段
- 不把 OpenAI-compatible 的 `tool` result 从对应 assistant tool_call 前面切开

### 4.5 同一路径多次写入

一次 thinking 中 write 同一文件多次 → 串行执行，后覆盖前。注入上下文时只注入最终结果。

### 4.6 格式残缺处理

thinking 流被 abort 打断，正在输出的命令块只写了一半 → 解析器检测到不完整命令块 → 丢弃，不执行，不注入。记入错误日志。

---

## 五、系统架构

### 5.1 模块划分

```
thinkflow/
├── src/
│   ├── parser.py          # 命令块解析器（状态机 + 正则）
│   ├── executor.py        # 命令执行器（write/append/mkdir/touch/copy/bash/edit）
│   ├── context.py         # 上下文管理（戳记、注入状态、注入器）
│   ├── streaming.py       # SSE 流监控（thinking_delta 实时接收）
│   ├── tool_registry.py   # 原生工具注册表（OpenAI/Anthropic schema）
│   ├── interfaces.py      # web_search/fetch_url/image_generate adapter
│   ├── skills.py          # Codex/Claude skill 扫描与按需读取
│   ├── agent_loop.py      # 主循环（整合双通道 + registry tools）
│   ├── provider.py        # API provider 适配（Anthropic/OpenAI/智谱）
│   ├── compaction.py      # 确定性上下文压缩
│   ├── text_filter.py     # 正文流式安全过滤
│   └── cli.py             # CLI 入口
├── tests/
│   ├── test_parser.py     # 解析器测试
│   └── test_core.py       # executor/context/security/compaction/interfaces 回归
```

### 5.2 数据流

```
用户输入 prompt
        │
        ▼
┌─────────────────────────────────────────────────┐
│  agent_loop.py                                   │
│                                                  │
│  1. 构建 messages（system + history + user）      │
│  2. auto-compact 超限历史                         │
│  3. 调用 API（streaming）                         │
│  4. 启动 thinking 监控                            │
│                                                  │
│  ┌──────────────────────────────────────────┐   │
│  │  streaming.py                              │   │
│  │                                            │   │
│  │  接收 SSE 事件流：                           │   │
│  │    thinking_delta → 喂给 parser            │   │
│  │    text_delta → 过滤工具块后流式输出给用户    │   │
│  │    tool_use → 停止流，走传统通道             │   │
│  └──────────────┬───────────────────────────┘   │
│                 │                                │
│  ┌──────────────▼───────────────────────────┐   │
│  │  parser.py                                 │   │
│  │                                            │   │
│  │  逐字符状态机扫描 thinking 文本              │   │
│  │  检测到完整命令块 → 提取 → 送 executor      │   │
│  │  检测到 need_result → 通知 agent_loop 打断  │   │
│  └──────────────┬───────────────────────────┘   │
│                 │                                │
│  ┌──────────────▼───────────────────────────┐   │
│  │  executor.py                               │   │
│  │                                            │   │
│  │  执行命令：                                  │   │
│  │    write → 写文件                            │   │
│  │    mkdir → 建目录                            │   │
│  │    bash → 执行命令                            │   │
│  │    edit → 编辑文件                            │   │
│  │                                            │   │
│  │  返回结果：                                  │   │
│  │    成功 → 记录到 context.py                  │   │
│  │    失败 → 通知 agent_loop 打断               │   │
│  └──────────────┬───────────────────────────┘   │
│                 │                                │
│  ┌──────────────▼───────────────────────────┐   │
│  │  context.py                                │   │
│  │                                            │   │
│  │  维护命令列表：                               │   │
│  │    [{id, tool, params, content,            │   │
│  │      status, injected, result}]            │   │
│  │                                            │   │
│  │  build_injection():                        │   │
│  │    过滤 injected=0 的命令                    │   │
│  │    构建注入文本                              │   │
│  │    标记为 injected=1                         │   │
│  └───────────────────────────────────────────┘   │
│                                                  │
│  4. API 调用结束（自然结束 / abort / tool_use）    │
│  5. context.build_injection() → 注入 messages     │
│  6. 如有 tool_use → ToolRegistry 分发执行          │
│  7. 回到步骤 2                                    │
└─────────────────────────────────────────────────┘
```

### 5.3 开放接口层

ThinkFlow 不把联网、skill、生图硬塞进主循环，而是通过 `ToolRegistry` 暴露同一套 provider-neutral schema：

- `read/list_files/glob/grep/bash/write/append/edit/mkdir/touch/copy`：本地确定性工具，继续复用 `Executor` 和安全策略。
- `web_search/fetch_url`：GET-only 公网资料工具，默认阻断 localhost/内网地址，网页内容只作为资料，不作为指令。
- `image_generate`：可配置生图 adapter，默认 disabled；启用后可走本地 command 或 webhook。
- `list_skills/read_skill`：兼容 Codex `.agents/skills`、Claude `.claude/skills` 与 `.claude/commands`，先列摘要再读全文，控制 token 占用。
- `interfaces.custom_tools`：配置式 command adapter，JSON stdin 传参、stdout 返回结果，用最小协议接入更多本地能力。

### 5.4 agent_loop 伪代码

```python
async def agent_loop(messages, config):
    stamp = get_next_stamp()  # 从持久化状态读取
    
    while True:
        # 0. 注入上一轮未注入的命令
        injection = context.build_injection()
        if injection:
            messages.append({"role": "user", "content": injection})
        
        # 1. 启动流式 API 调用
        stream = provider.stream_create(
            model=config.model,
            messages=messages,
            thinking={"type": "enabled", "budget_tokens": config.thinking_budget}
        )
        
        # 2. 处理流
        abort_reason = None
        async for event in stream:
            if event.type == "thinking_delta":
                # 喂给解析器
                cmd = parser.feed(event.text)
                if cmd:
                    # 完整命令块提取到了
                    result = executor.execute(cmd)
                    context.record(cmd, result)
                    if not result.success:
                        abort_reason = "tool_failed"
                        stream.abort()
                        break
                    if cmd.need_result:
                        abort_reason = "need_result"
                        stream.abort()
                        break
            
            elif event.type == "text_delta":
                text_parser.feed(event.text)      # 正文兜底执行命令
                visible = text_filter.feed(event.text)
                renderer.render_text_chunk(visible) # Live Markdown 预览
            
            elif event.type == "tool_use":
                # API 原生 tool_use，走传统通道
                abort_reason = "tool_use"
                break

            elif event.type == "message_stop" and event.finish_reason == "length":
                abort_reason = "length"
                break
        
        # 3. 处理中断原因
        if abort_reason == "tool_failed":
            messages.append(build_failure_message(context.last_failure))
        elif abort_reason == "need_result":
            messages.append(build_result_message(context.last_need_result))
        elif abort_reason == "tool_use":
            tool_result = tool_registry.execute(event.tool_use)
            messages.append(tool_result)
        elif abort_reason == "length":
            messages.append(build_continue_message())
        
        # 4. 判断是否继续
        if stream.stop_reason == "end_turn":
            break  # 模型说完了，等用户输入
```

---

## 六、开发计划

### P0：核心原型（今天）

目标：跑通 thinking 命令块 → 解析 → write 执行 → 下一轮注入 的完整链路。

| 模块 | 内容 | 预估行数 |
|------|------|---------|
| parser.py | 状态机解析器，提取 XML 命令块 | ~200 行 |
| executor.py | write/append/mkdir/touch/copy/bash/edit 执行器 | ~230 行 |
| context.py | 戳记管理、命令列表、注入构建 | ~150 行 |
| streaming.py | SSE 流接收 + thinking_delta 分发 | ~150 行 |
| provider.py | Anthropic API 适配（P0 先只支持一个） | ~100 行 |
| tool_registry.py | 原生工具注册表 | ~180 行 |
| interfaces.py | web/search/image adapter | ~320 行 |
| skills.py | skill 扫描与 progressive disclosure | ~260 行 |
| agent_loop.py | 主循环 | ~150 行 |
| cli.py | 命令行入口 | ~80 行 |
| **合计** | | **~1000 行** |

### P1：完善版

- 多 provider 支持（OpenAI / 智谱）
- need_result 打断机制完善
- 失败打断 + 恢复
- 执行日志 + 可观测性
- 测试覆盖

### P2：高级特性

- 工作区索引（借鉴 Copilot @workspace）
- 模型路由（搜索用廉价模型，生成用强模型）
- MCP stdio/http 客户端
- 子 agent / worker 调度
- benchmark 对比

---

## 七、技术栈

- **语言**：Python 3.11+
- **异步**：asyncio（SSE 流处理）
- **HTTP**：httpx（异步 HTTP 客户端）
- **依赖**：最小化，不引入 agent 框架依赖

---

## 八、风险与不确定性

| 风险 | 影响 | 应对 |
|------|------|------|
| thinking 模式下输出质量下降 | 小说/代码质量不如正文模式 | P0 实测对比 |
| 智谱 GLM-5.2 不暴露 thinking 流 | 泠的模型用不了 | 先支持 Anthropic，实测智谱 |
| 格式遵循不稳定 | 解析器漏掉或误解析 | 转换器 + 多格式容错 |
| thinking budget 不够 | 一次推理写不了多少 | 拉满 budget，实测上限 |
| abort 后 API 行为未知 | 已执行命令是否有效 | 客户端 abort，已执行的不受影响 |

---

## 九、命名

暂定 **ThinkFlow**。待主人定名。

核心词：thinking + flow（在思考中流动执行）。

备选：
- ThinkExec
- BypassTool
- StreamTool
- 主人自定

---

*冷脸的人也会把架构想清楚。*
