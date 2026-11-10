# 工程质量改进定向验收 · 2026-11-10

## 问题与修复

本次源码变更未发现新增确定缺陷。新增编码测试原先把stderr换行写死为LF，Windows真实输出为CRLF；实现者已改为`result.stderr.splitlines() == ['诊断']`，独立回读确认，执行器保留原始换行。

## 已核验实物

范围：parser.py、executor.py、prompts.py、ThinkFlow SKILL.md及对应两个测试文件的当前差异。没有重跑全套或模型基准。

1. **无效命令不消费ID。** `_seen_ids`登记移到全部语义校验完成之后。独立运行Agent单轮链，分别从thinking/text输出“合法id1写入＋缺path的id7”，只执行id1并只产生该回执；下一轮同id7修正后正常执行。随后另一通道镜像id7只复用原回执，不新增执行；原通道再次输出合法id7仍被拒绝。两个方向均通过。没有自动改写ID，也没有合并不同ID的相同操作。
2. **专用Python子进程UTF-8。** 在父环境PYTHONUTF8=0、PYTHONIOENCODING=cp1252下真实执行多行源码，stdout得到“中文 café 🙂”、stderr得到“诊断𐀀🙂”，源码中的中文和非BMP字符经stdin正确传入；子进程内stdin/stdout/stderr.encoding均断言为utf-8。输出保留平台CRLF。没有创建脚本或产物文件。
3. **shell环境保持原义。** 同一Executor随后真实运行shell中的Python，读取到0/cp1252；父环境也仍为0/cp1252。`command_env()`每次返回新字典，专用Python的覆盖没有修改父环境或后续shell环境。
4. **工程方法保持可选。** 实际构造custom、use_builtin=false、disable_system_prompt=true三种配置，核心STREAM_PROTOCOL恰好一次，新增BUILTIN工程建议均不出现；custom只在自定义场景保留。核心只补充已落实的ID重发与UTF-8执行契约；断言检查、实物验收、短接力等方法保留在可选指引和按需skill中。

上述两处已复现根因及直接影响定向验收通过。本轮只写本报告，内存/离线子进程探针均已结束，无外部API请求、临时文件或常驻进程。

## Anthropic压缩配对追加验收

仅核对`_adjust_split_for_tool_messages`新增Anthropic user/tool_result识别。独立通过实际Agent单轮压缩与httpx.MockTransport截获Anthropic请求：assistant包含原thinking/signature和两个tool_use，后接两条user结果（含嵌套text内容、说明text块、is_error）。keep_recent_messages=2和3分别切到第二条、第一条结果，均回退至原assistant调用；请求里的调用及两条结果与源字典逐项完全一致，只压缩前面一条旧消息。未改变参数、正文、错误标记或reasoning/signature。

普通user/content文本块即使正文含tool_result字样，也不触发回退：同样5条历史、keep=2时正常压缩前3条，保留最近2条。请求模型、max_tokens及read工具schema保持配置值。此次无外部API、无临时文件，MockTransport客户端已关闭。该协议边界原失败项及直接影响通过，未发现新增问题。

## 297fbcf首轮frame实物失败

原件：`bench/efficiency_20261011/artifacts/go-engineering-quality-20261110/frame_decoder-thinkflow-r1`。隐藏评分7/8、accepted=false；不能作为质量改进成功样本。独立阅读最终代码、附加测试、回执及收尾，并用无文件探针确认：

- **P1，产物decoder.py:65：** finish漏检查_digits。feed(b'1')消耗缓冲后留下长度数字状态，finish却返回None；这是eof_closed_and_type_errors评分失败的直接原因。
- **P1，产物decoder.py:120：** 未校验逗号就返回载荷。实测feed(b'1:a')返回['a']，随后feed(b',')返回[]，违反仅返回完整帧的契约。
- **P1，产物decoder.py:56：** 先复制整个输入再解析，内部缓冲不受max_size限制。max_size=8、预先构造4MiB首字节非法输入，解析期间峰值4,195,589字节。

T3公开测试OK，但24项附加检查有3项失败；自测还错误地要求feed(b'1:a')立即报错，以及把合法b'0:,'当坏帧。T4仅385字符正文解释问题并声称正在修复，没有工具标签、执行、修改或复验，随后stop/completed。最终产物仍有上述缺陷，交付未完成。该样本不能证明新工程指引导致过度简化；它生成了159行实现和24项测试，确定事实是契约遗漏、错误自测和修复意图未落实。没有修改或代补这些原始产物。

## 孤立tf闭合标记恢复定向验收

未发现新规则的确定问题。先更正此前路径判断：AgentLoop实际使用RuntimeView，events.py:47覆写strip_command_blocks并调用SafeTextStreamFilter；不经过renderer旧的孤立闭合标签删除函数。故当前clean_text保留孤立closer，完整命令载荷则被整体剥离，现有路径可承载本次检测。

独立通过MockTransport运行CLI的create_agent→single_run（终端view、autosave=false）及headless的agent.run：先成功write，遇多个裸closer及后续正文，进入既有错误反馈；下一轮Python检查、再下一轮正常收尾。两条路径各3请求，账本及执行次数均仅write一次、python一次，不重放成功操作。此探针用执行桩避免改动文件，核对的是完整请求/反馈/账本流程；实现者新增真实write→Python文件断言用例另已阅读。

相同两路径的围栏示例、inline反引号示例和write载荷内closer均不触发，1请求正常完成。另独立核对单个裸closer：触发1次orphan_tool_close、无猜测执行、下一轮正常继续；带有效native read批次的响应仍沿TOOL_USE处理，未被新规则吞掉。均使用非DeepSeek的fixture model，确认新增tf规则不依赖DeepSeek特判。

可选工程指引的收敛仍在BUILTIN层；本次没有把工程方法移入强制协议。孤立closer修复定向验收通过，首轮frame的普通“正在修复”正文未执行问题不属于该规则覆盖范围。原试跑失败继续保留，不能由离线协议修复通过推导真实产物已通过。此次只更新本报告，未改源码/冻结产物，无外部API、临时文件或残留进程。
