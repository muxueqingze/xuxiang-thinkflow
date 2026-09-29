# 修复后基准定向复核 · 2026-09-29

完整36次已结束，最终独立核对见后文。最初三个样本只读核验，随后完成原始用量、终态及全部样本测试工具证据核验，并对三个已发布JSON产物离线重评分。没有调用模型、重跑模型样本、修改被测代码或正式产物；只抽取工具事件、回执和公开终态，没有查看或摘录思考正文。本报告是本轮基准定向验收，不是Pro版本终审。

## 发现与证据限制

### P2：frame_decoder-thinkflow-r1 的末段成功回执不能证明测试通过

`artifacts/benchmark-20260929/formal-recovery-34119c6/frame_decoder-thinkflow-r1/trace.jsonl` 第50、77行证明执行过公共测试，但命令尾部使用 `& echo ... %errorlevel%`。第93行执行公共测试及附加测试，附加测试输出管道接到 `findstr`，随后又执行 `echo`。第100行的 unittest discover 同样接 `findstr`，之后还有两个 Python 检查命令。

这些命令的 `harness.json::tool_records` 最终退出码均为0，只能证明整个壳命令成功返回；不能据此还原各测试进程退出码。`src/executor.py` 第562—568行按壳进程 returncode 判定成功，未单独保存管道内各进程状态。正常测试输出没有保存到此样本的 trace/harness，stdout仅有包装程序完成摘要，因此现存记录不足以确认这些测试本身通过。

另外，第52及79行两次直接执行 `python tests\test_decoder_extra.py 2>&1` 均返回1。这是确实存在的附加测试失败，错误输出重定向后未进入保存的error字段。模型后来修改了附加测试（第86行），但最终复验改用了上述会掩盖测试退出码的命令。

可支持的结论：**主动发起了公共及附加测试；存在两次明确失败；最终测试通过证据不足。** 外部隐藏grader记录8/8、原生completed、进程exit 0、未超时仍分别保留，不把这个证据缺口改写成“模型没有测试”或“隐藏验收失败”。报告不能将该样本列为“已证实主动验证全部通过”。

### durable_inbox-pi-r1：未实施代码，也未执行测试

该样本 `stdout.jsonl` 第82—145行的全部五次工具调用为两次目录检查和三次文件读取，没有写文件或测试执行；最后assistant公开终态在第8349行为 `length`。进程exit 0不能替代正常assistant stop。

现存 `workspace/inbox.py` 仍为构造器及四个方法抛出NotImplementedError的桩代码；result记录0/8，八项错误均为 `NotImplementedError: Implement durable inbox`。因此记录为**未实施、未执行测试、未正常交付**，不能用“读过public_tests.py”作为测试执行证据。

### durable_inbox-thinkflow-r1：有可靠的实际测试执行与成功退出证据

该样本trace第33行直接执行 `python public_tests.py -v`，对应回执exit 0。第40行附加测试失败；第61行执行 `python public_tests.py && python tests\test_inbox_extra.py` 返回1，保存的工具错误文本明确显示公共测试 `Ran 1 test / OK`，附加测试 `Ran 27 tests / FAILED (failures=1)`。

后续第82及114行均执行同一个 `&&` 测试链，工具回执exit 0。第114行发生在最后一轮实现修改和附加测试修改之后；`&&` 链没有尾随echo或管道，现存两份测试文件均保留 `unittest.main()` 入口。因此可以确认**公共与附加测试实际执行，并在最终实现上成功退出**。成功时的完整测试输出未被保存，不能声称从输出重新核对了最终测试数量。

这里发生过附加测试修改；这项核验只证明执行与退出，不额外保证模型新增测试的质量。公共测试修改列表为空，外部grader8/8、原生completed、进程exit 0、未超时。

## 最终新增问题

### P2：两次ThinkFlow没有执行测试，一次改后没有复验

- `receipt_package-thinkflow-r2`：harness仅有read_skill、list_files、read和三次write，无bash或其他执行工具。trace第22/24/26行写入三个包文件后直接完成，没有实际测试证据。
- `repair_routes-thinkflow-r2`：仅读取、write、mkdir；trace第29行写router.py，第34行写tests/test_router.py，没有执行。写测试文件不等于运行测试。
- `interval_coverage-thinkflow-r2`：trace第34行公共测试成功，第41行附加测试exit 1；第54行又修改coverage.py，第56/58行修改测试后未执行。不能将早先公共测试成功用于证明最终代码已验证。

这三份外部隐藏验收均8/8且completed。它们暴露的是执行流程和验证证据缺口，不重判隐藏验收，不把“可确认主动测试成功”的比例称作实际功能完成率。

### P2：另两份ThinkFlow只有不可靠的壳成功回执

