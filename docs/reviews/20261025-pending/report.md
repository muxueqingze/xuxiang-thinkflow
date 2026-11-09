# ThinkFlow 后端与状态逻辑只读审查 · 2026-10-25

## 确认问题

### TF-01 · P1 · 可选交付检查绕过 shell 权限、环境过滤和取消清理

- **位置：** [src/delivery.py:107](<D:/the things of mine/项目库/agent/thinkflow/src/delivery.py:107>)，调用入口 [src/agent_loop.py:1234](<D:/the things of mine/项目库/agent/thinkflow/src/agent_loop.py:1234>)。
- **触发：** CLI/配置启用 `delivery_verify=True`，当轮成功写入位于 package.json 项目下的文件。默认关闭，Desktop DEFAULT_CONFIG 未暴露此开关；不能把本问题表述为默认桌面启动即触发。
- **实际：** `verify_delivery` 直接启动 `npm install` / `npm run build`；`_run` 不经 `_authorize_tool` 或 `Executor._bash`，不检查 `bash_policy`，不传过滤环境，也没有取消时终止子进程的处理。构建命令会继续执行 package.json 指向的脚本，命令字面固定并不使其没有副作用。
- **预期：** 开启交付检查不能解除独立配置的 `bash_policy=off` 或逐次批准要求；子进程环境与取消语义应与其他 shell 工具相同。
- **实测：** [probes-v2.json](evidence/probes-v2.json) 的 `delivery_bypass`：设 `bash_policy='off'`、`approval_mode='request_all'`，仍产生 `marker.txt`，内容为 `DUMMY_FIXTURE_ONLY`，`approval_requests=[]`。该值仅为探针设置的假环境变量，不读取真实密钥。预建空 node_modules 避免 npm install，实际只执行本机 `npm run build → python check.py`。
- **取消复现：** [delivery-lifecycle.json](evidence/delivery-lifecycle.json) 的 `cancel`：`absent_when_cancel_returned=true`，随后文件出现，内容 `STILL_EXECUTED`。测试直接调用生产 `delivery._run`，子进程写入 started 后等待 0.5 秒，再写目标；取消协程返回后仍完成写入。
- **影响：** 这条辅助执行链的权限与主工具链不一致；用户取消或运行超时返回后，构建进程仍可能写工作区。环境变量继承也违反受限 shell 的隔离约定。
- **最小修复方向：** 把自动交付检查委托给现有 Agent 授权和 Executor shell 生命周期，不再维持独立裸 subprocess 实现；保留结构化检查结果。无须为此重写队列或更换框架。
- **验收任务：** 复现 fixture 在 bash off 下不得生成 marker；request_all 必须能拒绝；假环境变量不得进入受限子进程；取消返回后不得出现延迟 marker；shell 检查结果需有可核对回执。取消清理与权限问题同源，计一个任务，不人为拆成多个高优先级问题。

### TF-02 · P1 · 已失败的交付验收没有持续状态，模型只回复完成即可结束为成功

- **位置：** [src/agent_loop.py:1233](<D:/the things of mine/项目库/agent/thinkflow/src/agent_loop.py:1233>) 至失败分支 1244，以及 [src/agent_loop.py:1259](<D:/the things of mine/项目库/agent/thinkflow/src/agent_loop.py:1259>)。
- **触发：** 启用 `delivery_verify=True`，实际构建检查失败后，下一轮模型只输出文字，没有新的文件操作；另一条触发是修复次数已耗尽。
- **实际：** 检查是否需要执行只看 `turn_records`；上轮失败仅被追加为模型消息，没有 run 级未通过状态。下一轮没有新写入便跳过检查，`run` 以 `stopped_reason='completed'`、`last_error=''` 结束。修复次数耗尽分支同样只渲染错误，然后正常返回。
- **预期：** 已由执行器确认失败的验收，必须经成功复验或明确的失败结束才能收口。纯自然语言不能使已有确定失败消失。
- **实测：** [delivery-lifecycle.json](evidence/delivery-lifecycle.json) 的 `default_failure_limit` 使用默认 `max_delivery_fix_attempts=3`；本地 `check.py` 固定 `SystemExit(7)`，第一轮写文件、第二轮只回复 Done，结果 `requests=2`、`stopped_reason='completed'`、`last_error=''`。没有第二次成功检查。另 [probes-v2.json](evidence/probes-v2.json) 的 `delivery_failure` 将次数设 0，确实发出“交付前验证失败，已达到自动修复上限”，仍报告 completed。
- **影响：** 用户和上层队列无法依赖成功结束状态区分“已通过交付检查”与“有确定失败未处理”。当前桌面默认未启用该功能，因此本次不声称默认 Desktop 队列已复现此路径。
- **最小修复方向：** 保存本次 run 未通过的交付检查及对应项目；结束时处理未解决失败，不仅看末轮写入。达到次数限制设置明确 stopped_reason/last_error 并退出失败。不要用自动无限重试掩盖状态缺失。
- **验收任务：** 固定 exit 7 → 下一轮纯文本、次数耗尽、后续修复并真实通过三个分支；前两者不能 completed，最后一者可正常结束；真实 exit code 需留证据。

