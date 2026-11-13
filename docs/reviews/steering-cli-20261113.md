# CLI 和 TUI 运行中补充验证

基线 `38a0807`，验证当前工作树。

- 默认终端和全屏 TUI 通过各自的真实 prompt_toolkit 输入入口接收消息，模型接口使用本机 TCP 回环 HTTP/SSE。首个响应流打开时，`tf-write` 已写出 `effect.txt` 并生成成功回执。此时发送普通消息、用 `/btw` 提交补充、用 `/queue edit` 粘贴带缩进的多行替换内容，再用 `/steer` 提交第二条补充。流仍打开时实际只有一次请求，界面仅回显初始消息。
- 放行 SSE 结束帧后，第 2 次请求含两条按序补充和写入回执，第 3 次请求才处理普通消息。两个入口各收到 3 次请求。补充生效时各回显一次，保存的用户请求保留换行和缩进。
- 取消前，`/queue` 在原队列位置标出当前任务补充。取消完成后，仍可查看、移除、编辑并恢复未消费消息。修改后的多行原文按 FIFO 继续发送，退出会等待活动任务取消和保存完成。
- 同一组测试覆盖默认终端和 TUI 的 `Enter`、`Ctrl+J`、`Alt+Enter`、多行粘贴和取消后发送草稿。

运行 `python -B -m unittest discover -s tests -p 'test_cli_interaction.py' -v`，6 项通过。追加队列显示和模型请求中的回执断言后，定向复跑 `-k steer` 与 `-k cancel_pauses_fifo`，均通过。可复现用例见 [test_cli_interaction.py](../../tests/test_cli_interaction.py)。会话和工具输出使用由测试自动清理的临时目录，回环监听在测试结束时关闭。