`durable_inbox-thinkflow-r2` 最终trace第80行串接 `& echo %ERRORLEVEL%` 和findstr，且成功输出未保存；`repair_routes-thinkflow-r1` 唯一测试命令（第32行）以 `&` 串接公共测试和附加测试，后者管道到findstr，匹配词还包含FAILED/ERROR。两者退出0均不足以证明测试通过。连同前述frame_decoder-r1，共三份应标“执行过测试，最终通过未知”。

## 完整测试证据矩阵

以下行号均指各运行目录的trace.jsonl（ThinkFlow）或stdout.jsonl（Pi/OpenCode）。只把测试命令及其回执/真实输出作为依据；最终代码有修改时要求之后的证据。这里核验的是执行及结果，不担保模型自编测试的覆盖质量。

| ThinkFlow样本 | 最后实现写入行 | 最终测试依据 | 分类 |
|---|---:|---|---|
| durable_inbox-r1 | 100 | 114：`python public_tests.py && python tests\test_inbox_extra.py`，exit 0 | 可确认成功 |
| durable_inbox-r2 | 27 | 80：公共/附加测试，echo/findstr串接，成功输出未留存 | 通过未知 |
| frame_decoder-r1 | 59 | 93/100：测试后接findstr/echo等，之前52/79明确失败 | 通过未知 |
| frame_decoder-r2 | 36 | 118：`python public_tests.py && python -m unittest discover -s tests -t .`，exit 0；晚于测试包修改111 | 可确认成功 |
| interval_coverage-r1 | 22 | 43：`python public_tests.py && python tests_extra.py`，exit 0 | 可确认成功 |
| interval_coverage-r2 | 54 | 34公共成功、41附加失败均早于最后实现修改，无后续执行 | 改后未复验 |
| pricing_refactor-r1 | 35 | 74：切换工作目录后公共测试&&unittest discover，exit 0；90附加边界检查exit 0 | 可确认成功 |
| pricing_refactor-r2 | 42 | 52：`python public_tests.py && python tests\test_pricing_extra.py`，exit 0 | 可确认成功 |
| receipt_package-r1 | 51 | 56公共测试exit 0，58附加测试exit 0，均晚于修改 | 可确认成功 |
| receipt_package-r2 | 26 | 无执行工具 | 未执行 |
| repair_routes-r1 | 23 | 32：公共测试 `&` 附加测试管道findstr，exit 0 | 通过未知 |
| repair_routes-r2 | 29 | 34仅写测试文件，没有执行工具 | 未执行 |

**ThinkFlow在最终代码上可确认主动测试成功的证据为6/12**；另外3份通过未知、1份改后未复验、2份未执行。其正式外部评分与正常终态仍为12/12。成功工具回执证明命令成功，但ThinkFlow没有保留成功时的完整测试输出，因此不据此猜测测试数量。

Pi与OpenCode按同样原则检查；虽然部分命令使用tail、Select-Object等管道，下面引用的是工具返回的 `Ran N tests / OK` 或断言脚本末尾通过输出，不单凭壳exit 0。各行证据晚于最后实现修改，且没有后续实现变更。

供发布读者复核的[verification-evidence.json](../../bench/harness_benchmark_20260929/reports-rerun/verification-evidence.json)保存36份分类、所用工具命令及日志行号、原生状态/退出码、必要测试输出节选和文件写入顺序。成功输出未留存时明确为null；Pi原生回执未提供数值退出码时也为null，不从isError虚构退出码。只导出工具资料，不含模型思考或回答；不是完整stdout备份。

| 题目 | Pi r1 / r2公开通过输出行 | OpenCode r1 / r2公开通过输出行 |
|---|---|---|
| durable_inbox | r1无执行；r2 20568：公共1+附加25均OK | 20：附加17+公共1均OK；34：公共1+附加18均OK |
| frame_decoder | 9954：公共1+附加35均OK；12809：公共1+附加34均OK | 36：公共1+附加32均OK；37：附加23+公共1均OK |
| interval_coverage | 4801：公共1+附加24均OK；4039：附加19+公共1均OK | 22：附加19+公共1均OK；15公共1为OK、23断言脚本输出all extra checks passed |
| pricing_refactor | r1无执行；r2 9645：附加20+公共1均OK | 48：公共1+附加9均OK；34：附加19+公共1均OK |
| receipt_package | 7391：附加21+公共1均OK；6953：公共1+附加13均OK | 29：公共1+附加22均OK；14公共1为OK、15断言脚本输出ALL EXTRA CHECKS PASSED |
| repair_routes | 9905：公共1+附加24均OK；8214：公共1+附加25均OK | 31：公共1+附加18均OK；24：公共1+附加14均OK |

Pi在最终代码上可确认主动测试成功为10/12（全部10次正常交付），另两次未实施/未执行；OpenCode为12/12。两者记录里也有早期测试失败后修正测试或实现，不能只看最后壳成功。例如Pi inbox-r2的15116行 `isError=false` 但可见 `FAILED`，因此本次没有把该标志当作测试成功依据。

