# ThinkFlow 0.10.2

本版同步更新 CLI、Python SDK 源码和 Windows Desktop。

- **构建验收覆盖本次改动的每个 npm 包。** 开启 `delivery_verify` 后，失败的包在本次任务中保持未通过，修复后重新构建。每一步沿用现有 shell 授权、工作目录、环境过滤和取消机制。
- **脚本验收跟踪真实运行。** 开启 `auto_verify_runnable_artifacts` 后，直接运行已写入脚本并成功退出才能完成对应检查。后续修改需要重新运行。
- **等待用户有明确状态。** 当前计划阻塞并向用户提问时，单次 CLI 返回 `3`，SDK 返回 `awaiting_input`。Desktop 显示“等待您回复”，保存该输入回执并暂停后续队列。用户的回答优先于先前排队的任务。
- **行内工具示例完整显示。** CLI 和 Desktop 保留 Markdown 反引号中的工具写法，围栏内示例也可正常展示。示例外的流式命令按原顺序执行。
- **长任务只追加变化的状态。** 工具 ID 更新时，相同计划和执行环境沿用已有前缀。压缩移走必要信息时补回当前值。可预测写入完成后可同轮交付；SDK 宿主可给固定任务传入本地产物验收函数。[缓存对照](https://github.com/muxueqingze/xuxiang-thinkflow/blob/v0.10.2/docs/research/runtime-cache-20261119.md) · [同轮交付对照](https://github.com/muxueqingze/xuxiang-thinkflow/blob/v0.10.2/docs/research/early-stop-20261119.md)
- **桌面分发**包含冻结后端及随包依赖的许可文本。安装版和便携版均自带 Electron 与 Python 运行时。

感谢 [@x1051445024](https://github.com/x1051445024) 贡献模型刷新补全、会话回放与恢复、流式响应收尾、缓存统计和可选供应商缓存设置。[#5](https://github.com/muxueqingze/xuxiang-thinkflow/pull/5) · [#6](https://github.com/muxueqingze/xuxiang-thinkflow/pull/6) · [#7](https://github.com/muxueqingze/xuxiang-thinkflow/pull/7) · [#8](https://github.com/muxueqingze/xuxiang-thinkflow/pull/8) · [#10](https://github.com/muxueqingze/xuxiang-thinkflow/pull/10)

CLI 安装或升级使用 `npm install -g xuxiang-agent@latest`。Windows Desktop 可下载[安装程序](https://github.com/muxueqingze/xuxiang-thinkflow/releases/download/v0.10.2/ThinkFlow-0.10.2-Windows-x64-Setup.exe)或[便携 ZIP](https://github.com/muxueqingze/xuxiang-thinkflow/releases/download/v0.10.2/ThinkFlow-0.10.2-Windows-x64.zip)。校验值见同一版本的 `SHA256SUMS.txt`。
