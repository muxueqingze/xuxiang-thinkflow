# 效率改进定向审查 · 2026-11-09

## 问题与收口状态

- **P2，已修复并独立复核：压缩前去重丢失唯一正文。** 原路径先注入成功回执、按旧 reasoning 去重，再压缩历史；小保留窗下唯一正文消失，记录已标记 injected。最终代码改为先压缩再注入，并删除 run 入口提前注入。独立使用 MockTransport 捕获实际 run('continue') 请求：`max_messages=1, keep_recent_messages=1`、只有旧 reasoning 保存的 pending write 正文完整存在于最终请求。没有新增缓存层。
- **P2，效率目标未通过：默认隐藏六个文件工具的 r2 receipt 样本出现40轮无文件作用的 print 占位调用。** 63个有效模型轮、70工具，其中55个 bash；40个命令精确为 `python -c "print(...)"`。这不是 bash 分段写文件，也不是40次必要验证。占位轮为15、17–23、26–36、39–59。建议恢复默认 native 文件工具发现，保留流式协议及已有效的反馈、压缩、编码修正；先利用已有 native 同批多工具能力。不能把此探索写成效率改进成功。

## 直接影响验证

独立实跑默认六文件工具隐藏、read/bash保留、fallback遵循deny、显式allowlist优先，均与实现契约一致。既有解析错误、native参数截断、误用tf-*函数名的恢复入口仍在。这里确认的是分流实现正确，不是该默认策略的效率收益。

默认环境透传仅增加 PYTHONUTF8、PYTHONIOENCODING、PYTHONDONTWRITEBYTECODE，显式用户列表仍优先。经生产 AgentLoop→Executor→真实 Python 子进程输出 `ë中`，exit 0、UTF-8正文完整。

Go自有UA、同会话稳定header、to_snapshot/load_snapshot恢复session已独立实跑通过。

## 真实产物和计量

主要复核样本：[r2 receipt result](../../bench/efficiency_20261011/artifacts/go-efficiency-20261109-r2/receipt_package-thinkflow-r1/result.json)、[harness事件和回执](../../bench/efficiency_20261011/artifacts/go-efficiency-20261109-r2/receipt_package-thinkflow-r1/harness.json)、[原始usage](../../bench/efficiency_20261011/artifacts/go-efficiency-20261109-r2/receipt_package-thinkflow-r1/meter.json)。目录中 job 的 `-r1` 表示该实验内第一次重复，实验批次实际是 r2。

亲读最终 model.py、cli.py，并独立子进程执行 `python -B public_tests.py`，1项通过。装置隐藏判题8/8；原始工具回执包含公开1项和附加21项通过。产物通过与效率不通过应分别报告。

逐条 raw_usage 独立求和与 result/meter 相同：输入1,267,358；缓存1,212,032；未缓存55,326；输出21,061；总计1,288,419。64请求中63条有usage，另1条失败缺usage，计量正确标记 partial，不能当成完整账单。

T24连续流式写3个实现文件并同轮调用bash；T37流式edit及write自测，随后验证通过。共5个delayed副作用，严格按保存时间小于stream_finished计3个；另1个与结束时间在四位小数精度相同，1个在队列drain时完成。T38的demo命令确有SyntaxError，随后21轮print占位并没有修复或运行demo。三次工具失败分别为两次重复mkdir和该语法错误。

此前另亲验 r1 baseline pricing：公开测试通过，11条usage独立求和为输入184,422、缓存149,248、未缓存35,174、输出17,230、总201,652；全部200且finished，消息/系统转发hash相同。r1新版receipt的13轮中，7→8/9→10是自测预期错误的修复与必要复验；11→12是GBK打印失败后改hex，不能将其全算作无意义重复。

## 结论

原P2与编码问题已定向收口；当前r2 schema实验不能通过效率验收。跨r1/r2比较须明示批次与源码变化，r1失败和缺usage样本保留，不用成功产物掩盖请求数及用量退化。本审查不是版本级全面终审。

审查仅写本文件；没有修改正式源码、调用模型或读取凭据。独立探针通过stdin执行、测试使用-B，未留下本轮临时文件或子进程。

## 后续：原生 python(code) 定向审查

