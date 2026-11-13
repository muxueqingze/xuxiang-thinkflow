# CLI 交互指南

`thinkflow` 打开默认终端交互，`thinkflow --tui` 打开全屏界面。两种入口都支持多行输入、运行中排队和工具详情。仓库内可用 `python run.py` 启动，参数相同。单次执行和 `--legacy-ui` 使用各自的交互方式。

## 编辑与发送

- `Enter` 发送。`Ctrl+J` 或 `Alt+Enter` 插入换行。终端拦截 `Alt+Enter` 时可用 `Ctrl+J`。
- 可以直接粘贴多行文本，换行和缩进保留到发送及会话记录中。
- 输入 `/` 查看命令补全。输入区和选择器都支持方向键。
- 状态行显示当前活动和待发条数。`/status` 可查看工作目录、模型、会话位置和上下文用量。

## 待发队列与停止

任务运行时，普通消息加入待发队列，等当前任务成功结束后按输入顺序逐条发送。用 `/btw 补充内容` 或 `/steer 补充内容` 给当前任务追加要求。当前模型响应和已开始的工具批次结束后，补充原文会进入下一次模型请求。同一批次收到多条补充时，按提交顺序一并发送。空闲时，补充命令按普通消息发送。

停止、运行失败或达到运行上限时，队列暂停。还未生效的补充留在原来的队列位置，继续发送时按普通消息执行。输入区的草稿仍可编辑。

| 操作 | 命令或按键 |
| --- | --- |
| 查看待发消息及编号 | `/queue` |
| 修改第 2 条待发消息，支持多行 | `/queue edit 2 新文本` |
| 移除第 2 条待发消息 | `/queue remove 2` |
| 任务结束后继续发送 | `/queue resume` |
| 停止当前任务并保留草稿 | `Esc`、`Ctrl+C` 或 `/cancel`。补全或选择器打开时，`Esc` 先关闭面板 |
| 退出 CLI | `/exit`、`/quit` 或 `/q`。有活动任务时会先等待取消收尾和已启用的会话保存 |

待发队列属于当前 CLI 进程，关闭后即清空。启用自动保存时，已经开始的消息会进入会话快照，可用 `thinkflow --resume` 恢复。切换会话前，先处理或移除当前会话的待发消息。

自动保存还记录工具执行前的意图和执行后的真实回执。流尚未结束时进程退出，恢复仍保留已经完成的操作；开始后未结算的操作标为 `unknown`，同 ID 调用返回该回执。`--no-autosave` 不写快照和执行日志，临时完整输出随 agent 关闭清理。

单次 CLI 的退出码为：成功 `0`，HTTP 错误、预算停止或未知副作用 `1`，用户中断 `130`。脚本可直接使用退出码判断结果。

用量中的 HTTP 次数包含实际重试，与逻辑轮数分别统计；缺失用量不算成零，部分数据只显示已知量。旧快照没有记录的 HTTP 次数显示未计量。`/savings` 是“每工具一往返”的估算基线，实际 harness 对照看 benchmark 结果。

## 查看工具记录

相邻的工具成功记录合并成一行，失败会即时显示。以下命令直接读取本地会话记录和保存的输出。

| 命令 | 显示内容 |
| --- | --- |
| `/cmds` | 工具列表、编号及执行状态 |
| `/cmds 3` | 第 3 号工具的详情、错误、结果摘要和已有输出引用 |
| `/cmds 3 stderr` | 从头读取第 3 号工具已保存的 `stderr` |
| `/cmds 3 stdout 8000` | 从字符偏移 `8000` 继续读取第 3 号工具已保存的 `stdout` |

用列表里显示的工具编号和详情里的输出名替换示例参数。

## 模型、强度与会话

默认入口空闲时，直接输入 `/model`、`/thinking` 或 `/resume` 打开行内选择器。方向键移动，`Enter` 确认，`Esc` 返回。两种入口均接受带参数的命令，例如 `/model provider/model`、`/thinking high` 和 `/resume 2`。全屏界面可先运行不带参数的命令查看候选，再用参数选定。

`/models` 查看当前模型目录。`/models refresh` 可主动刷新当前 OpenAI 兼容提供方的目录。切换模型、思考强度或会话时，先等待活动任务结束。持久修改使用 `thinkflow config`，用 `thinkflow config show --sources` 查看实际配置和来源。 [配置与嵌入](configuration.md)

### 思考强度

状态行和 `/status` 显示当前模型请求使用的思考设置。`/thinking` 应用到接下来的请求。切换模型时重新读取所选提供方的配置档位。

| 接口 | `/thinking` 的选项与请求行为 |
| --- | --- |
| DeepSeek 的 OpenAI 兼容接口 | `off` 明确关闭思考。`low`、`high`、`max` 开启思考并发送相应的 `reasoning_effort`。 |
| Anthropic Messages | `off` 关闭手动预算。可选 `light`、`medium`、`deep`、`max` 或填写数字。正预算从 1,024 tokens 起，且小于 `max_tokens`。状态行显示具体预算。 |
| 其他 OpenAI 兼容接口 | `default` 使用模型默认行为。显式选择 `none`、`minimal`、`low`、`medium`、`high`、`xhigh` 或 `max` 时，发送对应的 `reasoning_effort`。档位需由所选模型支持。 |

接口契约见 [DeepSeek 思考模式](https://api-docs.deepseek.com/guides/thinking_mode/)、[Anthropic 手动思考预算](https://platform.claude.com/docs/en/build-with-claude/extended-thinking)和 [OpenAI Chat Completions](https://developers.openai.com/api/reference/resources/chat/subresources/completions/methods/create)。
