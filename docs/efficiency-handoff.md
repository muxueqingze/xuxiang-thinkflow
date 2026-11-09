# ThinkFlow 执行效率 · 当前接力

更新：2026-11-09。分支 `codex/thinkflow-efficient-execution`，本地实现提交`a405f95`；实现与验收已收口，未推送。本分支交付源码与报告，旧桌面包未重打。

## 目标

保持工程能力，减少反复发送的上下文和cache输入。继续使用原FIFO流式文件执行与信息反馈，参考Pi；不削减输出/思考预算，不删必要反馈，不加验收门禁。

## 已可用实现

- 上轮未提交的verify版本门禁已撤出；runtime/ledger合入末尾observation，启动不再强制读skill。
- 未反馈载荷完整保留；先压缩历史，再精确去重已存在的成功tf正文。失败/恢复输出完整且不重复。
- 修复同一SSE分片末尾格式错误吞掉前面合法命令（TF-03）。
- OpenCode Go自有UA与稳定session随快照恢复；Python编码变量正常传给子进程。
- 新增原生 `python(code)`：stdin多行代码，无shell转义/临时脚本，共用既有进程输出、权限、超时、取消。独立100KB/Unicode/取消父子进程/授权/失败batch检查通过；code非字符串回执P2已修。
- 请求级原生read去重：完整相同正文可引用当前保留的write参数或首份read；本地messages/UI原文不变，追加ledger保留。每请求重算，压缩移走来源后恢复全文。`output_chars`用于正文边界；`context_projection`事件记录字符减少量。
- DeepSeek正文末尾DSML原生工具残片走原有协议错误恢复；先剥离已完成tf文件块并排除代码围栏，已有native批次不改。识别末尾parameter/invoke/calls控制标记，包括真实出现的单个calls尾；不自行执行DSML。
- 默认首轮保留read/write/edit/bash/python/read_skill/load_tools；其余工具从同一registry按需开放schema，会话及快照保存已加载名称。目录保留名称与用途，遵循显式allow−deny，不删除工具能力。此前隐藏全部六个文件工具的实验已撤销，write/edit首轮可用。
- Windows工具说明已统一到python(code)，移除要求先写临时脚本的旧冲突说明。
- 实测frame的恢复被旧3次连败阈值截停；`max_consecutive_failures`已改为默认null、显式数字可配。工具错误继续原反馈；unknown副作用仍先停止。独立默认4失败后恢复、显式3、成功重置和配置透传复核通过。

## 最终实测

装置复用 `bench/efficiency_20261011/`，实际Go端点 `https://opencode.ai/inference/go/openai/v1`，模型 `deepseek-v4.1-flash`，high，无额外输出/轮数/时长cap；自动压缩关闭。凭据仅进程环境/内存；区域Global由主人在网页完成。

最终`go-recovery-20261109`三题各8/8、实际公开检查及独立实物核验通过；31请求/cache321408/非cache71576/输出40954/总433938。旧2a4f4b8为32请求/cache382592/总542220；Pi1.0.1为27/cache187776/总249511。当前相较旧版合计缓存−15.99%、总token−19.97%；frame单题缓存+33.37%，Pi仍更省。

最终组三题全部走原生批次，流中写入/read投影/DSML恢复事件均0，不能据此归因流式省量。全部探索、失败、取消与9条缺usage保留；28条状态及9份比较的90条请求已独立核对。完整过程与数据见[结果报告](efficiency-results-20261109.md)和[机器证据](evidence/efficiency-20261109/comparison.json)。无活动实验。

## 验证与下一步

- 完整parser19/core82及238项unittest通过，见 `docs/evidence/efficiency-20261109/final-lazy-python.txt`；随后失败预算小修仅复验直接影响的13项budget与28项harness，均通过。
- 桌面原13项通过；独立定向实现审查、最终产物与计量验收见[审查记录](reviews/efficiency-20261109.md)。检查已收口，不自动再跑全量。
- 后续明确缺口：frame自建检查修订与原生逐步调用的成本；这组三题合计改善，但Pi仍更省，流式机制需按实际任务是否使用来评价。

## 保留与清理

- 旧可选delivery TF-01/TF-02未改，见 `docs/reviews/20261025-pending/report.md`。
- `.git/thinkflow-pending-20261109.patch` 与 `.zip`保存接手前未提交工作，供本轮回退。
- `.git/thinkflow-efficiency-handoff-before-close-20261109.md`保留接力收口前状态；完整实验历史已归正式结果报告。
- `bench/efficiency_20261011/baseline/`为复用基线输入；正式工作区产物、meter、events、harness与manifest保留作证据。
- 本轮所有实验profile及生成缓存已回收，清单在evidence；工作区产物与原始证据保留，测试进程已退出。旧实验未删除。
- 无新依赖、PATH或Codex认证改动。浏览器操作因网址识别检查中止，Global由主人完成，后续API200确认。
