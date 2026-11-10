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