### TF-03 · P2 · 一帧中的后续解析错误吞掉前面的合法命令，并占用从未执行的 ID

- **位置：** [src/agent_loop.py:922](<D:/the things of mine/项目库/agent/thinkflow/src/agent_loop.py:922>) 的 `feed → 检查 new_errors → break` 顺序；text 通道同型位置 [src/agent_loop.py:951](<D:/the things of mine/项目库/agent/thinkflow/src/agent_loop.py:951>)；ID 提前登记在 [src/parser.py:342](<D:/the things of mine/项目库/agent/thinkflow/src/parser.py:342>)。
- **触发：** 同一 thinking SSE delta 含完整合法命令，后跟缺 path 等可以在 feed 阶段确认的格式错误。例如：

  ```xml
  <tf-write id="1" path="marker.txt">EXECUTED</tf-write><tf-write id="2">bad</tf-write>
  ```

- **实际：** parser 已返回 id=1 的合法 Command 并登记 seen ID，但调用层发现同一 feed 的错误便在 dispatch 前 break；id=1 既未执行，也无 skipped/失败回执。模型下一轮重发合法 id=1，会得到 duplicate_id。相同内容拆成两个 thinking delta 时，id=1 正常执行。
- **预期：** 已解析的命令、错误与执行/跳过回执按流内顺序衔接，不能由 SSE 分片边界决定早先合法命令是否无声消失。若策略决定跳过，必须留下明确 skipped 信息，不能把未执行 ID 当成成功/已执行 ID 处理。
- **实测：** [probes-v2.json](evidence/probes-v2.json)：`coalesced.ledger=[]`、`marker=null`，先 `missing_path` 后 `duplicate_id`；`split.ledger=[{id:'1', status:'success', ...}]`、`marker='EXECUTED'`。两者运行相同生产 parser/AgentLoop/Executor，仅输入帧划分不同。原 [probes.json](evidence/probes.json) 使用 text 通道、未插入换行，text gate 将两帧缓冲合并，不能作为分片差异证据；保留为一次失败尝试，正式依据为 v2 的 thinking 对照。
- **影响：** 模型纠错时出现缺文件、无回执又无法使用原 ID 的恢复障碍，增加重试和上下文；属于确定的执行/解析衔接问题。没有证据证明它解释了历史付费基准的某个具体失败。
- **最小修复方向：** 让 parser 交付带顺序的命令/错误边界，或至少先保留错误前合法命令，再停止后续命令；未执行项明确记 skipped。ID 的解析去重和执行去重分工需清楚，避免简单清空所有 ID 导致既有副作用重放。
- **验收任务：** 同内容单帧/分帧、thinking/text 两通道、合法命令后 missing_path、错误后剩余命令、同 ID 内容冲突；核对文件和账本，而不只看 parser 返回数。

## 不计新增缺陷的观察与已知问题

