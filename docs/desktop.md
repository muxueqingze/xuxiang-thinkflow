# 续想桌面端

## 使用

在 [GitHub Releases](https://github.com/muxueqingze/xuxiang-thinkflow/releases) 下载 Windows x64 的 `ThinkFlow-版本-Windows-x64-Setup.exe`，双击安装。默认安装到当前用户的 `%LOCALAPPDATA%\Programs\ThinkFlow`，可以在向导里更改目录。安装后从开始菜单打开「续想 ThinkFlow」。可在 Windows「设置 → 应用」卸载，配置和会话会保留。

便携版下载 `ThinkFlow-版本-Windows-x64.zip`，完整解压后双击 `ThinkFlow.exe`。两种桌面包都自带应用所需的 Node.js 和 Python 运行时。具体项目的编译器和运行时按项目配置。首次打开时选择工作区，再在「设置 → 模型连接」填写兼容端点和模型。

`tf-python` 使用包内 `resources/backend/python/python.exe` 独立执行，包含官方 CPython 3.12.10 embeddable 的标准库与扩展模块，可直接导入当前工作区的模块。需要项目虚拟环境或额外第三方包时，在启动桌面前将 `THINKFLOW_PYTHON` 设置为相应解释器路径。CLI 继续使用该变量或系统 `python`。

- Enter 发送，Shift+Enter 换行，Ctrl+Enter 也可发送；中文输入法选词不会触发发送。
- 后续消息由后端持久接收，最多20条等待任务。当前任务成功后按顺序继续；停止、失败和重启后暂停。可独立编辑或移除未开始的任务，不改动输入框草稿。
- 提交先保存在本机，使用稳定命令ID和原内容指纹。回执丢失会精确查询原提交，已接收就不重发；未接收可用同ID重试或取回内容。旧0.7队列保留为人工迁移入口，不自动执行。
- 顶部图标直接打开文件、变更、活动和终端；右侧保留「执行 / 任务 / 文件 / 变更」四个页面。文件支持目录浏览、名称搜索和UTF-8只读预览，可同时保留多个文件标签，各自记住阅读位置；同工作区切换会话也保留。每次最多附加8个文件、合计128 KiB，提交时核对版本并冻结内容。
- 整文件覆盖使用完整读取的SHA256版本；当前源码的精确edit可使用同版本已读行范围，外部改动后重新读取。成功的可预测写入仍随模型流持续执行。
- 当前源码默认使用tf-*流式协议，独立read和检查同批声明、一起反馈；多行Python用tf-python。设置中的「启用原生工具调用」用于扩展工具和原生兼容，开关会持久保存。开启后read/write/edit/bash/python/read_skill/load_tools首轮可用，其余由load_tools按需开放。旧便携包沿用构建时行为。
- 设置中的「工程指引」可选推荐／自定义／关闭；默认推荐保留内置方法。自定义内容原样保存，只替换工程方法和回答风格；工具协议由核心自动注入，工作区规则独立保留。
- 当前源码的「高级配置」可调整上下文、工具、读取及执行参数；删除字段恢复默认。常用表单保存会保留这些设置；无效输入保留原文和原配置。参数与示例见[配置说明](configuration.md)。
- 变更页默认显示 Git 已暂存、未暂存和未跟踪文件；差异在文件行下展开，可同时展开多份文件，显示新旧行号。刷新、隐藏面板和同工作区切换会话会保留展开状态。切到「本会话」可查看逐次操作 diff 与版本检查后的回退。可回退不超过1 MiB的非敏感文本修改；新增文件、目录、二进制、shell产生的任意变更不提供自动回退。回退也记录意图和回执，未知状态须核对。
- 多步骤任务可由模型调用update_plan保存步骤、验收条件与执行回执。完成项必须引用成功工具回执；界面明确这是模型标记，成功回执不能替代验收条件的语义核对。
- 每个工作区的每个会话独立保存草稿。重启恢复上次可用工作区和未归档会话；最近工作区入口快速切换。一台桌面实例仍只运行一个会话，运行中不可切换工作区或会话。
- 左侧搜索会话；顶部「更多」可重命名、置顶、归档、恢复、分支、压缩与导出。归档不删除文件；分支复制上下文和账本，不复制工作区文件，也不重放工具。
- 执行摘要显示在对话下方，点开账本查看详细工具状态与用量。账本默认收起；停止不会回滚已经完成的写入。
- 查看较早的消息时不会强行跟随新输出，可点「回到底部」。消息和代码块分别支持复制。
- 平衡模式约束文件工具在工作区内，对shell等高风险操作逐次询问。只读模式禁止写入与命令执行。桌面开放模式放宽命令与敏感读取，可读取工作区外内容；带变更回执的文件写入仍限当前工作区。CLI开放模式沿用原有路径策略。
- 模型端点可使用不需要key的本地服务。设置提供DeepSeek预设、与当前协议匹配的思考选项、模型列表与连接测试；后两项仅点击时调用已保存端点，连接测试有少量模型用量。保存本身不调用模型。
- 会话导出为Markdown，单次导出上限1600万字符，超限会明确提示；完整展示对话仍在本机快照。界面只展示最近120条消息/500条账本，单条长消息显示末尾32000字符。压缩会摘录模型上下文、裁剪旧账本正文并保留摘要/hash，它不是完整文件历史归档。
- 当前源码另存原始用户要求及附件展开后的执行输入，当前输入在压缩后完整保留。`request:N` 回取用户原话，`request:N:input` 回取包含冻结附件的提交时输入；原文和摘要状态随恢复、分支及配置切换保存。仅新记录的要求拥有引用，旧快照按原内容载入。0.9.0已包含该能力。
- 当前源码将完整工具输出保存在会话快照旁的`.outputs`目录，支持按短回执引用及关键词回取；分支复制原文，取消/超时保留已收到的输出。Markdown导出提供回执到本地文件的索引。见[输出留存](output-recall-20261111.md)。
- 异常退出后存在不确定执行时，先核对文件，再点击「已核对，继续会话」。应用不会自动重放未知副作用。
- 文件工具内存操作上限8 MiB，大文件读取返回有界截断，截断内容不能作为完整写入基线；不会因失败偷偷改走shell。
- shell、Python、配置 command 和 image command 共用完整输出归档。stdout/stderr 内存各保留末尾8万字符，预览上限100万字符；`max_bash_output_chars<=0` 使用默认预览长度，原文仍完整保存。
- 命令拥有的进程树由 Windows Job / POSIX 进程组管理，父进程先退出也保持所有权；正常结束、超时及取消都会结束本次所属子进程并结算输出。重复停止保留已收到的输出和实际执行回执。

## 思考、设置与工作区面板

- 输入区的思考按钮显示下一次请求使用的实际档位。DeepSeek 支持关闭/低/高/最高；其他 OpenAI 兼容端点默认跟随模型，主动选档才发送 `reasoning_effort`；Anthropic 使用具体 token 预算，须至少 1024 且小于输出上限。选择本身不调用模型。
- 全屏设置覆盖模型连接、上下文与运行、工程与权限、工具、技能、外观和高级配置。可搜索并定位具体字段，自动展开所在折叠区；返回后继续原工作台。上下文及技能目录表单与高级 JSON 使用同一份配置草稿，保存后生效；主题即时在当前设备生效。
- 运行轮数与秒数留空表示不设该项限制，秒数支持小数。切换 OpenAI/Anthropic 后，工具历史在下次请求中转换为目标格式，原始记录保留。
- 用量显示实际 HTTP 请求数（包含重试），逻辑轮单独计数；缺失或部分用量按已知下界展示，旧快照的未计量请求明确标注。「减少模型往返」使用每工具一请求的估算基线。
- 工具页读取实际 registry，清楚区分流式入口、当前原生开放状态和按需开放。每项「允许原生」只改变 `disabled_native_tools`；流式工具仍遵循执行权限。
- 技能页显示本地发现的技能，支持搜索和只读正文预览；技能发现开关与额外目录可直接修改，一行一个目录。
- 终端使用 xterm.js 和 Windows ConPTY，新建 PowerShell 的输入/输出及原生管道采用 UTF-8。可输入交互命令、新建/关闭标签、调整高度；隐藏面板或切换同工作区会话会保留终端，切换工作区或退出应用会关闭其进程树。终端操作不经过模型。
- 单次授权原位展示真实命令，较长命令可展开；正文独立滚动，允许/拒绝按钮持续可见。草稿和已受理队列保留。
- 面板拖拽直接跟随指针，开合使用短过渡；保留内容与阅读位置。小窗口统一限制左右栏，给对话区保留至少 400px；系统减少动态效果和应用选项均生效。

## 数据

数据位于 Electron 当前用户 `userData` 目录（Windows通常为 `%APPDATA%/thinkflow-desktop`）。`settings.json` 保存模型配置和系统加密后的密钥；`navigation.json` 记录最近20个工作区及上次会话；`data/workspaces/` 保存会话、输入回执、后端队列、附件快照、任务清单及文件变更记录。草稿、待确认提交与界面偏好在Electron本地存储中。普通任务及文件文本不会单独加密，不要把用户数据目录当公开源码上传。CLI历史与桌面独立。

加密密钥绑定当前系统用户，复制到另一台电脑后可能需要重新填写。应用只防止通过界面回读API key，不承诺对有权读取本机进程/用户目录的管理员形成隔离。

## 快捷操作

| 操作 | Windows 快捷键 |
| --- | --- |
| 搜索会话与常用操作 | Ctrl+K |
| 新建会话 | Ctrl+N |
| 收起/展开侧栏 | Ctrl+B |
| 设置 | Ctrl+, |
| 快捷键帮助 | Ctrl+/ |
| 文件面板 | Ctrl+Shift+E |
| 变更面板 | Ctrl+Shift+G |
| 终端 | Ctrl+` |
| 停止当前任务 | Esc（终端或菜单有自己的按键处理） |

输入区左侧是文件引用和权限，右侧是上下文、模型、思考与发送。模型和权限点击后就地选择；有草稿时运行按钮切为加入队列，Esc 仍可停止。上下文浮层区分最近输入估算、当前保留消息和自动整理阈值。操作方式参考 [Codex 项目与会话](https://learn.chatgpt.com/docs/projects) 和 [快捷键](https://learn.chatgpt.com/docs/reference/commands)。

## 开发与构建

```powershell
python -m pip install -e .
npm --prefix desktop ci
npm run desktop
python -m pip install pyinstaller
winget install --id JRSoftware.InnoSetup --exact --scope user
```

构建需要 Inno Setup 6.5 或更新的 6.x 版本。将 `ISCC.exe` 所在目录加入 PATH，或用 `THINKFLOW_ISCC` 指定编译器的完整路径。如果编译器的 `Languages/` 目录缺少 `ChineseSimplified.isl`，将 [Inno Setup 6.7.3 的简体中文翻译](https://raw.githubusercontent.com/jrsoftware/issrc/refs/tags/is-6_7_3/Files/Languages/Unofficial/ChineseSimplified.isl) 保存到该目录。

```powershell
npm run desktop:build
```

每次构建在独立的 `artifacts/desktop-时间戳/` 下生成 `ThinkFlow-win32-x64/`、安装程序和便携 ZIP。`artifacts/latest-desktop.json` 记录最近一次完整构建的版本与三种产物路径。Python 服务入口为 `scripts/desktop_entry.py`，Electron 使用私有管道连接服务。

构建会下载并校验官方 Python embeddable ZIP，复用缓存为 `artifacts/build-inputs/python-3.12.10-embed-amd64.zip`，这是保留的构建输入。离线构建可预先把同名官方文件放到该目录。工具运行时完整解包到后端的 `python/`，保留官方 `._pth` 隔离设置和许可证；stdin 入口使用命令行 UTF-8 开关并显式加入工作区导入路径。它不安装 pip/Tk；项目第三方依赖使用上面的解释器覆盖。构建不修改系统 PATH。

```powershell
python -B tests/run_all.py
npm --prefix desktop test
npm --prefix desktop run test:smoke
npm --prefix desktop run test:ux
npm --prefix desktop run test:workbench
node desktop/tests/electron-panels.cjs
node desktop/tests/electron-composer.cjs
node desktop/tests/electron-thinking.cjs
node desktop/tests/electron-git-inline.cjs
node desktop/tests/electron-file-tabs.cjs
node desktop/tests/electron-workbench.cjs --packaged
```

桌面回归使用临时工作区与本地SSE端点。真实DeepSeek测试须显式执行 `python -B scripts/live_deepseek_eval.py --run`，从当前环境的DEEPSEEK_API_KEY或Windows用户DPAPI凭据读取密钥，默认不运行，不进入CI。当前目标平台为Windows x64；未做macOS/Linux发布验证，Windows包未商业签名。
## 可选输出预算（源码更新）

OpenAI 兼容接口的“输出 token 上限”默认留空，保存为 `null`，发送请求时省略 `max_tokens`，不另加客户端输出额度；已有配置中的显式正整数仍会保留。CLI 配置同样支持 `null`，`--max-tokens 0` 可清除已有输出额度。Anthropic 协议必须给出正整数，切换时需要明确填写，程序不会猜一个上限。

这只改变客户端请求，不改变服务商自身容量和省略参数时的默认值。更新到0.9.0桌面包可使用上述配置。
