# 工程能力与缓存输入优化 · 2026-11-09

最终源码在固定三题上均正常完成，产物各8/8，实际公开检查与交付文本已独立核对。相较旧版2a4f4b8，缓存输入减少15.99%，总token减少19.97%。Pi在这组任务中仍更省；frame_decoder单题缓存增加33.37%，因此本轮结论是三题合计改善。

最终冻结工作树已保存为本地实现提交`a405f95`（`codex/thinkflow-efficient-execution`），未推送。

## 最终对照

全部使用OpenCode Go的`deepseek-v4.1-flash`、thinking enabled/high；无客户端输出、调用轮数、总时长额度，自动压缩关闭。最终组是`go-recovery-20261109`，旧版和实际Pi 1.0.1复用`go-efficiency-20261109-r1`，每题各一次。temperature=0已发送，DS thinking模式不会因此变为确定性采样。

| Harness | 完整交付 | 请求 | 缓存输入 | 非缓存输入 | 输出（含推理） | 总token |
|---|---:|---:|---:|---:|---:|---:|
| 旧ThinkFlow 2a4f4b8 | 3/3 | 32 | 382,592 | 109,597 | 50,031 | 542,220 |
| 当前ThinkFlow | 3/3 | 31 | 321,408 | 71,576 | 40,954 | 433,938 |
| Pi 1.0.1 | 3/3 | 27 | 187,776 | 28,451 | 33,284 | 249,511 |

当前较旧版非缓存输入减少34.69%；较Pi缓存输入多71.17%、总token多73.92。三题累计墙钟旧版664.454秒、当前342.438秒、Pi328.220秒，包含当时供应商响应及本地执行。

| 任务 | 旧版缓存输入 | 当前缓存输入 | 变化 | 当前总token |
|---|---:|---:|---:|---:|
| receipt_package：金额聚合与CSV命令行 | 103,680 | 95,488 | −7.90% | 131,843 |
| pricing_refactor：共享金额逻辑重构 | 149,248 | 52,992 | −64.49% | 84,347 |
| frame_decoder：增量UTF-8帧解析 | 129,664 | 172,928 | +33.37% | 217,748 |

三题最终实现、公开测试及附加检查的真实输出均已查看；frame附加30项还独立脱离管道执行通过。没有修改公开测试。全部9份比较样本的原始usage逐条复算与meter/result一致，缓存字段完整。推理token包含在输出中，不再次相加。

最终组实际文件操作全部走原生批次，delayed完成、重复read投影事件、DSML恢复事件均为0。本组对照衡量的是整套实现与提示配置；流式执行、read投影和协议恢复的正确性另由定向运行验证。本轮没有逐项消融，不能把合计省量直接归到某一机制。

## 实现与原理

继续以“是否提供新信息”决定反馈，不把成功确认变成新的推理需求：

- **成功正文只保留必要副本。** 先完成历史压缩，再对实际保留的历史精确比对tf成功载荷；缺失正文完整反馈。native read只在完整内容确实相同时投影为现存write/read引用；原消息、快照和界面原文保留，来源被压缩后恢复全文。
- **反馈仍完整。** 未反馈载荷不再先裁8K，失败/read/bash输出不重复头尾且保留执行器返回正文。runtime和账本附在已有observation，原生工具后不另造用户回合。DeepSeek所需reasoning原样保留。
- **工具按需加载。** 参考Pi的小工具入口，默认提供read/write/edit/bash/python/read_skill/load_tools；其余能力继续使用原registry，保留用途目录，按需开放完整schema并保存到快照。显式allow−deny优先。最终实验首请求工具数由14降至7；默认生产配置的目录还包含联网等扩展能力。
- **Python直接执行。** `python(code)`经stdin运行多行代码，复用既有进程、授权、超时和取消链；不需要临时脚本或多层shell转义。普通Python可组合已知文件处理和针对性检查。
- **修实际恢复缺陷。** 同一SSE片段后部格式错误不再吞掉先前合法命令；DeepSeek把DSML控制尾返回成正文时进入原协议错误反馈，不执行残片。旧3次连续失败硬阈值改为默认null，显式数字预算保留，版本冲突仍由模型读回解决。
- **撤出未发布的verify版本门禁。** 验证继续使用实际命令、结果和项目现有检查。没有新增强制验收流程。Go使用自有UA与稳定会话标识，Python编码环境正常透传。

