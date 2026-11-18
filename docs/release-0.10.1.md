# ThinkFlow 0.10.1

感谢 [@x1051445024](https://github.com/x1051445024) 提供复现步骤、用量记录和代码位置。此版本更新 CLI、Python SDK 和 Windows 桌面版。

- [分步计划提前退出（#2）](https://github.com/muxueqingze/xuxiang-thinkflow/issues/2)。当前请求显式建立了计划，工具结算后还有可执行步骤时，ThinkFlow 会依据新的计划进度或已执行的操作继续。若模型只声称在等回执，框架可提醒一次，仍无进展则报告未完成。等待用户决定的步骤可标为 `blocked`。同一次响应完成计划和文件写入仍只需一次模型请求。
- [流式显示抖动（#3）](https://github.com/muxueqingze/xuxiang-thinkflow/issues/3)。CLI 把完成的 Markdown 块依次写入终端，未完成的内容显示在单行预览。桌面逐块更新消息，保留读者上滚的位置。
- [长会话前缀变化（#4）](https://github.com/muxueqingze/xuxiang-thinkflow/issues/4)。压缩为后续消息留出空间，保留当前用户要求和完整工具批次。请求保留先前发送的运行状态，回取协议的说明保持稳定。

**实测**　合成的 49 条长消息在默认字符阈值下，从 19 次压缩降到 4 次。另用 OpenCode Go 的 DeepSeek V4.1 Flash 连续执行三次短问答，三次均答对，各一次 HTTP 请求，只有首轮触发压缩。服务端报告的缓存读取依次为 0 / 6,155、6,144 / 6,177、6,144 / 6,199 个输入 token。这是同一段合成历史的观测值。

CLI 安装或升级：`npm install -g xuxiang-agent@latest`。Windows 用户可从本次 GitHub Release 下载 Setup 安装程序或便携 ZIP。
