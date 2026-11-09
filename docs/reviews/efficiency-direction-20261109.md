# 流式Python与执行方向定向审查 · 2026-11-09

## 确定问题

### P2 · 已关闭：最终CLI覆盖被提示生成阶段再次合并profile覆盖

`src/cli.py`的main先merge_active_provider，再应用`--no-native-tools`等CLI覆盖；新增resolve_system_prompt却再次merge。独立按真实调用顺序构造named profile的enable_native_tools=True，首次merge后把flat值设False：create_agent实际enable_native_tools=False、MockTransport请求全部无tools，但系统提示仍包含“当前还开放了原生工具”。

这会让no-native运行再次被指向不可调用的native能力。应在已有配置解析顺序中使用同一份最终有效配置生成提示和Agent，不让提示生成再次覆盖已应用的CLI优先级。

修复后仅复跑原失败场景：profile=true、首次合并后CLI覆盖false，Agent和wire仍无native，系统提示不再声称开放native，且保留tf-python契约。提示生成直接采用最终effective配置，原问题关闭。

### P2 · 已关闭：no-native下DSML恢复仍要求使用native工具

独立SSE返回`</｜DSML｜ calls>`，捕获第二请求：wire无tools，恢复文字却要求“请用已提供的原生工具”。新tf-python已经提供可用的canonical恢复路线，这条恢复指引也应依据实际工具通道选择，避免继续要求不可用能力。不需要执行DSML或增设完成门禁。

修复后同一MockTransport场景捕获第二请求：没有native工具要求，恢复指引明确tf-write、tf-edit、tf-python、tf-bash；两次请求均无tools，随后正常结束。原问题关闭。

## 新入口定向验证

独立MockTransport分片输出tf-python，前置围栏内同名标签仅作字面；真实Python通过stdin执行多行代码和引号断言，输出FRAGMENT_OK。实际只产生两次模型请求，均无tools；下一请求完整保留源码和stdout。与第一个tf-python同chunk的第二条命令因信息边界被记skipped，没有执行。

原native Python通过统一Executor.execute路径仍正常输出NATIVE_OK。文本tf-python经request_all授权拒绝未执行；取消真实sleep进程后进程消失，ledger记录cancelled。parser新工具复用现有FIFO/barrier/need_result分流，已核对现有两文件写后Python检查的真实子进程回归代码。

上述两项指引一致性问题均已定向收口，未扩大检查。没有外部模型调用、没有全量测试、没有修改核心源码；独立探针通过stdin执行，无临时脚本或遗留进程。

## 信息工具批量反馈定向复核

新路径复用原FIFO，将默认成功blocking结果集中到反馈边界；显式need_result/confirm仍保留即时边界。本次只检查新增路径，未确认新的确定问题。

独立MockTransport驱动实际Agent与真实Python进程：同一响应声明两条tf-python，二者都成功，下一请求分别只有一次VALUE_ONE/VALUE_TWO输出。第二条tool_completed事件时捕获深拷贝快照，此时结果尚未放入模型消息；新Agent恢复后一次请求完整带回两份结果，不重执行、不重复反馈。首响应finish_reason=length时同样完整保留两份结果；文本批次后混合原生Python调用时，三项均成功，下一请求两份文本结果也各出现一次。

真实Python抛ValueError时第一条failed、后续skipped；request_all授权拒绝时同样停止队列后续执行；注入Executor的unknown结果时第一轮即unconfirmed_side_effect，后续skipped，没有新模型请求。

专门验证流已结束、run仍在queue.close等待真实Python的取消时点：stream_finished事件已收到，执行尚未返回；取消后PID 89980消失，第一条ledger=cancelled、第二条=skipped，run状态cancelled。没有遗留子进程。

Context通过所有未注入成功blocking records归集，消息追加后标记每条记录，符合批次快照和完整反馈契约。上述测试未写工程文件或临时脚本，无外部模型调用、无全量回归。

## 2026-11-10 默认协议与配置往返

### P2 · 已关闭：桌面IPC及持久化遗漏enable_native_tools

Python DEFAULT_CONFIG已新增该字段，但desktop/main.cjs的configure参数白名单未接入；desktop/settings.cjs的DEFAULTS/normalizeConfig也未接入。独立执行真实validateParams函数，传true或false均报“不支持的请求参数”；真实normalizeConfig和SettingsStore.prepare.backend均丢弃字段。renderer设置表单目前没有该选项。于是桌面不能显式启用并保持该配置，手工保存在settings.json的值也会在load规范化时丢失。探针未读现有设置、未读取凭据、未写设置文件。

修复后独立复跑true/false：真实IPC校验均接受；normalizeConfig、SettingsStore.prepare/save/load/backendConfig均保留原布尔值（仅将fs替换为内存存储，未读写真实用户设置）。renderer已有checkbox、加载checked与提交布尔值，代码链一致。问题关闭。

### P2 · 已关闭：交互切换provider后默认提示未同步

独立create_agent从native=True的默认提示启动，实际apply_provider_profile切到native=False后，provider和wire均关闭tools，但build_system_prompt仍含“当前还开放了原生工具”。该函数只更新provider/client，未更新条件化默认提示。初始化时一致不代表切换后仍一致；固定配置模型实验不受此交互路径影响。

修复后独立连续切换false→true→true→false，管理提示中的native说明次数分别0/1/1/0，wire同步，工作区suffix始终保留。自定义提示经过相同切换保持原文，不被覆盖。问题关闭。

初始化CLI路径已通过实际argparse/main的最小离线验证（只替换配置读取、模型发现与SessionStore，不调用模型）：默认false、--native-tools、--no-native-tools、--native-tool read隐式启用、profile=true再CLI关闭、profile=false再CLI开启六种配置，最终provider、prompt、wire-tools均一致。审查未修改src/或bench；以上两个问题均已收口。

## 空结束响应错误链

独立MockTransport复现DeepSeek正常stop但仅有reasoning、无正文/调用：产生一次empty_assistant_response，经原错误反馈后第二轮正常结束。没有增加独立重试层。

对误报边界做真实执行核对：reasoning通道完整tf-write实际写出proof.txt，账本success，一轮结束且无协议错误；普通正文回答也一轮结束、无错误。因此原始text为空不会覆盖已完成的thinking命令。该判断只在END_TURN下运行，已有native TOOL_USE分支保持原路。

唯一临时目录temp/efficiency-review-20261110-gz8bsq8n及17字节proof.txt已核对归属、送入回收站并确认路径不存在。没有外部模型调用、没有全量测试、没有遗留进程。本节点复核通过。
