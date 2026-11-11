# ThinkFlow 0.9.1：会话恢复与桌面 Python 补丁

2026-11-11。延续 [0.9.0](release-0.9.0.md) 的运行方式，修复当前安装包定向检查中复现的两处问题。

## 修复

- CLI/SDK 和桌面的执行日志写到半行时进程退出，恢复可能被不完整 JSON 或 UTF-8 字符阻断。现在只恢复已完整提交的日志行，继续追加前去除未完成的末行，并根据已保存回执补齐缺失完成记录。这样后续压缩不会让已完成操作重新变成未知；结果未知的已开始操作仍交由用户核对。
- 0.9.0 桌面的 Python 工具依赖系统 `python`，没有外部 Python 的机器会报 `WinError 2`。桌面现在包含官方 CPython 3.12.10 独立运行时，支持标准库、中文 stdin 和工作区模块导入。`THINKFLOW_PYTHON` 可指定项目虚拟环境；CLI 继续使用用户的 Python。

本补丁不增加模型请求，也不改变连续可预测执行、独立信息合批、上下文压缩或工程指引配置。

## 安装

- [Windows 安装版](https://github.com/muxueqingze/xuxiang-thinkflow/releases/download/v0.9.1/ThinkFlow-0.9.1-Windows-x64-Setup.exe)：安装后从开始菜单打开，升级保留原有设置与会话。
- [Windows 便携版](https://github.com/muxueqingze/xuxiang-thinkflow/releases/download/v0.9.1/ThinkFlow-0.9.1-Windows-x64.zip)：完整解压后运行 `ThinkFlow.exe`。
- CLI：`npm install -g xuxiang-agent@next`；固定版本 `npm install -g xuxiang-agent@0.9.1`。需要 Node.js 18+、Python 3.12+。npm 包不包含桌面应用。

`next` 提供当前预览版，`latest` 保持 0.5.1。0.9.0 历史发布及其产物保留。

## 检查依据

- [执行日志恢复](reviews/current-session-20261111.md)：真实 CLI 中断、中文日志截断、恢复后继续执行，以及未知结果不重放。
- [协议切换](reviews/current-protocol-20261111.md)：错误参数和失败回执经过 OpenAI/Anthropic 切换后的实际发送数据。
- [桌面 Python 运行时](reviews/packaged-python-20261111.md)：移除子进程 PATH 中的系统 Python 后，核对最终包的执行结果。
- [最终包独立验收](reviews/patch-0.9.1-quality.md)：正式 ZIP 的 244 个条目 CRC 通过，解压件在无系统 Python 的 PATH 下完成实际工具调用、ConPTY 计算、截断日志恢复和完整续跑；旧工具没有重放。

npm tarball 已隔离安装，两个命令入口、实际文件写入与 Python 回读、成功回执及断尾恢复通过。最终桌面 EXE 的隔离 Electron smoke 和桌面 15 项 Node 接口检查通过。[分发验证记录](evidence/release-0.9.1.json)

本轮使用隔离工作区与本地模型夹具，不消耗付费模型额度。