旧基线保留其原3次连续失败阈值，三题均未触发；当前默认没有该阈值，最终worker记录有效值null。现有时间/轮数配置、取消、不确定副作用停止机制保持。完整差异可在当前分支查看；代码定位见[代码地图](代码地图.md)。

## 所有探索均保留

以下目录均在`bench/efficiency_20261011/artifacts/`。完整28条实验状态、所有已报用量和9份最终比较的逐请求原始usage在[comparison.json](evidence/efficiency-20261109/comparison.json)。表中“交付”还核对运行终态和实际检查，不只看隐藏评分。

| 实验目录 | 结果及用途 |
|---|---|
| go-efficiency-20261109-r1 | 旧版/Pi各3题通过。探索ThinkFlow两题初始ReadError失败，receipt产物通过但2条请求缺usage。 |
| go-efficiency-20261109-r2 | 隐藏全部六个native文件工具的实验失败：receipt出现40次无作用print，64请求、已报1,288,419 token且1条缺usage；frame人工停止，pricing未开始。该策略已撤销。 |
| go-final-receipt-20261109 + go-final-rest-20261109 | 恢复文件schema后三题通过，缓存410,112、总538,634，缓存目标未达。 |
| go-python-receipt-20261109 | Python工具初版pilot，模型未使用新工具；receipt通过，总157,459。 |
| go-code-blocks-20261109 | frame/pricing通过；receipt以DSML尾提前结束，产物6/8，无公开检查。 |
| go-projection-20261109 | frame/pricing通过；receipt虽产物8/8，却因单个DSML calls尾提前结束，无检查和完整报告，记为未完成。 |
| go-lazy-tools-20261109 | receipt/pricing通过；frame产物8/8，但自测、cmd语法及版本冲突后触发旧连败阈值，运行失败。 |
| go-recovery-20261109 | 最终冻结组三题完整通过，使用本报告主表的全部最终值。 |

上述装置记录共326个请求、已报4,760,978 token，9条请求缺usage；没有报告用量的请求保留未知，不当成零。该数包含失败、取消和已撤销探索，不含装置外连通性探针。此前更低的个别成功值没有替换最终组数据。

## 验证、证据与保留项

- 完整节点：parser19、core82及238项unittest通过，日志[final-lazy-python.txt](evidence/efficiency-20261109/final-lazy-python.txt)。后续失败预算小修只复验直接影响的[13项budget](evidence/efficiency-20261109/optional-recovery-budget.txt)和[28项harness](evidence/efficiency-20261109/harness-recovery-budget.txt)，均通过。桌面原有13项检查通过，本轮没有界面改动。
- 独立审查：[efficiency-20261109.md](reviews/efficiency-20261109.md)。覆盖实际文件/子进程、压缩后正文恢复、按需工具加载/快照、Python取消/授权、协议恢复、失败预算及最终产物/计量。
- 原始工作区、meter、events、harness、stdout、result、冻结manifest留在各实验目录；原始模型输出仅本地ignored保留，正式证据只收录必要指标。`comparison.json`保存最终冻结源码指纹及逐请求usage。
- 本轮隔离profile、生成缓存已回收，清单在同一evidence目录；测试进程已退出。基线archive是后续复现输入，继续保留。接手前工作树备份在`.git/thinkflow-pending-20261109.patch`与`.zip`。
- 无新增依赖、PATH或Codex登录改动；key只用于进程环境/内存，没有进入Git。源码与文档本地交付，旧Windows便携包保持原构建内容。

当前下一步是针对frame的自建检查修订和原生逐步调用成本继续观察。Pi仍是更省量的实际参照。可选delivery的旧TF-01/TF-02保持原待办，见[既有审查](reviews/20261025-pending/report.md)；本轮只处理与当前执行/缓存目标直接有关的修改。

供应商契约参考：[OpenCode Go](https://opencode.ai/docs/console/go/)、[DeepSeek thinking mode](https://api-docs.deepseek.com/guides/thinking_mode/)。本机Pi来自`@earendil-works/pi-coding-agent` 1.0.1，实际CLI及默认read/bash/edit/write工具参与对照，装置见[README](../bench/efficiency_20261011/README.md)。