默认 native 文件 schema 已恢复；前文隐藏 schema 的结论只针对 r2 实验，不代表当前默认配置。新增 python 工具保持 exec/high/blocking，经原授权入口和 Executor 进程管理运行，多行源码通过stdin传入独立解释器。

**P2，已修复并独立复核：非字符串code导致回执落账异常。** 原独立调用 `_execute_traditional_result('python', {'code': 123})`，`command.cmd`被赋整数，最终context.record的哈希计算抛TypeError，没有工具回执。修复沿用原字段契约，只让字符串进入cmd，其余设置input_error。独立复跑同一输入：返回 `Invalid tool arguments: code must be a string`，success=false，账本恰好1条且status=failed、cmd=null，子进程创建次数为0。

独立运行结果（全部本地进程，无模型请求）：

- 100KB多行stdin执行成功，包含断言、双引号及`ë中`输出，无脚本文件。
- 同一native batch两次python均成功，第二次确认没有第一进程变量，符合每次独立进程契约。
- request_all拒绝时授权回调收到源码，没有创建任何子进程。
- 主Python及其派生子进程取消后，PID 82696/45192均消失，账本状态cancelled。
- 超时返回timed_out=true、success=false，保留超时前stdout；复用同一执行器的bash正常exit 0、输出42。
- batch首python抛ValueError，返回exit 1和完整traceback；后续python记skipped且未执行。

新能力的进程、授权、结果及batch链路通过上述定向验证，code类型问题已收口。本次只复核该失败项，没有重跑其他验收。此节不据此声称模型实测已节省token。

## 后续：read请求投影与DSML恢复

本次只审查这两项新实现，未确认新的P0/P1/P2。

read投影仅改请求副本，来源是实际保留消息中的native write.content或完整成功read。独立实际读取src/feedback.py三次，再经Agent的build_request_body：第一份保持全文，第二、三份均直接引用r1，没有引用链；原回执header、附加suffix与持久messages均保留。将本地ledger payload预算压至200后，output_chars仍正确划定原消息正文，投影结果不受裁剪影响。随后强制压缩到仅保留最后read调用/结果，重建请求恢复完整原正文。已核对失败/truncated read不进入complete_reads，短正文不会被更长引用替代；Anthropic块结构不参与此OpenAI投影。

DSML在DeepSeek END_TURN收口时检查已剥离tf块、排除Markdown围栏的余文末尾，不执行该协议，不删除reasoning，也不改已有TOOL_USE批次路径。独立MockTransport驱动实际Agent.run验证：

- 三段reserved闭合残片触发1次协议错误，下一轮真实python进程输出42，第三轮正常结束。
- 同样残片与完整native调用同轮出现时，native python成功执行；2轮结束，未被协议恢复吞掉。
- Markdown围栏内字面残片1轮正常结束，没有协议错误。

另已阅读现有定向SSE测试，确认其核对了先前成功tf写入不重放、下一轮native继续，以及写入文件正文内DSML字面不触发。此处只记录实际协议错误恢复和请求中精确副本去重的正确性，不把未完成作品标为通过，也不由此推定总token收益。

## 后续：按需schema与单DSML闭合标记

当前默认schema为read/write/edit/bash/python/read_skill/load_tools，其他原生工具通过load_tools开放；write/edit首轮保留，与前文失败的隐藏文件工具实验不同。新增additional_native_tools只改变schema发现范围，原registry执行和权限链不变。

发现并收口两处P2目录问题：初版仅列工具名称，丢失CustomToolConfig.description提供的用途信息；目录只过滤deny，可能宣传显式allowlist不允许加载的工具。修复后独立构造非自解释custom名称lookup_xyz：默认目录同时保留名称和用途，不携带参数；显式read/load_tools时不列该custom或glob；显式加入custom后只列允许项；deny该custom后目录不再出现。两项均已关闭。

独立MockTransport三轮调用load_tools→grep→完成，确认首轮grep未在schema中、下一请求已出现、真实grep成功匹配src/feedback.py；首轮核心write/edit保留。snapshot恢复保留加载结果，disabled工具加载返回失败，显式native_tools的最终schema严格按allow−deny。当前fallback测试的流式tf-write/tf-append FIFO契约、默认write/edit及恢复触发断言已核对，未改写执行路径。

