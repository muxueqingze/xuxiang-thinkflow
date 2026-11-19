# ThinkFlow 0.10.3

本次 CLI 和 Windows Desktop 包含 [0.10.2 的全部改动](https://github.com/muxueqingze/xuxiang-thinkflow/blob/v0.10.3/docs/release-0.10.2.md)，包括多包构建与脚本验收、等待用户的独立状态、长任务缓存去重和可预测写入的同轮交付。

修复终端输出的一处时序问题。当文件工具在模型输出半段正文时返回，工具进度可能插进行内代码中间。工具日志和正文现在各自保持完整。同时修正 Windows 构建验收测试对同一目录长路径和短路径的比较。

- CLI 安装或升级使用 `npm install -g xuxiang-agent@latest`。单次运行需要用户回答时返回退出码 `3`。
- Windows Desktop 下载[当前用户安装程序](https://github.com/muxueqingze/xuxiang-thinkflow/releases/download/v0.10.3/ThinkFlow-0.10.3-Windows-x64-Setup.exe)或[便携 ZIP](https://github.com/muxueqingze/xuxiang-thinkflow/releases/download/v0.10.3/ThinkFlow-0.10.3-Windows-x64.zip)。两个包都自带运行时，SHA-256 可从同版本的 `SHA256SUMS.txt` 核对。
