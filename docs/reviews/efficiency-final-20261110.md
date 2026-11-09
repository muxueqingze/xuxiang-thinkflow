# 候选与最终交付实物验收 · 2026-11-10

候选实验目录：`bench/efficiency_20261011/artifacts/go-stream-batch-final-20261110`。计划为三题×ThinkFlow/Pi×两次重复，共12job；冻结提交`60a0087b14008014cf63b373ffd0e13e86d6a915`。候选TF pricing-r1暴露未闭合DSML开头漏检后，最终比较将使用修复后新源码全部6份TF，复用本候选组全部6份Pi。旧TF低值不替代最终结果。本记录按完成批次追加。

## 候选第一批：3份已独立核对

已亲读三个workspace的全部实现文件、原始工具回执、事件顺序及最终可见回答。独立公开检查均使用`python -B public_tests.py`，子进程显式设置PYTHONUTF8=1、PYTHONDONTWRITEBYTECODE=1，均1项OK、exit 0。没有访问网络或个人数据，未生成审查临时文件。

| Job | 完整交付判断 | 最后修改后的检查证据 | 请求 | 输入 | 缓存 | 未缓存 | 输出 | 总token |
|---|---|---|---:|---:|---:|---:|---:|---:|
| receipt_package-pi-r1 | 通过 | 最后修改后再次编译、公开测试OK、同输入输出路径及临时文件清理检查成功；最终报告与实现一致 | 10 | 75991 | 68736 | 7255 | 10252 | 86243 |
| pricing_refactor-pi-r2 | 通过 | 最后修改后公开测试OK、集中附加检查all extra checks passed；最终报告与动态调用共享pricing实现一致 | 8 | 59193 | 45440 | 13753 | 11635 | 70828 |
| pricing_refactor-thinkflow-r2 | 通过 | T3三个写入后执行Python补查，T4公开测试OK，T5完整收尾；最终报告与实际输出一致 | 5 | 13877 | 6144 | 7733 | 7720 | 21597 |

三份meter的每条raw_usage已独立求和，与各自result及meter totals逐项相同；三题均隐藏8/8，但完整交付判断同时依据最终文件、检查时间顺序和可见收尾，并未只采用accepted。

额外核对了全部检查输出中的失败标记及原断言：Pi receipt曾错误地把合法paid记录列为非法，后续修正检查并完整输出model checks passed；Pi pricing先后把silver数量9的18.99误写成21.10、把客户21.75误写成21.70，最终重跑完整检查块，涵盖前述两项以及动态替换断言，输出all extra checks passed。TF pricing的12项非法输入检查若不抛错会打印FAIL，实际完整输出中没有FAIL。上述判断不是仅据exit 0或自动计数。

ThinkFlow pricing-r2的T1合批read README/public_tests及dir，T2合批read checkout/reporting；T3三次write完成时间33.266/33.985/35.110秒，均早于同轮stream_finished的38.032秒，流中写入3次确实发生。同轮随后执行Python补查。五次请求tool_count均0；两个Pi样本分别全部tool_count=4。该TF样本没有parser/provider协议错误。

已核对manifest的模型deepseek-v4.1-flash、enabled/high、temperature=0、两次重复、固定任务版本及无输出/请求/任务时长cap。首次冻结文件复核：manifest列出的文件hash与当前实物全部一致。

本对照比较整套执行模式。ThinkFlow无native tools与Pi原生tools还对应DeepSeek不同的历史reasoning处理，不能把总差额全部归因于信息工具合批或流中写入。原三份实物检查成果保留；旧候选TF其余样本仅保留状态/计量，最终新源码6份TF再逐份验收，不下12job总体结论。

## 候选第二批：Pi pricing-r1通过，TF pricing-r1失败

Pi pricing-r1已亲读pricing/checkout/reporting全部实现。实际最后文件修改后有公开测试OK、非法输入/API/动态替换检查及5,000例随机精确舍入检查成功输出，最终可见交付说明与实现及回执一致；没有未解决的检查失败。独立UTF8/-B公开测试1项OK、exit 0，判完整交付通过。

