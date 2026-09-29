# v0.6 对抗问题收口

此表记录修复与证据，不覆盖独立审查者发现时的 FAIL。第2轮报告独立保留。

| 第1轮问题 | 修复 | 定向证据 |
|---|---|---|
| R1 native重复副作用 | 共用已执行ID与原回执；native完整JSON指纹入快照 | harness新增重复/冲突/恢复测试；原专家append复现从XX变X |
| R2 native失败后继续写 | 后续项skipped并生成配对tool结果 | 原批次失败复现不再创建第二文件 |
| R3 未结束的EOF冒认完成 | 缺少finish/DONE明确error | 残缺tf-write未写入且run_finished=error |
| R4 扩展取消后继续运行 | custom/image取消与超时清理本次进程树 | 真实子进程取消后等待1秒无marker；定向测试 |
| R5 扩展绕过只读 | 输出/执行/生成类工具统一先查read_only | image command未创建marker |
| R6 junction越界 | 根和目标realpath/normcase后比较；敏感文件检查真实名称 | Windows新临时junction下read/write/copy两端均拒绝，test_security_paths |
| R7 bash失败丢诊断 | 无论成功失败都保留exit/stdout/stderr | 真实exit2保留ASSERT输出 |
| R8 扩展错误假成功 | ToolResult携带success/error，兼容字符串接口 | 缺URL、非零退出、缺skill均failed |
| R9 error字段错配 | UI消费message或error | 界面消费者修复；后续Electron流程覆盖 |
| R10 恢复意图不足 | 记录脱敏cmd、完整输入hash、字节数与限长native参数摘要 | 实际tool_started结构；恢复UI展示cmd/hash |
| R11 损坏凭据不可修 | 清除/替换不依赖旧密钥解密；初始化容错并阻止误发送 | 真实Electron坏密文→设置替换→重启解密通过 |
| R12 长会话无界state | 展示窗口120消息/每条32k/500账本；已反馈账本4096条与正文预算 | 150×40000字符state低于5M，原文保留；账本预算保留hash与未反馈观察 |

集成证据：第一轮修复后，旧parser/core全部通过，新增unittest合计51项通过。该数字是自动测试，不代表真实模型或版本级终审。

## 第2轮修复

- S1：错误容器最多128px，详细文字最多104px并可滚动，关闭按钮与输入区持续可见。
- S2：切换会话或回到无错误的闲置状态，清除前一会话的错误提示。
- S3：扩展子进程stdout/stderr并发读取，各保留80000字节，多余输出继续drain并附截断标记；取消/超时等待本次进程树回收。真实双路各2MiB回归通过，取消/超时契约定向通过。
- S4：明确展示窗口、账本裁剪与1600万字符导出上限，导出标题标识只含最近500条回执。

第2轮原失败项已由原独立专家在新包定向复核通过；最终结论PASS（功能/视觉定向范围），结果见round-2.md文末，仍待Pro版本终审。

已知边界：未运行真实商业模型端点；没有掉电、长时负载或多系统验证。工具策略不是OS沙箱；取消不回滚；native/文本去重是有限窗口；会话分支不复制工作区。
