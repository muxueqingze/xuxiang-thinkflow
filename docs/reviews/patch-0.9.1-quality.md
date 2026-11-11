# 0.9.1 补丁独立定向验收

## 问题

### P1：桌面独立日志的 UTF-8 断尾阻止旧会话恢复（已修复，最终包复核通过）

`src/desktop_service.py` 的 `_journal` / `_unresolved_intents` 使用独立 `.jsonl`，不经过本次 `SessionStore` 修复。独立实测通过 `DesktopService` 和真实 `AgentLoop._execute_command` 执行中文路径 `touch`，保存成功回执；关闭后将最后 `tool_completed` 行截在中文 UTF-8 首字节。重新打开工作区时旧会话被跳过并新建会话；显式 `resume(old_id)` 抛出 `UnicodeDecodeError('utf-8', b'\\xe6', 0, 1, 'unexpected end of data')`。原工具文件存在，故不是工具执行失败。

修复：桌面读取也以完整换行作为提交边界，续写清除未完成尾行。根据已提交快照及一致的 command fingerprint，为 text/native 的 success、failed、conflict 回执向正确会话补写并同步落盘完成事件；CLI/SDK 同步结清已知回执，避免历史裁剪后旧开始事件重新变成未知执行。cancelled、unknown 与 UI revert 不借此消除待确认状态。

## 已检查

- `SessionStore`：核对同步事件观察者先写开始日志后执行工具、完成事件先存回执再写完成日志的实际调用顺序。独立 3 个边界检查通过：完整 JSON 但无换行仍忽略；下一次追加移除旧尾行且完整新意图恢复为 `unknown`；已有换行的损坏 JSON 明确报错。
- 独立恢复再中断检查：真实旧会话工具成功后截断完成行，切至另一新会话，再恢复旧会话。注入补写完成事件时再次截断并抛 `OSError`，确认当前新会话及其日志没有被改写；重试恢复后恰好补齐一条完成事件，保留成功回执，再次恢复不重复写日志，没有重新执行工具。
- Python：已检查冻结可执行路径、显式解释器优先级、`-X utf8`、官方 embeddable 原始 `._pth`、stdin bootstrap、原有 subprocess 输出/超时/取消链路及 OpenConsole 构建项。实现者的实际解释器退出码、异常、取消及完整输出证据见 [运行时验证](../evidence/packaged-python-runtime-20261111.json)。

## 正式 ZIP 独立亲验

产物：`artifacts/desktop-2026-11-11T13-42-08-603Z/ThinkFlow-0.9.1-Windows-x64.zip`，189,768,454 字节，244 条目全部 CRC 通过。将正式 ZIP 解至专属临时目录，执行其中 frozen backend，而非源码或构建目录。

1. 后端 PATH 只保留 `C:\Windows\System32;C:\Windows`，移除 `THINKFLOW_PYTHON`、`PYTHONHOME`、`PYTHONPATH`。通过本地 HTTP SSE 实际返回 `tf-python`，成功执行包内 `resources/backend/python/python.exe`；工作区模块导入、SQLite `6*7`、中文 stdin/stdout 与默认文件编码均通过。
2. 通过同一 frozen backend 的工作区终端接口创建真实 ConPTY PowerShell，实际计算 `6*7` 并将 `42` 写入工作区文件，随后关闭终端并确认终端列表为空，OpenConsole 无回归。
3. 关闭后端，将其真实 Python 完成日志截断于中文首字节，启动第二个解压件后端。恢复原 session ID，成功回执保留且不要求误确认；再由真实 SSE 驱动完整新一轮工具执行，两个回执均为 success，第一项工具未重放。
4. 共 3 个本地 HTTP 请求，两个后端正常退出，退出码均为 0。独立客户端、终端、HTTP 服务已关闭；解包目录及一次性探针已回收，正式证据保留于 [patch-0.9.1-quality.json](../evidence/patch-0.9.1-quality.json)。

## 结论

本次补丁独立定向验收通过；已发现的桌面同根问题修复并在正式 ZIP 中复核。没有付费 API 请求、全局 PATH 修改或真实用户配置操作，未提交或推送。本报告不承担版本级全面复审。