TF pricing-r1只执行T1读取README和dir。T2原始可见正文是未闭合的`<｜DSML｜ calls>`、`<｜DSML｜ invoke name="read id="4" ...`及两个无tf前缀read标签，没有可执行命令，没有文件写入、检查或正常交付报告。response_structure确认T2有146字符text而非空响应。最终workspace没有pricing.py；独立公开测试exit 1，原quote返回21.11而契约要求20.05。隐藏3/8、accepted=false，真实交付失败；run报告completed不能使其通过。此为DSML开头形态漏检，不是empty响应修复回归，失败样本保留。

两份逐条raw_usage复算与result/meter一致：

| Job | 请求 | 输入 | 缓存 | 未缓存 | 输出 | 总token |
|---|---:|---:|---:|---:|---:|---:|
| pricing_refactor-pi-r1 | 7 | 64065 | 28800 | 35265 | 13875 | 77940 |
| pricing_refactor-thinkflow-r1（失败） | 2 | 2812 | 896 | 1916 | 93 | 2905 |

没有修改生成题答案或冻结源码；本轮独立检查没有留下临时文件。

## Pi参照追加：frame_decoder-pi-r2

已亲读decoder.py与18项test_decoder.py，核对原始完整检查输出、最终可见交付文本以及事件顺序。最后文件修改后运行公开及附加检查，原回执明确1项OK、18项OK，无FAIL/异常输出；最终声明与实际实现一致，判完整交付通过。

独立离线以UTF8、PYTHONDONTWRITEBYTECODE=1及-B分别运行public_tests.py和test_decoder.py，再次得到1项/18项OK，exit均0。逐条raw_usage复算：7请求，输入48704、缓存35584、未缓存13120、输出12572、总61276，与result/meter一致；隐藏8/8且计量完整。本轮未产生审查临时文件。

## Pi参照收口：frame-r1与receipt-r2

frame_decoder-pi-r1已亲读最终decoder实现，原先公开测试的missing frame terminator错误经修改后，实际公开测试和17项附加检查均成功，最终报告完整。独立UTF8/-B公开及17项测试复跑全部通过。逐条raw_usage合计：9请求，输入90587、缓存86016、未缓存4571、输出16111、总106698，计量完整且与原件一致，判完整交付通过。

receipt_package-pi-r2已亲读model/cli/init，公开与模型附加检查成功，ConnectError后自动恢复并执行CLI checks passed。stdout原事件序列为一次turn_end(error,502)→后续turn_end(toolUse)→最后message_end及turn_end(stop)，最后有完整Implemented/Verification completed交付文本；进程exit 0，第7请求供应商finish_reason=stop。独立公开测试也通过。因此按原始终态及实际产物判定为重试后完整交付，不是仍在错误中终止。

该样本raw result的accepted=false、harness_error=502/stopped_reason=error来自pi_worker对历史last_error只赋值不清除，不能覆盖原始后续成功终态。原始文件保持不改；审查派生完成状态为“自动重试后完成”，不为改善观感重跑该Pi样本。

receipt-r2共7请求，第5条http缺失、transport_error=ConnectError、finished=true且无raw_usage。其余6条已报用量独立合计：输入42573、缓存29440、未缓存13133、输出14262、总56835，与原result/meter一致。56835只能作为已报下限，未知请求不是0。后续比较只能使用完整TF用量与该Pi下限，并明确总体Pi计量不完整。

至此Pi全部6份已完成实物、原检查/收尾与计量核对。没有因评分或自动active_tests计数单独宣布完成。

## 待冻结DSML修复定向验证

独立MockTransport输入候选pricing实际未闭合DSML开头：一次协议错误→下一轮规范tf-read真实读取源码→第三轮收尾，未把DSML解析执行。围栏中相同文本和inline code示例单轮结束，无误报。AgentLoop三次请求均无tools且tool_choice=none；独立OpenAIProvider入口实际捕获请求也为无tools、tool_choice=none。没有调用外部模型，没有扩大源测试范围。

## 最终TF组：go-stream-final-20261110（40dfa30）

最终TF六份统一来自新冻结组，不使用候选TF较低值。第一份receipt_package-thinkflow-r1完整交付通过：已亲读model/cli/init，最后修改后的公开检查1项OK；首次附加检查的CSV字节排序期望错误（Ada应排在Ada, Jr之前），随后纠正并输出正确CSV和all extra checks passed。最终中文交付报告完整，说明与文件及实际输出一致。独立UTF8/-B公开测试再次1项OK。

