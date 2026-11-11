# ThinkFlow CLI 交互参照 · 2026-11-11

ThinkFlow 以 `969377c` 为考察基线。默认入口是 [`interactive_loop_v2`](../../src/cli.py#L1007)，另有 [`--tui`](../../src/tui.py#L143) 全屏入口及 `--legacy-ui`。本轮研究关注终端交互如何使用既有运行时、会话和配置；所有选项均以本地确定性操作为起点。

## 参考行为与实现

- Codex CLI 的源码固定到 `5228d94a27b7f84cae4854acbb5ea3b3e90dfeaf`。`Enter` 提交、`Tab` 排队，`Ctrl+J` 和 `Alt+Enter` 可换行；终端输入层维护草稿、历史、粘贴和补全。队列保留每条原始输入，区分等待发送的后续任务、正在等待应用的转向指令和需要改期重发的转向指令。底栏显示等待内容并提供取回编辑。参考 [默认键位](https://github.com/openai/codex/blob/5228d94a27b7f84cae4854acbb5ea3b3e90dfeaf/codex-rs/tui/src/keymap.rs#L1875-L1898)、[输入状态机](https://github.com/openai/codex/blob/5228d94a27b7f84cae4854acbb5ea3b3e90dfeaf/codex-rs/tui/src/bottom_pane/chat_composer.rs#L130-L161)、[队列](https://github.com/openai/codex/blob/5228d94a27b7f84cae4854acbb5ea3b3e90dfeaf/codex-rs/tui/src/chatwidget/input_queue.rs#L15-L58)和[待发预览](https://github.com/openai/codex/blob/5228d94a27b7f84cae4854acbb5ea3b3e90dfeaf/codex-rs/tui/src/bottom_pane/pending_input_preview.rs#L14-L30)。
- Codex CLI 把正在变化的工具或回复行与已完成记录分开保存，`Ctrl+T` 可看带实时尾部的完整记录。会话选择支持搜索，`/model` 有会话内选择路径，配置面板复用已有编辑器。参考 [记录数据结构](https://github.com/openai/codex/blob/5228d94a27b7f84cae4854acbb5ea3b3e90dfeaf/codex-rs/tui/src/history_cell/mod.rs#L1-L10)、[会话搜索](https://github.com/openai/codex/blob/5228d94a27b7f84cae4854acbb5ea3b3e90dfeaf/codex-rs/tui/src/resume_picker.rs#L494-L510)、[模型切换](https://github.com/openai/codex/blob/5228d94a27b7f84cae4854acbb5ea3b3e90dfeaf/codex-rs/tui/src/chatwidget/model_command.rs#L1-L26)、[配置入口](https://github.com/openai/codex/blob/5228d94a27b7f84cae4854acbb5ea3b3e90dfeaf/codex-rs/tui/src/chatwidget/config_panel.rs#L1-L27)和 [OpenAI Codex CLI 文档](https://learn.chatgpt.com/docs/codex/cli)。
- Claude Code 的交互文档列出多行输入、`Esc` 停止并保留草稿、`Ctrl+O` 展开详细记录，以及运行中 `Enter` 排队、输入首行 `Up` 取回草稿。运行工具时排入的文本可在工具结果就绪后交给当前任务，剩余消息按输入顺序续发；`/status` 等本地查询可以即时处理。它的 `/btw` 运行独立的旁问，因此 ThinkFlow 可借鉴队列与工具边界，沿用自身 `/btw` 的追加用途和低请求目标。参考 [终端交互](https://code.claude.com/docs/en/interactive-mode)、[命令](https://code.claude.com/docs/en/commands)、[键位](https://code.claude.com/docs/en/keybindings)和 [设置及来源](https://code.claude.com/docs/en/settings)。
- Codex 源码采用 [Apache 2.0](https://github.com/openai/codex/blob/5228d94a27b7f84cae4854acbb5ea3b3e90dfeaf/LICENSE)。Claude Code 的官方仓库[许可证](https://github.com/anthropics/claude-code/blob/main/LICENSE.md)保留权利，本记录以 Anthropic 官方说明为行为依据。

## ThinkFlow 差距与实现建议

| 问题 | 本地依据 | 拟实现 |
| --- | --- | --- |
| 两个现代入口缺少可靠的显式换行 | 默认 [`PromptSession`](../../src/cli.py#L1181) 用库默认 `multiline=False`，全屏 [输入框](../../src/tui.py#L178) 显式关闭多行 | 采用 `prompt_toolkit` 自带缓冲区、历史和括号粘贴。`Enter` 提交，`Ctrl+J`、`Alt+Enter` 插入换行。能否识别 `Shift+Enter` 取决于终端。斜杠补全和选择面板优先消费相应按键。 |
| 普通输入、旁注和任务转向合成一种行为 | 默认 [提交与清理](../../src/cli.py#L1191) 和全屏 [输入分派](../../src/tui.py#L233) 各维护一份 `pending_btw`。运行中多个输入合成为一条包装文案，空闲 `/btw` 直接写 `agent.messages` | 让两入口使用同一 FIFO 控制器，普通消息保留独立的原文、顺序和用户身份，显示待发内容并能取回编辑。若增加显式转向，利用已存在的工具反馈后续请求消费输入；只有已经到达终点时才作为后续任务，界面提示实际状态。ThinkFlow 的 [`record_request`](../../src/conversation.py#L24) 继续保存真实用户原文。 |
| 取消、退出与状态变更缺少一致的任务所有权 | [默认分离的收尾任务](../../src/cli.py#L1202) 和 [全屏分离的收尾任务](../../src/tui.py#L197) 都在取消后继续消费暂存。`/resume`、`/new` 等可在执行中修改当前 agent，退出随后立即调用 [`agent.close()`](../../src/cli.py#L1743) | 共享控制器持有运行任务，`Esc` 停止并保留草稿与队列，退出等待取消落定再保存和关闭。成功后续发，停止或失败后暂停，沿用 [桌面既有语义](../v0.7-handoff.md#L15)。`/status` 即时读取，会话切换与运行配置修改在空闲处理。根据 `AgentLoop.stopped_reason` 和 `last_error` 判定成功。 |
| 活动、详情和成功摘要混排 | [`renderer.py`](../../src/renderer.py#L192) 可能显示开始、完成、汇总三份工具记录；[原生结果](../../src/renderer.py#L494) 只显示前八行；[状态栏](../../src/tui.py#L119) 的字符串裁切可能遮住活动、待发数和快捷键 | 状态首屏优先当前活动、待发数、停止操作。合并成功的相邻工具，失败及时展开。使用现有命令账本与输出引用展示可选详情，保持输入或滚动位置。 |
| 模型、配置与会话选择能力分散 | 默认已有 [三个选择器](../../src/cli.py#L1258)，全屏走通用斜杠分派；外层 [`config` 子命令](../../src/config_cli.py) 已支持无模型配置查询和修改 | 共用模型、强度和会话候选；会话按现有时间和首条消息筛选。配置在会话内复用字段解析及来源展示；改动继续沿用已支持的配置层级。模型列表用本地配置，主动 `refresh` 才查询提供方。 |

## 验收要点

用假提供方和隔离会话目录覆盖真实入口。确认多行键位及原文入库、连续入队顺序、停止后队列暂停、退出等待清理、会话切换与运行隔离、工具失败详情与原文引用。检查普通执行、选择器、状态和折叠无额外模型请求，并以实际键序覆盖 `Enter`、`Ctrl+J`、`Esc` 及选择器导航。已核对本机 `prompt_toolkit 3.0.52` 的 `PromptSession` 默认值。