单DSML parameter/invoke/calls三种closer分别独立验证：裸露尾部各触发一次协议错误并进入下一轮；围栏内各正常单轮结束；与完整native read同轮时各正常执行read、两轮结束，无协议误报。写入标签正文经实际strip_command_blocks处理后不留下这些字面标记，原文件内字面回归仍保留。

本节点定向验收通过，未剩余确定问题；未调用外部API、未运行全量测试。上述结论只针对本节点行为，不代替后续固定题的工程结果和token实测。

## 最终冻结批次：receipt样本

样本来源为[go-lazy-tools-20261109/receipt_package-thinkflow-r1](../../bench/efficiency_20261011/artifacts/go-lazy-tools-20261109/receipt_package-thinkflow-r1/result.json)。亲读最终model.py、cli.py及__init__.py，独立执行`python -B public_tests.py`：1项OK、exit 0。原始harness回执包含`python public_tests.py && python extra_tests.py`成功输出，分别1项和18项OK，exit 0；最终收尾文本中的文件清单和检查声明与实物及回执一致，无DSML残片。隐藏判题8/8、公开测试未改。此次产物定向核对未发现确定问题。

逐条原始usage复算如下，与result.json和meter.json逐项一致：

| 请求 | 输入 | 缓存输入 | 输出 |
|---|---:|---:|---:|
| 1 | 2014 | 0 | 79 |
| 2 | 2726 | 1920 | 40 |
| 3 | 2917 | 2048 | 1817 |
| 4 | 4921 | 2816 | 2970 |
| 5 | 7957 | 4096 | 241 |
| 6 | 8315 | 6144 | 779 |
| 合计 | 28850 | 17024 | 5926 |

未缓存输入11826、推理1684（包含在输出内）、总token34776。6请求全部HTTP 200、finished且有完整usage/cache数据，实际模型均deepseek-v4.1-flash，enabled/high、temperature=0、没有输出cap，同job只有一个session标识，转发前后消息/系统hash相同。8次工具调用均成功；本样本全部文件写入走native，流中写入计数为0，不能把该样本节省归因于流式写入。

本节仅验receipt这一完成样本，其余最终题目及完整比较待独立核对。没有改动样本文件或生成本轮临时产物。

## go-lazy-tools另两题与失败预算

pricing_refactor最终pricing/checkout/reporting已亲读，独立`python -B public_tests.py`为1项OK。原始python回执末次输出`all extra checks passed`、exit 0；之前两次附加检查失败分别是导入不存在的prism_check、自测期望值错误，均保留在记录中。独立raw_usage求和：输入47836、缓存31872、未缓存15964、输出6987、总54823，与result/meter一致。

frame_decoder的隐藏8/8与accepted=false并不冲突。实际T12通过python修改自测文件后，T13误用cmd.exe分号，Python把`public_tests.py;`当文件名，exit 2；T14公开测试成功，但附加测试错误地要求数据`b'7'`在max_size=5时不报超长，exit 1；T15原生edit同一自测文件得到conflict。旧默认连续失败3轮预算随即停止，最终stopped_reason=max_consecutive_failures。该样本没有正常完成恢复或收尾，不能列为成功交付。

frame计量独立复算：输入297127、缓存259584、未缓存37543、输出20800、总317927，与result/meter一致。两个样本均有完整usage。本批只能记录receipt/pricing通过、frame运行失败，不能把整组三题标为整体通过。

将max_consecutive_failures改为默认None、显式数字预算可选，符合本次解除未明示恢复次数限制的目标；普通错误仍按原反馈继续，unknown副作用仍停止。此调整本身不等于修复模型自测或命令错误。最小验收重点是：默认连续4次失败后可继续成功、显式3仍停止、成功轮重置计数、create_agent透传配置、unknown停止优先。版本冲突仍应由模型重新读取解决，不自动将python写入当作已读版本。以上为对拟议调整的判断，尚未在本节宣称该预算修改已验收。

