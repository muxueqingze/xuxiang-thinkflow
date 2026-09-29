# 生产实验观察器事故复核 · 2026-09-29

## 已确认问题

**P1：父进程的进度打印编码错误会终止正在生成的被测进程。** 冻结 `91e491167564f625334b812d6f0a422d2315b23e` 的 `monitor.py::print_progress` 用 `ensure_ascii=False` 向父进程 stdout 输出工具错误。`run.py::run_one` 在 communicate 的8秒轮询超时后调用它；打印异常进入外层 `except Exception`，记录 infrastructure_error 并执行 kill_tree。因此一个非必要观察器的显示错误改变了被测任务生命周期。

事故样本是计划第19项 `repair_routes-thinkflow-r2`。原 result 的错误为：

> UnicodeEncodeError: 'gbk' codec can't encode character '\ufffd' in position 270: illegal multibyte sequence

审查者使用现存 `live.json`，直接调用同一冻结版 `print_progress`，仅将 stdout 替换为本地 GBK BytesIO 包装，在 **270–271位置、U+FFFD** 精确复现相同异常。没有模型请求、生成重跑或冻结文件修改。

触发数据来自第3轮失败的工具命令 `python public_tests.py; python tests\\test_router_extra.py`。Windows cmd 把分号留在文件名中，错误包含无法按预期解码的中文路径；live.last_tool.error 保存了 U+FFFD。工具命令失败本应由模型继续恢复，不能与随后杀进程的观察器故障混成一个原因。

## 原样本状态与费用

- `result.json`：外部grader 8/8，exit_code=1，infrastructure_error明确存在，timed_out=false。`harness.json` 不存在，stdout/stderr文件均空。
- trace 最后是第4轮 `stream_started`，没有正常 `run_finished`；live仍为 run_active=true。**只能说中断时产物通过外部检查，不能判为正常交付，也不能按被测agent自身失败统计。**
- 独立从四条 meter.requests 重算：输入32,201、输出14,830、总计 **47,031 token / 4请求**；其中缓存输入16,000、未缓存16,201、推理7,527。推理已包含在输出中。
- 四次 HTTP 200、finished=true、usage完整、无transport_error。第4请求下游断开，但代理继续收完上游：17,713输入＋338输出＝18,051 token。该消耗已经包含在47,031中，不能因worker未收到完整结果而漏记，也不能重复加一次。

依据路径：`artifacts/benchmark-20260929/production-91e4911/repair_routes-thinkflow-r2/{result,meter,live}.json` 与 `trace.jsonl`。审查只读取工具/状态/用量数据，没有读取模型思考正文。

## 暂停与恢复核对

独立核对 manifest 中所有冻结文件的当前哈希，均匹配；source_commit仍是91e4911。plan第22项为 `interval_coverage-opencode-r2`，第23项为 `repair_routes-pi-r2`。runner遇到已有result先跳过，遇到只有目录但没有result则在创建workspace、meter及被测进程之前报 incomplete run。因此使用下一目录作暂停门，在既有任务结束后触发guard的方案符合现有代码执行顺序。

主会话操作记录称：第22项完成后由第23项门目录触发暂停，检查门中只有 OBSERVER_PAUSE.txt 后回收；随后以 `PYTHONIOENCODING=utf-8` 重启同名实验，已完成项跳过，剩余项继续。审查时确认门标记已不存在，并看到同名恢复进程；**本审查没有取得该暂停/回收和环境设置的原始本地日志，因此这些具体操作以主会话工具记录为依据，不能包装成审查者独立重放过。** 这不是已计量请求的缺口。

该环境修复不要求改动冻结源码。adapters的子进程环境采用白名单且另设 PYTHONUTF8=1，不透传父进程 PYTHONIOENCODING；故这里修复的是父进程进度输出编码，不改变子进程模型、工具、消息或预算配置。原失败result必须原样保留。

## 接受的补测与汇总口径

仅为此确定的装置事故另目录补跑一次可以接受，须在补跑前固定映射：`repair_routes-thinkflow-r2` 替代原同id装置无效样本；不得依据补跑成绩再次选择或追加重试。冻结源码、production模式、任务、harness、repeat=2保持一致，prompt SHA256应等于 `be4ea0ee2c779675c6e68d471bbee856e38f7d4d37cf819f0c3db2cd4653bf9a`。

最终必须同时呈现：

1. **36项预定评测**：35项原始有效样本＋唯一指定补测；补测即使效果差也照实纳入。如果补测又发生装置故障，必须标明未获得完整有效36项，不能冒称完成。
2. **原始实验记录**：原36次中的该装置事故完整保留，单列8/8产物检查与非正常终态，不让“8/8”覆盖infrastructure_error。
3. **全部实际成本**：原36次消耗＋补测消耗；等价于最终有效36项成本＋本次事故 **47,031 token / 4请求**。补测费用不是这47,031，而是补测自己实际产生的用量。耗时也分有效样本运行耗时、事故耗时56.344秒及总体墙钟时间，避免把恢复等待混成生成速度。

目前没有阻止恢复或一次性补测的不可接受证据缺口。正式汇总前仍需取得补测的manifest/实际请求条件、上述prompt哈希、终态与完整meter，保留主会话暂停/恢复操作摘要；这些尚未发生的结果不能预先认定通过。

计划在36项与唯一补测全部结束后再永久改为ASCII安全进度JSON并加入GBK离线回归，顺序正确。后续还应明确观察器显示失败不会再杀被测任务；本次复核不修改任何冻结文件。