- 原生工具设置的行为已经实测：即使 schema 只公布 read、隐藏 write、或完全关闭原生注册，供应商返回 native write 仍会执行。见 `probes-v2.json` 前三组。**本报告不把它算成确定权限漏洞：** CLI [src/cli.py:1709](<D:/the things of mine/项目库/agent/thinkflow/src/cli.py:1709>) 明确将这些参数定义为“不注册 / 只暴露 / 隐藏”，不是 SecurityPolicy 授权开关；原有 read-only/bash/批准策略仍独立生效。若以后要将这些设置提升为执行白名单，须先确认契约，不能借审查擅改。
- [docs/efficiency-handoff-20261011.md](<D:/the things of mine/项目库/agent/thinkflow/docs/efficiency-handoff-20261011.md>) 已列明 3 个未修复问题：长 write 的 8K 预裁剪导致去重不生效、恢复快照中未反馈 verify 输出经 4K 注入截断、failed tf-bash 反馈截断/重复 stderr。本轮确认了代码路径，未重复运行完整旧复现，也未将其计为新增发现。
- SHA 检查与 os.replace 之间的跨进程竞争窗口、有限去重窗口、Windows 后台进程管道残留不是本轮新发现；设计文档已有边界声明。未将已声明边界包装成新增 bug。

## 现场、验证与适用范围

- 项目：`D:/the things of mine/项目库/agent/thinkflow`。HEAD `2a4f4b8c518f3d93ebc4bbaa364a16e8151a9322`，当前分支 `codex/thinkflow-efficient-execution`。
- 接手时已有 8 个 tracked 修改，以及 `src/feedback.py`、两组效率测试、效率 bench 与接力等未跟踪文件。它们属于 10-11 暂停工作；本次没有恢复研发，没有改、暂存、提交正式源码/配置/数据。当前测试结果适用于这份**含未提交修改的工作树**，不等同旧 0.8 Windows 包或 HEAD 的独立测试。
- 已读 README、DESIGN 的职责/持久化/进程边界、v0.8 与生产基准接力、按差异补读 10-11 接力及相关实现。未发现项目/上级独立 AGENTS.md，也未找到 `docs/代码地图.md`；使用 DESIGN 模块表定位。公共规则与 agent 类目测试要求已读。
- 当前全套基线仅运行一轮：`PYTHONDONTWRITEBYTECODE=1 python tests/run_all.py`，exit 0，原 parser/core 测试与 **232 项 unittest** 全过；[python-baseline.txt](evidence/python-baseline.txt)。`npm --prefix desktop test` exit 0，**13/13** 通过；[node-baseline.txt](evidence/node-baseline.txt)。基线包含已有的本地 HTTP/mock 与隔离 profile 测试，没有调用真实模型。不能将基线绿色当成上述缺陷不存在。
- 新探针只使用 `httpx.MockTransport` 模拟真实 SSE 形态，调用生产执行器/解析器；文件和子进程仅在本审查 `temp/` 下。没有真实模型、用户 profile、现有服务、现有认证或秘密访问，也无外发。
- 已逐项看持久接收/ACK、同 ID 回执、队列取消/恢复、意图先落盘、未知结果停机、读版本/外部修改、变更回退的实现和现有测试，并由本轮全套重新执行。**这些路径未新增确认问题，不等于做了崩溃注入穷举或跨进程 exactly-once 证明。** 真实磁盘掉电、多平台、长时负载、最终旧桌面包本轮未验证。
- 三个主问题位于原有 delivery/parser 调度路径；没有将“暂停的优化尚未完整验收”本身当成 bug。只产出修复任务，未经主人恢复指令不落正式补丁。

## 复现命令与交付

使用新的空 fixture 目录执行，避免已有 marker 影响判断：

```powershell
python -B 'D:/the things of mine/项目库/agent/thinkflow/docs/reviews/20261025-pending/probes/reproduce.py' 'D:/the things of mine/项目库/agent/thinkflow' 'D:/the things of mine/项目库/agent/thinkflow/temp/retired-audit-20261025/new-fixtures'
python -B 'D:/the things of mine/项目库/agent/thinkflow/docs/reviews/20261025-pending/probes/delivery_lifecycle.py' 'D:/the things of mine/项目库/agent/thinkflow' 'D:/the things of mine/项目库/agent/thinkflow/temp/retired-audit-20261025/new-delivery-fixtures'
```

修复优先顺序：TF-01 接回已有授权/进程管理 → TF-02 持久化本次验收失败状态 → TF-03 保证解析与执行账本顺序。每项按上述失败场景定向复核，不要求再跑付费基准。探针保留在 `probes/`，输出保留在 `evidence/`；本次三组 fixture 目录已按明确清单送回收站，`temp/` 保持空。
