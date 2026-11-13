# ThinkFlow 0.10.0：连续状态更新、项目搜索与运行中补充

2026-11-13。延续 [0.9.1](release-0.9.1.md) 的执行链，落实本轮四项工程改进。

## 行为变化

- `tf-update_plan` 用 JSON 正文保存已知计划状态。成功只确认提交内容，可与文件写入、完成标记处于同次生成；失败及显式 `need_result` 仍反馈。先限定为实际可预测的本地状态更新，外部命令和任意扩展没有自动改成 delayed。原生计划工具继续遵循提供方的 tool call/result 协议。
- `tf-grep`、`tf-glob`、`tf-list_files` 接受 JSON 正文，独立搜索整批反馈。目录枚举遵循工作区内 `.gitignore`，显式文件/目录可直接搜索；保留 Python 正则和原有文件权限。遍历在可取消的工作线程完成。
- Desktop 运行中可选「补充当前 / 排队任务」，Alt+Enter直接排队；CLI/TUI使用 `/btw` 或 `/steer` 补充当前任务，普通输入继续排队。补充在完整响应和工具批次结束后进入下一次请求，不中断已开始的工具。暂停同时作用于待发输入；停止或崩溃后未送达补充保留原队列位置，避免串入后一个任务。
- 可选 `task_plan.context` 保存简短阶段说明、决策与文件导航。省略沿用，提供对象整体替换，`{}`清除；原有步骤负责进度和下一步。状态随计划持久化并直接投影到请求，不新增记忆库或默认摘要调用。当前任务及其运行中补充在压缩后一起保留原文。

具体语法见[协议1.11](../PROTOCOL.md)，SDK队列接入见[配置与嵌入](configuration.md)。阶段设计参考[固定版本Pi与官方工程资料](research/context-design-20261113.md)。

## 实测

- 两种模型协议的真实 SSE 消费路径中，计划→写文件→完成计划均在流结束前执行，只发出 **1 次请求**；错误计划会阻止后续依赖写入。只读工作区仍可记录计划状态。
- 同一普通源码语料，搜索前20条的中位耗时 **31.4→10.0 ms**，完整扫描耗时相近；加入3,000个忽略文件后，同一源码命中 **1,277.5→507.9 ms**。[搜索记录](reviews/search-20261113.md)
- 无补充路径的200对交替样本，请求正文逐字节相同，每轮均1次HTTP；配对中位差约 **+2.6 µs**。这是同版本的局部调度成本，不计模型网络延迟。[复现与数据](evidence/steering-performance-20261113.json)
- 真实终端/TUI均按3次请求完成「当前任务、同批两条补充、后续任务」；桌面本地模型流程用4次请求完成当前读取、补充和两项后续任务。原文、输入法、暂停、回执丢失查询和重启恢复有实际验证。[CLI](reviews/steering-cli-20261113.md) · [Desktop](reviews/steering-ui-20261113.md)
- 独立检查发现并关闭崩溃恢复后补充串入后续任务的问题；双协议原生工具配对、两次压缩、阶段状态恢复和分支隔离通过。[独立定向验收](reviews/iteration-quality-20261113.md)

本轮未调用付费模型；历史 Pi 三题对照维持其原版本、任务和数字，本次局部测量不更新该结论。

正式桌面 ZIP 与 npm tarball 的校验、隔离安装和最终打包界面记录见[分发证据](evidence/release-0.10.0.json)。

## 安装

- [Windows 安装版](https://github.com/muxueqingze/xuxiang-thinkflow/releases/download/v0.10.0/ThinkFlow-0.10.0-Windows-x64-Setup.exe)
- [Windows 便携 ZIP](https://github.com/muxueqingze/xuxiang-thinkflow/releases/download/v0.10.0/ThinkFlow-0.10.0-Windows-x64.zip)
- CLI：`npm install -g xuxiang-agent@next`，固定版本 `xuxiang-agent@0.10.0`；Node.js 18+、Python 3.12+。npm只安装CLI。

桌面自带 Electron、服务运行时及独立 CPython；新增搜索依赖 PathSpec 随包提供，其许可证与精确版本源码链接位于 `PathSpec-LICENSE.txt`。源码/CLI安装通过现有依赖入口安装 `pathspec>=0.12.1`，本机验证版本1.1.1，无PATH变更。
