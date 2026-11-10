# ThinkFlow 0.8.0 · CLI 预览包

日期：2026-11-10。npm包名`xuxiang-agent`，发布通道`next`，命令入口`thinkflow`和`xuxiang`。

```bash
npm install -g xuxiang-agent@next
thinkflow --version
thinkflow --help
```

固定版本安装：`npm install -g xuxiang-agent@0.8.0`。需要Node.js 18+与Python 3.12+；安装钩子安装Python依赖，可用`THINKFLOW_PYTHON`指定解释器。模型端点与凭据由用户配置。

## 本包更新

- 默认tf-*流式执行，独立信息调用合批反馈，支持多行Python代码块；保留实际失败与显式取回结果边界。
- 框架核心自动注入工具协议，推荐工程提示可以替换或关闭；新增原则作为可选示例随包分发：`docs/prompts/engineering-guidance.md`。
- 无效命令在完整校验前不占用编号；专用Python进程统一UTF-8；压缩保持OpenAI/Anthropic工具调用与结果配对；孤立tf结束标记进入协议恢复。
- 包含会话、文件版本与变更记录、权限配置、provider适配等Python核心，以及CLI启动器、内置skill与使用文档。

此npm包提供CLI。Electron界面与Windows便携运行时单独构建，本次GitHub预发布资产为同一份npm tarball。

## 验证与工程记录

源码回归及GitHub CI已覆盖当前核心；发布前另验证打包清单、隔离安装的postinstall、两个命令入口和安装包内的实际执行路径。发布记录保留包校验值，并与registry返回的tarball核对。

61.11%的历史token对照固定于源码40dfa30；后续工程指引试跑的全部结果与默认恢复决定见`docs/engineering-quality-20261110.md`。本版本不把新增指引试跑描述为质量提升或新的节省比例。

`next`供显式选择本预览版，`latest`继续指向0.5.1。正式更新到默认通道时另行调整dist-tag。