T2三文件写入完成时间27.672/31.625/36.563秒，均早于同轮stream_finished=51.157秒；同轮先公开检查，再附加检查，失败后的下一轮复验成功。中间可见输出曾出现DSML闭合残片，但当轮已因真实检查失败进入恢复，最终不是残片终止；本样本provider_protocol_error事件为0，不据此声称DSML事件恢复触发。

四条raw_usage复算：输入14292、缓存0、未缓存14292、输出6978、总21270，全部有usage且与result/meter一致。四请求tool_count均0、tool_choice均none，缓存0是每条provider原始字段明确报告，非缺字段填零。该样本隐藏8/8，完整交付判定另有上述实物、检查与收尾证据。

Pi观察器修复只在turn_end更新本地last_error并将reason/error写入事件，没有变动模型请求、工具执行或消息历史。已核对`pi-observer-recovery.json`的两条真实事件回放：error→toolUse→stop保留历史错误事件但最终completed；terminal error仍failed。原Pi六份证据不改写，receipt-r2继续以原始终态派生“重试后完成”、用量为已报下限。

### 最终TF pricing两次重复：均通过（累计3/6）

两份pricing/checkout/reporting实现均已亲读，最后修改后实际公开检查和附加检查成功，最终交付报告完整。各自独立UTF8/-B公开测试均1项OK。r1附加检查直接ALL EXTRA CHECKS PASSED；r2经历三次自测期望错误，最终完整Fraction参照检查块已亲读，包含正确0.90/0.80乘数、大金额/舍入、非法输入、31.50客户汇总、动态替换及非变异断言，成功输出ALL EXTRA CHECKS PASSED，并再次公开测试OK，未以后续无关检查掩盖失败。

| 最终Job | 请求 | 输入 | 缓存 | 未缓存 | 输出 | 总token | 流中副作用 |
|---|---:|---:|---:|---:|---:|---:|---:|
| pricing_refactor-thinkflow-r1 | 4 | 10465 | 5376 | 5089 | 5678 | 16143 | 3 |
| pricing_refactor-thinkflow-r2 | 8 | 33985 | 16768 | 17217 | 11005 | 44990 | 4 |

每条raw_usage复算与result/meter一致，全部请求无tools且tool_choice=none。r2有一次client_disconnected记录，但转发器仍完成读取并记录该请求usage；全组计量完整。T2 response_structure显示重复tf-read id1/2，parser_error后T3使用3/4/5重新读取三个文件并继续，属于真实ID纠错，不是DSML或empty。中间可见单独tf-write闭合字面后仍继续检查、正常收尾，不能当作提前结束失败。

### 最终TF frame-r2：通过（累计4/6）

已亲读decoder.py状态机，核对最后写入后的编译/公开检查及tf-python附加检查原始输出。附加检查代码使用fails归集，非空会打印FAILURES并sys.exit(1)，只有无失败才打印EXTRA CHECKS OK；实际输出仅EXTRA CHECKS OK。400+400+300次循环与报告的1,100次试验相符。最终正常交付完整，不以评分或exit 0代替上述检查。

独立UTF8/-B公开测试通过；另用无文件stdin探针核对全部二段切分的多帧UTF-8载荷、错误头/终止符/非法UTF-8及永久失败，均通过。本轮没有新增产物文件。

4请求raw_usage复算：输入10938、缓存3968、未缓存6970、输出25671、总36609，与result/meter一致。T3实际输出22329，T4输入6420；各请求无tools且tool_choice=none。decoder写入在146.219秒完成，早于同轮stream_finished=164.000秒，流中写入确实发生。以上为完整模式观察，不将历史携带差异单独归因于合批。

### 最终TF frame-r1：通过（累计5/6）

已亲读decoder.py、全部实际检查输出和正常最终报告。没有新增产物缺陷。T5附加检查虽exit 0却打印`FAIL framing b'2:a,'`，原因是自测把合法的未完成帧误判为feed时立即错误；按契约应先返回空列表，再在finish时报错。T7实际修正此期望，输出`[] | finish raises OK`，同时检查巨量声明长度的分配（峰值1310字节）、完整帧后接部分帧、长度恰等于max_size。没有修改实现来迁就错误测试。独立UTF8/-B公开测试1项OK，另以无文件stdin探针复验该部分帧、失败黏性和精确上限，均通过。