预算修改落地后已独立定向收口：通过create_agent构造真实Agent、仅替换单轮结果以验证run控制逻辑。默认未配置及显式None均在4失败后第5轮成功completed；显式3恰在第3失败停止为max_consecutive_failures；失败/失败/成功/失败/失败/成功序列完整执行6轮并completed，说明成功轮重置计数。显式预算1下调用原_stop_for_uncertain_effect处理unknown，立即以unconfirmed_side_effect停止，不被预算原因覆盖。create_agent有效配置分别为None/3/None/1，与传入契约一致。本项验收通过，上一批frame失败记录保持原状，不重分类为通过。

## 最终交付组：go-recovery-20261109

最终比较只取[go-recovery-20261109](../../bench/efficiency_20261011/artifacts/go-recovery-20261109)的三题，与[go-efficiency-20261109-r1](../../bench/efficiency_20261011/artifacts/go-efficiency-20261109-r1)固定baseline/Pi三题对照，不用上一批较低值替换最终样本。

亲读最终receipt的model/cli/init、frame的decoder、pricing的pricing/checkout/reporting。三题各独立执行`python -B public_tests.py`，均1项OK；frame的30项附加测试还独立用无管道命令复跑通过。原始回执及最终可见收尾一致：receipt修复自测缺失导入后21项OK并完成10,000记录检查；frame修复自测期望后30项OK，另有400个有效流和1,500个垃圾流检查成功输出；pricing纠正附加算术期望后集中检查成功。receipt使用more、frame使用findstr的shell管道本身不证明测试退出状态，因此同时核对了原始明确OK输出，frame另以独立无管道运行交叉确认。三题均隐藏8/8、accepted=true、completed，无未完成的收尾残片。

全部9份meter逐条raw_usage独立求和，与对应result/meter合计逐项相同，usage/cache均完整，所有请求均200且finished。最终31请求实际模型均deepseek-v4.1-flash，生成参数enabled/high、temperature=0、无输出cap；转发前后消息和系统hash一致。

| 组别/题目 | 输入 | 缓存输入 | 未缓存输入 | 输出 | 总token | 请求 |
|---|---:|---:|---:|---:|---:|---:|
| 最终 receipt | 123526 | 95488 | 28038 | 8317 | 131843 | 11 |
| 最终 frame | 198570 | 172928 | 25642 | 19178 | 217748 | 13 |
| 最终 pricing | 70888 | 52992 | 17896 | 13459 | 84347 | 7 |
| **最终合计** | **392984** | **321408** | **71576** | **40954** | **433938** | **31** |
| baseline合计 | 492189 | 382592 | 109597 | 50031 | 542220 | 32 |
| Pi合计 | 216227 | 187776 | 28451 | 33284 | 249511 | 27 |

最终组较baseline总token下降19.97%、缓存输入下降15.99%、未缓存输入下降34.69%、输出下降18.14%。较Pi总token仍高73.92%、缓存输入高71.17%、未缓存输入高151.58%、输出高23.04%。只能据此说本轮整体配置相较旧版有所下降，不能宣称超过Pi。

实际worker会记录provider_protocol_error、parser_error、context_projection；最终三份harness这三类事件各为0，delayed完成/流中写入同为0。因此该组的改善不能归因于DSML恢复、read投影或流式写入。这些能力的正确性由前面的定向验证支持，最终模型样本没有触发它们。

最终三题实物与计量定向核对通过，历次失败探索继续保留。本轮只写审查文档，离线执行使用-B，未产生本轮临时文件或遗留进程。

机器汇总[comparison.json](../evidence/efficiency-20261109/comparison.json)已独立交叉核对：9份比较记录共90条请求，其逐请求raw_usage、生成字段、转发hash，以及usage、grade、observations均与原始meter/result一致；final_frozen_source与最终manifest的files完全一致。all_experiments的27份已有result均逐项核对，没有遗漏或多报原件；另1条r2 pricing未开始记录的目录仅有PAUSED-BEFORE-JOB.txt，无meter。没有usage的数字改为null而非0；projection receipt标incomplete、lazy frame标failed、r2中止样本标cancelled，均保留。最终所报总量仍是go-recovery整组三题433938，不用探索中的更低值替换；Pi仍更省，三种新增机制在最终组事件为0，不作机制因果归因。
