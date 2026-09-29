# 续想桌面端

## 使用

Windows x64 文件夹包内双击 `ThinkFlow.exe`，首次选择工作区，再打开「接口与权限」填写兼容端点和模型。无需安装 Node.js 或 Python；必须保留整个应用文件夹。

- Enter 发送，Shift+Enter 换行，Ctrl+Enter 也可发送；中文输入法选词不会触发发送。
- 运行中发送的后续消息进入本会话队列（最多10条），当前任务成功完成后按顺序继续。停止、失败、断连或重启后队列暂停，须手动继续；可取回编辑或移除，不会修改正在运行的模型请求。
- 发送中的文本会先记入本机 outbox。若发送回执不确定，显示「发送状态待核对」并禁用继续队列。先检查会话是否已收到：已收到则移除该待核对项；确实未发送才确认或取回编辑，防止重复执行。
- 每个工作区的每个会话独立保存草稿。重启恢复上次可用工作区和未归档会话；最近工作区入口快速切换。一台桌面实例仍只运行一个会话，运行中不可切换工作区或会话。
- 左侧搜索会话；顶部「更多」可重命名、置顶、归档、恢复、分支、压缩与导出。归档不删除文件；分支复制上下文和账本，不复制工作区文件，也不重放工具。
- 执行摘要显示在对话下方，点开账本查看详细工具状态与用量。账本默认收起；停止不会回滚已经完成的写入。
- 查看较早的消息时不会强行跟随新输出，可点「回到底部」。消息和代码块分别支持复制。
- 平衡模式约束文件工具在工作区内，对shell等高风险操作逐次询问。只读模式禁止写入与命令执行。开放模式明确放宽限制。
- 模型端点可使用不需要key的本地服务；远程服务按其自身认证规则配置。保存配置不会发起模型调用。
- 会话导出为Markdown，单次导出上限1600万字符，超限会明确提示；完整展示对话仍在本机快照。界面只展示最近120条消息/500条账本，单条长消息显示末尾32000字符。压缩会摘录模型上下文、裁剪旧账本正文并保留摘要/hash，它不是完整文件历史归档。
- 异常退出后存在不确定执行时，先核对文件，再点击「已核对，继续会话」。应用不会自动重放未知副作用。

## 数据

数据位于 Electron 当前用户 `userData` 目录（Windows通常为 `%APPDATA%/thinkflow-desktop`）。`settings.json` 保存模型配置和系统加密后的密钥；`navigation.json` 记录最近20个工作区及上次会话；`data/workspaces/` 保存按工作区隔离的会话及意图日志。草稿、待发队列与界面偏好在 Electron 的本地存储中，普通任务文本不会单独加密。不要将整个数据目录当公开源码上传。CLI历史位于原有ThinkFlow目录，两者独立。

加密密钥绑定当前系统用户，复制到另一台电脑后可能需要重新填写。应用只防止通过界面回读API key，不承诺对有权读取本机进程/用户目录的管理员形成隔离。

## 快捷操作

| 操作 | Windows 快捷键 |
| --- | --- |
| 搜索会话与常用操作 | Ctrl+K |
| 新建会话 | Ctrl+N |
| 收起/展开侧栏 | Ctrl+B |
| 设置 | Ctrl+, |
| 快捷键帮助 | Ctrl+/ |

命令面板只提供已实现的动作；没有内置终端、Git差异编辑器或跨会话并行执行。操作方式参考 [Codex 项目与会话](https://learn.chatgpt.com/docs/projects) 和 [快捷键](https://learn.chatgpt.com/docs/reference/commands)。

## 开发与构建

```powershell
python -m pip install -e .
npm --prefix desktop ci
npm run desktop
python -m pip install pyinstaller
npm run desktop:build
```

构建产物位于 `artifacts/desktop-时间戳/ThinkFlow-win32-x64/`；最近路径记录于 `artifacts/latest-desktop.json`。每次构建单独输出，不覆盖旧包。Python服务入口 `scripts/desktop_entry.py`，Electron使用私有管道，不监听HTTP端口。

```powershell
python -B tests/run_all.py
npm --prefix desktop test
npm --prefix desktop run test:smoke
npm --prefix desktop run test:ux
node desktop/tests/electron-ux.cjs --packaged
```

桌面验收使用临时工作区与本地SSE端点，不能替代真实供应商兼容性测试。当前目标平台为Windows x64；未做macOS/Linux发布验证，Windows包未商业签名。
