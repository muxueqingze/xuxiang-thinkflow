# 长工具输出留存与回取独立验收 · 2026-11-11

## 问题清单

- **P2，已修复：账本裁剪使完整 read 失去既有精确去重。** `src/context.py` 的 `compact_history` 原先将账本预览裁剪标成 `record.truncated=True`，而 `AgentLoop._messages_with_runtime_status` 据此排除该 read。独立夹具保留完整 10,000 字符 read provider 消息及相同的完整 write 来源；账本裁剪前投影为 156 字符，裁剪后回到 10,037 字符。现已用 `payload_clipped` 区分账本显示裁剪；定向复核实际原生 read → `build_request_body`，裁剪前后反馈均为 470 字符（含回执与运行信息），去重保持。旧回执重放只剩预览时正确返回 `truncated=true` 及原文引用，归档中部仍可精确回取。
- **P2，已修复：导出缺少回执到原文文件的映射。** `src/desktop_service.py` 的 `export_session` 原先仅列 SHA256 引用与文件路径，无法把多个原文文件对应到 `receipt:N:stdout/stderr`。修正后定向调用真实 `export_session`，两个不同结果的短回执引用各对应唯一一行，并包含各自归档路径及准确字符长度。

## 实际验证

- 真实 DesktopService 和 AgentLoop；仅模型传输用 MockTransport。运行约 90,000 字符 stdout 与 stderr，尾部诊断保留；压缩后更换模型与 provider key；再次输出旧、新测试 key，两者均已脱敏。实际归档文件逐份检查，没有完整旧、新 key。
- 从上述会话 fork，将父 `.outputs` 目录改名为不可用；分支通过 `receipt:1:stdout` 精确回取中部中文。OpenAI 与 Anthropic 的 `_handle_traditional_tools` 实际 provider 反馈都含所请求正文。恢复分支后仍可回取，导出索引指向分支自己的目录。全过程四次 mock 模型请求。
- 真实 DesktopService.cancel 中止仍在 sleep 的 Python 进程；已收到的 40,014 字符输出保留，取消回执和归档持久化，恢复后仍能回取 `PARTIAL_MARKER`，没有 `.part` 残留。取消后的未确认副作用提示沿用现有恢复规则。
- 保存源根不存在时，复制失败明确上抛，旧 JSON 不变。另在新归档目标的 `os.replace` 注入实际失败，旧快照逐字节不变，临时 `.part` 清理，源归档仍可回取。
- 源码检查覆盖短结果提示开关、取消/超时结算、字符范围、摘要后原文引用、SDK 临时存储、SessionStore 相对根、历史快照及 fork 复制。未重复主会话的完整测试套件。

## 状态与清理

当前结论：**PASS，两项发现均已按原失败路径复核。** 限本功能节点，不是版本级终审。CLI `--no-autosave` 不绑定持久根的修正已核对源码，实际 CLI 运行证据由主会话补齐。

四轮独立目录 `temp/qa-output-recall-20261111`、`temp/qa-output-recall-20261111-r2`、`temp/qa-output-recall-20261111-r3`、`temp/qa-output-recall-20261111-r4` 均已核对归属后移入回收站，Test-Path 为 False。第一轮发现 Windows 文本 stdout 将 LF 转为 CRLF，改用明确 UTF-8 字节夹具后继续验证；这是夹具修正。测试进程与客户端均结束，无付费模型请求、额外依赖或源码修改；只保留本报告。

## 字面 query 增量复核

新增 `src/recall.py` 与两类原文入口、流式解析、命令指纹及原生反馈已定向核对，结论 **PASS**。

- 在内存中执行 90 组搜索，以 Python `str.find(query, offset)` 作为独立位置判据；覆盖 Unicode、CRLF、64Ki 字符块边界、长于单块的 query、指定 offset 与未命中。匹配位置一致；从返回头解析 start/end 后，反馈正文逐字符等于原文切片，均不超过 limit。
- 无 query 的原文范围返回格式逐字节相同，旧无 query 命令的指纹也与原字段序列计算结果相同。
- 流式属性中的 `&`、双引号和中文正确还原为字面 query。空 query 被拒绝后同文本 ID 可改正；已执行 ID 改 query 会明确冲突。
- 通过 `_handle_traditional_tools` 实际组装 OpenAI 与 Anthropic 的 provider 结果，均含 query 定位到的目标值。原始要求与工具输出复用同一 `recall_text`，输出文件以 `newline=""` 打开，保持 CRLF 字符位置。

本次增量验证只在内存运行，未创建归档或临时文件，无模型请求；没有重复生命周期或完整测试套件。真实模型第二次探针的成绩另由主会话按实际证据记录，不由这些接口测试推导。