8轮的实际用途：T1执行dir但伴随DSML读指令，进入协议恢复；T2合批读三个文件；T3写入成功后bash复用已使用id2，触发parser_error；T4又复用id1，仍失败；T5执行公开和附加检查；T6把修正检查输出为DSML，进入协议恢复；T7实际执行规范tf-python；T8正常收尾。后半段有真实格式恢复和自测修正/新增契约检查，没有发现检查通过后无目的重复整套验证。T3写入56.891秒，早于stream_finished=57.016秒。

逐条raw_usage复算：8请求，输入23354，缓存3840，未缓存19514，输出8028，总31382。与result/meter完全一致；T4虽client_disconnected，转发层仍取得完整usage，未按零计量。全部tool_count=0、tool_choice=none。协议错误2次、parser错误2次均保留在成本内。

### 最终TF receipt-r2：通过（累计6/6）

已亲读model/cli/init实物、全部检查代码及输出、最终中文交付说明。T4公开测试成功，附加检查错误地把`A`和`a`合并而失败；T5按区分大小写的契约修正期望后，从头重跑完整检查，实际输出ALL EXTRA CHECKS PASSED。包含舍入、void验证、输入不变、生成器、10,000条最大规格金额、CLI空格路径、CSV引号/LF、失败保留已有输出、缺失输入不创建输出、空输入替换及参数数量检查。独立UTF8/-B公开测试1项OK。最后修改后没有未收口的实现错误或检查失败。

T2的DSML开头被现有协议恢复捕获；T3规范read继续，T4三文件写入分别在21.781/22.016/23.531秒完成，早于stream_finished=30.203秒，同轮继续公开及Python检查。T5修正自测，T6正常交付。逐条raw_usage复算：6请求，输入19868，缓存10752，未缓存9116，输出8542，总28410，与result/meter一致；所有请求tool_count=0、tool_choice=none，usage完整。

### 最终12份实物与原始计量结论

最终40dfa30的6份ThinkFlow及候选组保留的6份Pi，均已独立查看最终文件、最后修改后的真实检查输出和收尾。12份均完成任务交付；Pi receipt-r2的原始accepted=false及ConnectError未知usage按前述事实保留，以自动恢复后的最终stop判断完成。候选TF失败及旧探索不混入最终比较，也没有挑选各轮最低值。

| 模式 | 请求 | 已报输入 | 已报缓存 | 已报未缓存 | 已报输出 | 已报总token | 未知usage请求 |
|---|---:|---:|---:|---:|---:|---:|---:|
| 最终ThinkFlow | 34 | 112902 | 40704 | 72198 | 65902 | 178804 | 0 |
| Pi参照 | 48 | 381113 | 294016 | 87097 | 78707 | 459820 | 1 |

以上逐请求raw_usage重新求和，与各result/meter totals核对一致；Pi数字是已报下限。ThinkFlow总token比Pi已报下限减少61.11%，请求减少14次（29.17%），每题每次重复的总token也均低于对应Pi已报值。本组达到完整交付同时低于Pi总token的目标。

最终manifest中30个src文件hash与现有实物逐项一致；两组任务版本、三题、两次重复、公共任务提示hash、Python/平台、deepseek-v4.1-flash、enabled/high、temperature=0及无输出/请求/时间cap条件一致。最终TF全部34请求实际wire为无tools且tool_choice=none；Pi48请求均携带四个原生工具。TF有实际流中写入和独立read合批，协议恢复3次、parser恢复3次也计入实际成本。对照结论属于整套执行模式，包含DeepSeek有无tools时的reasoning历史契约差异，不作单一机制因果归因。

本轮独立复验未生成临时文件或启动常驻进程；只更新本审查记录。机器汇总及完整workspace导出另做原件交叉核对。

最终导出交叉核对已完成：comparison.json的12个job、82条请求（包括Pi一条raw_usage=null）、78个原始证据文件hash，逐项与源文件一致；每个TF回合声明工具顺序及流结束前成功写入也按原events重算一致。workspaces.json的12个job共54份README/Python文件与源目录集合完全相同，UTF-8重编码逐字节、字节数、SHA256均一致，CRLF未被归一化。两JSON此时状态data_complete_review_pending，主会话可依据本报告登记review_complete，原始结果不改写。本轮范围内无阻塞交付问题，最终实物与计量验收通过。
