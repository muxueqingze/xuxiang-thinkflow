# F11：Anthropic 内容块的接收与回传

2026-11-11，基线 `d60024c`。实际实现位于 `src/agent_loop.py`，定向回归位于 `tests/test_anthropic_roundtrip.py`。

## 契约与收益

官方 [Thinking](https://platform.claude.com/docs/en/build-with-claude/thinking) 规定工具续轮须完整回传 thinking；omitted 模式允许 `thinking` 为空，`signature` 与 redacted thinking 的 `data` 都是不透明字段。[Streaming messages](https://platform.claude.com/docs/en/build-with-claude/streaming) 定义 start → delta → stop 的内容块生命周期及签名增量。

本次保存 API 实际返回的摘要、空 thinking、签名和 redacted 内容，不生成、解码或推测未返回的原始思维链。收益是工具续轮和恢复请求不再丢失供应商提供的协议状态，不增加模型请求。

## 实现

- SSE 消费链按 index 组装 thinking、signature、text 和 tool input；只在 `content_block_stop` 后将完整块纳入消息。签名分片连接，redacted 原字段保存。
- assistant 原始内容块按响应顺序保存，包括 thinking/text/tool_use 交错。可见文本仍走现有过滤器；不会把过滤后的摘要与原签名重新拼装。
- 修复既有交错流缺陷：看到 tool_use 之后，后续 thinking/text 不再被误判为需要立即截流。
- 原生工具开始执行前先登记完整非 tool_use 内容块，使工具事件触发的快照已包含签名。批次结算后在同一 assistant 位置替换完整内容并追加结果；取消时保留原先前缀，避免孤立工具调用。
- 截断、连接中断、流错误、显式结果边界及取消均不回放未闭合块。未执行原生批次不进入历史；已执行文本操作沿用账本。
- 已闭合但参数不是合法 JSON 对象的工具仍进入既有参数校验：不执行工具，记录 `Invalid tool arguments` 失败回执；回传配对工具块使用既有的空对象占位。合法完整工具输入保持原样。
- 同协议发送保持原块；切到 OpenAI 的发送投影不携带这些字段，存储历史不变。近期工具对在压缩后仍完整保留。

没有更改 provider 配置、提示词、模型参数或登录状态。`tests/test_message_projection.py` 的一处 Anthropic fixture 补全了原先缺少的 start/stop 事件；不为不完整 fixture 添加运行兼容分支。

## 验证

本地 `httpx.MockTransport` 使用规范 SSE，工具实际操作专属临时工作区。四个测试覆盖：

1. 空 thinking、签名分片、redacted、非空摘要、正文与两个 read 交错；正文 tf-touch 实际创建文件但不出现在可见正文；四个原生工具事件的快照均已有原始签名；压缩、SessionStore 写盘/读取、恢复后实际下一次 HTTP 请求保持完整原块。
2. 原生工具执行期间取消：取消账本存在，签名前缀保留一次，没有孤立 tool_use。
3. 五种流边界：取消、max_tokens、连接中断、error、need_result；恢复后的实际 HTTP 请求仅含闭合块，未执行原生工具没有产生文件或回执。
4. 已闭合工具的非法 JSON 和非对象 JSON：失败账本、错误内容、下一次 HTTP 请求的配对工具结果保持既有契约。

首轮 F11 与消息投影组合 12 项通过（11.606 秒）；此前 harness、可选输出预算及 round2 核心合计 51 项通过。独立质检指出非法参数会提前抛错、need_result 夹具实际走 EOF 两项，已修复。原失败项及直接影响复核为四项 F11 加原有非法参数回归，共 5 项通过（7.504 秒）；need_result 现在明确断言真实中断原因和成功 read 回执。本项没有调用商业模型端点。

```powershell
python -X utf8 -c "import sys, unittest; sys.path.insert(0, 'tests'); r=unittest.TextTestRunner().run(unittest.defaultTestLoader.loadTestsFromNames(['test_anthropic_roundtrip', 'test_message_projection'])); sys.exit(not r.wasSuccessful())"
```

临时工作区在 `temp/native-roundtrip-20261111/` 内逐测试释放；无常驻测试服务。正常工具续轮与明确中断恢复已验证；供应商实际端点验收由后续完整任务实测覆盖。
