# 配置基础设施定向验收 · 2026-11-11

独立审查对象：基线 `74c3922` 后未提交的配置层、CLI、公共 runtime 与桌面高级配置节点。未调用远程模型，未读取真实密钥或修改用户全局配置。

## 问题与修复复核

| 级别 | 初次发现及实际证据 | 定向复核 |
|---|---|---|
| P1 | 无工作区时 `DesktopService.configure({'context': {'files': 7}})` 成功；随后 `resolve_system_prompt` 抛 `TypeError: 'int' object is not iterable`。空 system_prompt 的配置预览绕过了 context 字段实际消费。 | 已修。共享验证拒绝 `files=7`、`max_chars='bad'`、`enabled='false'`，服务原配置保留。真实 CLI 无效保存退出 2。 |
| P2 | 分层覆盖 `native_tools=[]`、`thinking_mode='disabled'` 后，当前 provider 正确，但活跃 provider_profiles 仍保存原 `['read']` / `'enabled'`，切换回来会恢复旧值。 | 已修。实测活跃 profile 摘要与最终空工具列表、thinking_mode、reasoning_effort 一致。 |
| P2 | CLI `--set providers.a.model=new --doctor` 的 nested model 为 new，实际平铺 model 仍 old；空配置下仅 `--set model=new` 在应用覆盖前退出 1。 | 已修。调用真实 `cli.main`，仅替换配置 fixture、SessionStore 和 doctor 观察出口，两种场景都得到最终 model=new 并退出 0。 |
| P2 | 文件只提供 providers.a，环境指定 `THINKFLOW_ACTIVE_PROVIDER=a` 时，active_provider 来源误标文件。 | 已修。实际解析来源为 `env:THINKFLOW_ACTIVE_PROVIDER`。 |

## 独立实测与实物检查

- 使用专用 THINKFLOW_HOME、项目目录及桌面 userdata，走 `python -B -m src.cli config` 子进程验证 global → legacy project → .thinkflow project 合并；false、0、null、空列表保留各自语义。
- `config show --config` 隔离全局配置；show 不出现测试密钥。无效 max_read_chars 保存退出 2，原配置字节不变。
- 桌面真实服务初始化、打开专用工作区、修改配置：运行中的 configure 被拒，agent 与 session 保持；空闲修改保留 session，更新实际 agent；完全替换 runtime 字段后，max_retries 与 compaction 恢复核心默认。
- 服务普通状态不出现测试密钥。公共 runtime 的独立 import 不载入 src.cli。
- 直接检查 `desktop/temp/runtime-config-1000.png`：高级配置展开后编辑区、字段提示与固定保存栏均可见，无重叠；长字段正常换行，编辑区拥有独立滚动空间。该项复用本轮 Electron 截图，不冒称另做了 GUI 自动化。
- 检查配置预览调用链：使用 build_agent_config 的空 system_prompt 分支，未构造 provider HTTP 客户端、调用模型发现或读取工作区指引。

## 结论

**PASS**：本节点发现的四项配置问题及下述 token 预算账本边界均已修，原失败路径与直接影响已独立复核。

审查临时文件仅位于 `temp/configuration-qa-20261111`，已核对绝对路径与文件清单并送回收站。本人创建的服务与 asyncio task 已关闭；未启动常驻进程。

## token 预算追加验收

### [P2] 预算压缩后的本轮账本起点失效——已修

初次代码将 `record_start_index` 设在 pending observation 注入之后、预算强制压缩之前。第二次压缩可能删除刚注入的旧回执，使该起点偏大，漏掉本轮记录及依赖这些记录的统计、交付检查。

独立复现：4097 条回执中首条未注入，其余已注入；首次保留 4097 条，注入后记录起点 4097，预算压缩只留 4096 条。此后增加一个新 write，旧起点对应的本轮切片为空。

修复后起点移到预算压缩和请求体重建完成之后。使用实际 AgentLoop、MockTransport 和流式 tf-write 复核：发送请求时旧回执保留 4096 条且已归档 1 条；只发 1 次请求，实际 qa.txt 内容为 hello，commands_executed=1、delayed_successes=1，末条回执 success。本轮写入被正确计数。

### 独立请求与保留边界

- 预算 0、enabled=false 各发送 1 次请求，无预算事件、无压缩。
- 开启 3000 触发阈值，在注入大型 pending observation 后，实际请求估算从 19262 降至 10453，仅发送 1 次请求，完整当前中文要求保留。
- last_input_tokens_estimate 与捕获的实际请求体 messages/system/tools 紧凑 JSON UTF-8 字节数独立计算一致；模拟供应商报告的 prompt_tokens=123 原样保留，不混入估算。历史 100 万 prompt_tokens 不参与当前预算判断。
- OpenAI 与 Anthropic 双工具调用批次，在 keep_recent_tokens 为 1、100、250、500、10000 时都完整保留调用与结果。
- 源码核对：generation options 不计入输入估算；预算触发只做一次本地强制压缩并重建请求，不额外请求模型摘要。

触发阈值不等于硬上限。上述开启案例压后仍为 10453，原因是末条大 observation 按末条完整保留规则留下；本报告不据此宣称真实任务 token 效率百分比。

追加检查只使用 MockTransport。专用 `temp/token-budget-qa-20261111`、`temp/ledger-budget-qa-20261111` 均在每轮结束后核对并送回收站，全部 AgentLoop/HTTP 客户端已关闭。