## 完整36次数据独立复算

读取原始plan、manifest、每次result/meter及公开终态，再与reports-rerun比对。没有调用analyze/summarize汇总函数；从每条原始usage重新相加，结果如下。

| Harness | 正常交付 | 产物通过 | 请求数 | 输入 | 输出 | 总token | 平均秒 |
|---|---:|---:|---:|---:|---:|---:|---:|
| ThinkFlow | 12/12 | 12/12 | 108 | 1,918,537 | 204,919 | 2,123,456 | 76.8851666667 |
| Pi | 10/12 | 10/12 | 114 | 1,139,247 | 125,371 | 1,264,618 | 49.3254166667 |
| OpenCode | 12/12 | 12/12 | 120 | 1,781,451 | 117,265 | 1,898,716 | 60.3709166667 |

全体为**342请求、5,286,790 token**。ThinkFlow/Pi/OpenCode缓存命中分别1,624,448 / 1,087,488 / 1,670,016，非缓存输入294,089 / 51,759 / 111,435，推理125,495 / 73,973 / 70,578。缓存已包含在输入中，推理已包含在输出中。

- 36个唯一组合覆盖六题×三方×两次；计划顺序与原轮一致。manifest全部冻结文件指纹匹配。
- 六题各自六份prompt文件的实际字节哈希一致；读取文本、规范化Windows换行后重算的哈希也全部匹配result保存值。没有仅相信保存的prompt_sha256字段。
- 对照冻结tasks._SEEDS的公共测试源码，36份public_tests.py内容均未改（按文本换行规范化比较）；各result的修改列表为空。
- 所有请求序号连续无重复，均HTTP 200、finished、无transport_error，response_model均deepseek-flash；每条输入/输出/total/缓存/推理字段完整，total等于输入加输出。无abandoned请求。
- 每次最多22请求，最长执行151.234秒，所有请求启动都在300秒内。统一请求参数来自冻结meter强制设置；本轮没有保存完整请求体，不能声称重新逐包抓取验证了每项发送参数。
- 两个输出超过请求8192的观测保留：frame_decoder-opencode-r1第3请求9303；pricing_refactor-opencode-r2第3请求9579。8192是请求值，不是实际输出硬上限。300秒是执行/新请求预算，计量drain机制仍可能延长总观测时间。
- 全部逐次result原字段与导出一致，requests中的meter与原始文件一致；solutions所有Python文件集合及文本与工作目录一致；12份thinkflow-events与各自harness.json完全一致。
- ThinkFlow终态从harness.json核对，Pi/OpenCode从原生公开事件独立提取。两次失败均Pi：durable_inbox-r1为0/8、pricing_refactor-r1为3/8；两者exit 0但最后assistant为length，无写入/测试。pricing样本缺pricing.py，保留的原始实现偶然通过三项不等于完成重构。

成功同题同重复配对独立重算：ThinkFlow/Pi为10对，总token比1.2024347690731143，用时比1.3789597032704173；ThinkFlow/OpenCode为12对，总token比1.118364199806606，用时比1.273546451036716。均与新报告一致。

新旧ThinkFlow双方正常成功的7对：新1,287,165 / 旧1,419,067 token，比0.9070501956567237；用时比0.9982578937479363。因此这些共同成功题的token减少约9.3%，耗时基本持平。不能拿失败较多的原轮总用量直接推导单任务优化收益；原轮与新轮的monitor、run、worker及核心均有已披露变化，也不把效果单独归因于某一修复。

逐事件独立按“成功delayed操作完成时间早于同turn的stream_finished”重算共30次：write 20、edit 8、mkdir 2。与保存字段一致。这证明这些文件操作与流响应存在时间重叠，不证明30次测试，也不能单独证明总耗时收益。

## 发布产物抽验与结论

使用regrade.py的离线函数从新reports-rerun JSON在自动清理的临时目录恢复三份代表产物：receipt_package-thinkflow-r2（无测试执行）8/8、interval_coverage-thinkflow-r2（改后未复验）8/8、pricing_refactor-pi-r1（未实施）3/8。三份逐项判分及错误均与原result完全一致。这验证了成功和失败产物的导出可复核；不是补跑被测模型的测试流程，也没有重跑全部36份grader。

**新报告的完整性、用量、终态、配对及导出抽验通过；测试执行证据存在上列明确限制。** ThinkFlow与OpenCode在本组均12/12外部产物及正常终态，Pi10/12；但不能把ThinkFlow12/12写成“12次均完整执行并通过测试”。报告必须同时披露主动验证证据：ThinkFlow可确认6/12、Pi10/12、OpenCode12/12，未知不等于功能失败。保留原生提示/工具、缓存、输出超限、共同父Git目录、监控差异与小样本边界，不作行业排名或Pro全面验收结论。
