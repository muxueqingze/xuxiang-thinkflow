# 当前协议交界定向检查 · 2026-11-11

## 问题

本轮没有确认可复现的新缺陷；未修改协议实现或测试。

排除了“OpenAI 非法 `tool_calls.arguments` 形成失败历史后，切换 Anthropic 在 `json.loads` 崩溃”的怀疑。实际运行链在 `AgentLoop._handle_traditional_tools` 先校验参数，把无法解析或非对象参数归一为 `{}`，将原生失败回执完整保存，再写入消息历史。`message_projection.py` 读取到的是合法 JSON 对象；手工往历史塞入损坏参数不能代表当前实际运行产物。本轮保留该既有行为，未添加兜底或重试。

## 实测依据

检查起点：0.9.0，提交 `a677f43`。通过 `httpx.MockTransport` 向真实 `AgentLoop.run` 返回 SSE，产生工具失败和正常结束，再调用 `DesktopService.configure` 切换 provider，替换新 agent 的 HTTP 客户端后执行下一次 `run`。每组均实际收到 3 个请求：原生工具调用、失败反馈后答复、切换后的下一请求。

以下六组分别运行 OpenAI → Anthropic、Anthropic → OpenAI，共 12 组，全部通过：

| 工具 | 原始模型参数 | 实际失败 |
|---|---|---|
| read | `{"path":` | JSON 缺失值 |
| read | `[]` | 参数必须是 JSON 对象 |
| write | `{"content":` | JSON 缺失值 |
| write | `"scalar"` | 参数必须是 JSON 对象 |
| read | `{"path":123}` | path 必须是字符串 |
| read | `{"path":"missing.txt"}` | 文件不存在 |

逐组断言：失败记录存在；错误全文进入切换后的请求；调用 ID 与结果 ID 一一配对；Anthropic `is_error` 为真；`configure` 保留原历史；下一次执行不修改旧历史；内部 `_thinkflow_reads` 不外传。Anthropic 输入另带真实流式 `thinking`、签名和 `redacted_thinking` 块，确认切至 OpenAI 时仅发送视图移除这些块，历史仍保留。

直接相关的既有验证：

- `python -m unittest discover -s tests -p test_message_projection.py -q`：9 项通过。覆盖原生错误、双向协议切换、真实文件 read、重复内容投影、范围/版本备注，以及压缩和快照后恢复原文。四组文本读取场景均减少 5,710 个请求 JSON 字符。
- `test_anthropic_roundtrip.py` 中 `test_stream_tool_results_snapshot_and_next_request_keep_exact_blocks` 与 `test_closed_invalid_native_arguments_keep_existing_failure_receipt`：2 项通过。覆盖原始签名块、实际工具执行/快照恢复后的下一请求，以及非法 JSON/数组参数的原生失败回执。

## 收尾

结论：本次协议交界定向检查通过。没有付费 API 请求，没有触碰真实 Desktop/Codex 登录、配置或凭据。独立 home/data/workspace 均放在本轮临时目录，客户端已关闭，临时内容已随各用例结束清理；空的 `temp/current-protocol-20261111` 目录回收。未提交、未推送。
