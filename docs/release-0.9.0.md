# ThinkFlow 0.9.0：长程会话与桌面安装

2026-11-11。CLI 与 Desktop 使用同一运行内核，分别分发。

## 安装

| 入口 | 安装方式 | 运行环境 |
|---|---|---|
| Windows 桌面安装版 | [下载 Setup.exe](https://github.com/muxueqingze/xuxiang-thinkflow/releases/download/v0.9.0/ThinkFlow-0.9.0-Windows-x64-Setup.exe)，双击安装，从开始菜单打开“续想 ThinkFlow” | 自带 Electron/Python，无需 Node.js/Python 或管理员权限 |
| Windows 桌面便携版 | [下载 ZIP](https://github.com/muxueqingze/xuxiang-thinkflow/releases/download/v0.9.0/ThinkFlow-0.9.0-Windows-x64.zip)，完整解压后运行 ThinkFlow.exe | 与安装版相同 |
| 命令行 | `npm install -g xuxiang-agent@next`；固定版本用 `xuxiang-agent@0.9.0` | Node.js 18+、Python 3.12+ |

安装版默认写入当前用户的 `%LOCALAPPDATA%\Programs\ThinkFlow`，可选择其他目录；创建开始菜单入口，不创建桌面快捷方式。升级保留 `%APPDATA%\thinkflow-desktop` 的设置与会话，卸载只移除程序。便携版也使用该应用数据目录。首次使用选择项目目录，在设置中填写模型端点和凭据。

npm 包提供 `thinkflow` 和 `xuxiang` 两个 CLI 命令，不安装 Electron。`next` 更新到本预览版本，`latest` 保留 0.5.1。

## 相比 0.8.0

- 长程上下文保留原始要求、冻结附件和完整工具输出，支持范围/关键词回取；行范围 read 可直接支持同版本精确 edit。
- 文本和两种原生协议共用精确 read 正文投影，切换协议时只转换发送视图；独立读取失败仍返回其余结果。
- CLI/SDK/桌面工具执行中保存真实回执，恢复不重放已完成操作；扩展命令保留完整输出，取消等待实际结果结算。
- 生产用量区分真实 HTTP 尝试、逻辑轮与缺失用量。
- Anthropic 实际返回的 thinking、signature、redacted 内容块完整保存并原样回传，包括空 thinking。这里保存供应商返回的协议状态，隐藏的原始思维链仍不在客户端可见。
- 工具执行期间追加快照变化，轮次边界保存完整检查点；会话列表读取小索引。旧 JSON 快照可直接读取，首次保存后加入新格式。
- 分层配置、单次覆盖和公开 SDK；桌面全屏设置/搜索、实际思考档位、Git 差异、文件标签、工具/技能目录、工作区终端和统一动效。

连续可预测执行、独立信息合批及最小充分信息保持。工程指引可推荐、自定义或关闭，工具协议由核心注入。

## 持久化实测

同一 Windows/Python 3.12 环境，2.7 MB 会话、40 次新回执更新、12 个会话的固定本地样本：

| 指标 | 0.8 开发基线 d60024c | 0.9 实现 |
|---|---:|---:|
| 40 次更新写入 | 108,403,739 字节 | 34,321 字节 |
| 更新累计耗时 | 544.523 ms | 83.263 ms |
| 列表读取 | 64,981,228 字节 | 20,412 字节 |
| 列表耗时 | 532.005 ms | 6.711 ms |
| 列表读取完整快照次数 | 24 | 0 |

恢复后的消息、完整展示历史和全部回执逐项一致。这是本地存储和界面等待的收益，模型请求数保持不变。复现入口 `bench/persistence_20261111/run.py`；[基线](evidence/persistence-20261111-before.json)与[新实现](evidence/persistence-20261111-after.json)保存完整计量。

## 验证入口

本地完整集合执行346项，首次检查发现旧Anthropic用量夹具缺少内容块index/start/stop，以及PID文件创建早于写完的测试时序；另修正配置CLI测试按GBK读取UTF-8输出。修正夹具后4项原失败/直接影响检查通过；桌面Node 15项通过，最终完整集合由GitHub CI运行。F11/F12的独立验收已收口。

- [API 内容块接收与回传](reviews/anthropic-roundtrip-20261111.md)
- [本轮独立运行时验收](reviews/release-runtime-quality-20261111.md)
- [前一轮执行与消息改动](reviews/pro-implementation-20261111.md)
- [桌面安装、数据位置和构建](desktop.md)

本轮发布验证使用隔离目录和本地 SSE 服务；既有用户桌面实例与登录保持。完整长任务与 Pi 的新对照另按[效率接力](efficiency-handoff.md)推进。
