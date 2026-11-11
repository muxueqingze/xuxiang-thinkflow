# 0.9.0 核心运行链定向验收

2026-11-11。独立核对 F11（Anthropic 内容块往返）和 F12（增量持久化与会话索引）。源码基线为 `d60024c` 后本轮工作区改动；不包含桌面视觉、安装包和 Pro 版本级终审。

## 问题及复核

1. **P1，Anthropic 非法工具 JSON 绕过已有失败回执，已关闭。** 初版 `src/agent_loop.py` 的 `content_block_stop` 直接执行 `json.loads`。用规范 SSE 生命周期、已闭合 `tool_use` 和 `partial_json="{bad"` 独立复现：抛出 `JSONDecodeError`，账本为 0，历史仅有原始用户消息。已有 `tests/test_harness.py::test_invalid_native_json_does_not_execute` 要求返回 `Invalid tool arguments` 并保存失败回执。修复后原始参数仍进入既有校验器；实际复核 `{bad` 和 `[1]` 都产生失败账本，下一次 HTTP 请求保留签名块、合法配对的工具调用及 `is_error=true` 结果，不执行工具。
2. **P2，need_result 测试未命中声明场景，已关闭。** 初版 `test_boundaries_replay_only_closed_blocks_and_do_not_execute_native_batch` 的 need_result 分支实际输出 `Stream ended without a finish reason or completion marker`。原断言仅检查原生调用未执行和未闭合块未回传，未检查文本 read 成功或 `NEED_RESULT`，因此该子场景绿色不能证明信息反馈边界。夹具补上换行后命中正文命令 gate；实际复核输出 read#2 成功、20 bytes 和工具结果注入，新增中断原因及成功账本断言通过。
3. **P2，CLI 列表使用旧 checkpoint 时间排序。** 初版 `SessionStore.discover` 仅使用主 JSON 文件 mtime，工具事件改为追加 JSONL 后，活跃会话可能排在旧会话后面。现已改为 `inspect().mtime`，取 checkpoint 与增量文件的较新时间。独立探针将 checkpoint 时间设为旧、增量时间设为新，确认该会话排第一，关闭。

## 实际检查

- 亲自运行 `test_anthropic_roundtrip` 与 `test_message_projection` 的 12 项检查，全部通过；上述 need_result 证据缺口来自检查实际日志，而非测试失败。
- F12 运行 10 项定向检查：`test_session_incremental` 的 5 项；CLI 真实进程在工具完成后被终止并恢复、未完成意图被终止后恢复；SDK 会话切换/分支；桌面会话恢复/分支/工作区隔离；桌面真实 localhost SSE 工具操作及回执恢复。10 项通过，耗时 8.084 秒。
- 索引后缀由 `.index.json` 改为 `.index` 后，只重跑受影响的 CLI 列表和桌面列表两项，均通过。`*.json` 保持只表示完整快照的既有约定。
- F11 两项修复后，仅运行非法参数、五种中断边界、原始完整块往返及既有 harness 非法参数契约，4 项均通过，耗时 12.567 秒。原始完整块场景覆盖本次参数传递修改的直接影响。
- 独立 F11/F12 交叉探针绑定真实 `SessionStore`，执行 Anthropic 空 thinking + opaque signature + redacted 块 + 原生 read。`tool_completed` 事件后从磁盘新建 store 并 load，确认轮开始 checkpoint 的字节未被工具事件重写，JSONL 已保存原始签名前缀及成功 read 回执。
- 从上述工具事件的磁盘状态恢复新 Agent，实际发送下一次 HTTP 请求：两个 opaque 块逐字段相同，read 原文 `independent-result` 随待反馈回执注入，没有孤立 `tool_use`。分支追加 `branch-only` 后，源 journal 字节不变、源消息中无分支输入。
- F12 已有半行追加、checkpoint 替换后旧 journal 仍存在、legacy JSON 升级、同一 store 切换路径、保存后内存继续变动等契约检查均实际执行。检查覆盖原消息、记录和桌面 transcript，不只比较字段数量。

运行命令使用 `python -X utf8`，通过 `sys.path.insert(0, 'tests')` 加载本仓库测试。首次尝试 `python -m unittest tests.*` 因 tests 不是可导入包而未执行，已改正确入口；该次不计入通过数。交叉探针最初在轮结束后比较 checkpoint，因正式轮结束会完整保存而失败；断言已移至 `tool_completed` 回调的实际增量边界，之后通过。

## 收益证据范围

已核对 `bench/persistence_20261111/run.py` 和 `docs/evidence/persistence-20261111-before.json`、`after.json`：相同约 2.7 MB 历史、40 次回执更新，测量的是落盘字节、保存耗时、列表读取量，并检查恢复后的历史与回执相等。测量显示减少全量序列化和读取的价值；本验收没有以保存速度推导模型 token 或任务产物质量，也没有重复运行整轮性能实验。

## 当前裁定与清理

F11、F12：**PASS**。上面三项均已关闭，无本范围未决问题。版本级仍交由 Pro 终审。

本轮探针集中在 `temp/release-runtime-quality-20261111/`，未调用商业模型、未使用真实桌面用户数据与凭据。所有 Agent 客户端、测试 HTTP server 和 CLI 子进程均通过测试收尾关闭；最终已确认专属目录为空、无匹配本轮专属目录或长时测试命令的 Python 进程。空目录按工作区规则送入回收站；未清理其他任务目录。
