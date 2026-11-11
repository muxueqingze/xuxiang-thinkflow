# Pro 审查后工程迭代

更新：2026-11-11。基线 `558078e`；依据用户提供的《ThinkFlow 全局源码审查与迭代路线》核对实现，只采纳能改善实际工程行为的建议。连续可预测执行、独立信息合批、最小充分信息与可替换工程指引保持。

已完成单元：让原始证据、执行结果和请求事实贯通 CLI、SDK 和桌面入口。报告 F01–F10 已对应到实现与具体复现，见[改动与验证](reviews/pro-implementation-20261111.md)。

- 附件：`request:N` 保留用户原话，新增 `request:N:input` 读取已有冻结执行输入；两次压缩、修改源文件、恢复与 fork 的 9 项定向检查通过。见[说明](attachment-recall-20261111.md)。
- 计量：逻辑轮与 HTTP 尝试分开，完整/部分/缺失用量及运行起止时间独立记录；CLI 和桌面显示真实尝试数，部分用量显示已知下界。
- 输出和进程：配置 command/image command 复用 Executor 与 OutputCapture；Windows Job 与 POSIX 进程组清理所属子进程；文件线程和重复取消都结算真实结果。
- 消息：文本、OpenAI 和 Anthropic 共用精确 read 正文投影；跨协议切换只转换发送视图，保留历史。固定请求少传 5,710 个 JSON 字符，模型请求数不变。
- 执行：Registry 是三种调用入口的实际分发源；后注册工具可发现、可调用。纯信息批次保留失败和其余成功结果，写入链沿用原停止顺序。
- 持久化：CLI/SDK 可绑定 SessionStore，执行前后记录意图与真实回执；强杀恢复保留已完成结果，未结算操作返回 unknown。CLI 失败/预算停止退出 1，中断退出 130。

验证：本地 parser/core 与 336 项 unittest、桌面 15 项测试通过；最后补充的显式读取边界连同分发模块 7 项通过。隔离 Electron 实测配置与 HTTP/部分用量展示。进程和运行时两个单元的独立验收通过，原失败项均已复核。远端最终提交由现有 CI 执行完整测试。

F11/F12 已接入 0.9.0 并通过[独立定向验收](reviews/release-runtime-quality-20261111.md)：保存 API 实际返回的 Anthropic thinking/signature/redacted 块，工具执行中的磁盘快照也可恢复原块；工具边界追加变化、轮次保存完整检查点、列表读取 `.index`。隐藏的原始思维链不在客户端可见。固定2.7 MB样本40次更新由108,403,739字节降至34,321字节，恢复历史和回执一致。

交付仍走 [PR #1](https://github.com/muxueqingze/xuxiang-thinkflow/pull/1)。0.9.0 分发 CLI npm `next`、Windows当前用户安装EXE和便携ZIP；入口与实测见[版本说明](release-0.9.0.md)。正式测试、运行时与安装证据归入 `tests/`、`docs/evidence/`，本轮实验目录结束即回收。

下一单元是跨阶段、至少两次压缩、恢复/分支的完整 Pi 对照，比较总请求、全部token与最终产物质量；语义摘要仍作为可选对照，不额外添加默认摘要请求。
