# ZCode 定向架构参考：输入受理、投影与文件保护

日期：2026-09-29。本文是少数架构接缝的源码调查，不是 ZCode 全面审计，也不替代续想 0.7 既有交互验收。

基线：续想 `0fb505c2a53e6c61b30b7b304a3b16985142b63a`；本次另定向复核主会话在工作区修复的启动保存失败问题。参考项目为官方 [zai-org/ZCode](https://github.com/zai-org/ZCode)，固定提交 `29628c9acdb81b703bbd4080c207a0e7ce5e276e`，Apache-2.0。所有下列链接固定到该提交。

调查方法：先读续想 runtime、desktop service、renderer 与 executor，再按问题读取 ZCode 的有限源码。参考仓库未安装依赖、未启动程序，未执行其 AGENTS、skills 或 bootstrap。续想复现仅使用隔离临时目录与 FakeAgent，没有请求真实模型端点。本次仅新增本文；实现修复由主会话完成。

## 续想的三个具体限制

### 1. 输入受理缺少服务端命令身份；启动失败曾留下幽灵 running

**确定 bug，P1，已修复并独立定向复核。** 基线 `src/desktop_service.py` 的 `dispatch("run")` 在 `_save()` 前更改 transcript/title/status；保存异常发生时尚未创建 task。原隔离复现中，状态为 `running`、task 为 `None`，调用 cancel 仍然 running。后端直接重试仍能启动，因为 `_require_idle` 检查 task；因此这不是整个后端死锁。正常 UI 却会按 running 禁用切换，并把新输入当作排队，形成幽灵运行状态。

主会话补丁位于 [desktop_service.py:543](../../src/desktop_service.py#L543)：暂存旧状态，使用新 transcript 列表，保存失败恢复旧 tuple，成功后才创建 task。独立复核对 `_save` 注入 `OSError`，检查 transcript/title/status/last_error/reply_index 全部等于原值、task=None，cancel 返回 idle；随后明确 retry 只出现一条用户输入，FakeAgent 只收到一次。结果：通过。独立结果归档为 `../evidence/v0.7-review/ghost-run-fixed.json`；回归测试由主会话加入 `tests/test_desktop_service.py`，本文没有重跑整套服务测试。主会话另做的实际交付包故障注入不计为本文独立实测。

**尚存的扩展限制，非已复现重复执行 bug。** [renderer/app.js:68](../../desktop/renderer/app.js#L68) 在 renderer 维护候选 ID/outbox，但第 85 行发给服务的是 `{prompt}`，ID 没有进入 run RPC。当前丢回执后暂停、禁止自动重发是保守且正确的；代价是只能人工核对记录，无法按候选 ID 查询是否受理。队列消费也在 renderer，关闭窗口后没有独立的服务端队列事实。优先借 ZCode 的命令键与可查询回执，其次再迁移队列所有权，不应因此改写模型工具执行循环。

### 2. UI 混用完整快照和增量，但缺少共同水位

**未来扩展/维护限制，P2；本次未证实当前本地单通道的乱序 bug。** [desktop_service.py:213](../../src/desktop_service.py#L213) 返回会话状态与最近 120 条 transcript，但没有与增量关联的 epoch/seq 水位。runtime 的 `events.py` 虽然自增 seq，service 自产事件及 state 没有组成同一个可校验的投影协议。[renderer/app.js:154](../../desktop/renderer/app.js#L154) 接收快照替换状态，第 453 行直接追加 text_delta，没有投影序号拒重/断档检查。现有本地进程顺序、切换保护降低了风险，不能据此宣称已有远程断线数据错误。

当加入多客户端、后端重连或更长会话时，UI 无法证明某个 delta 已包含在某次 snapshot 内；120 条之外的历史也没有分页入口。最小补足是 service 所有权下的 session epoch + event seq + snapshot 水位；同 epoch 拒重、遇断档重取快照、跨 epoch 清理旧投影。rowId 与按游标取历史可以随后增加，无需复制完整的订阅协议和庞大状态仓库。

### 3. 写文件没有“模型读到的版本仍有效”前置条件

**协同编辑安全能力缺口；有实复现，但不把约定中的覆盖写语义算作解析 bug。** [executor.py:304](../../src/executor.py#L304) 使用实例内路径锁串行写，底层第 345 行以 `os.replace` 原子替换；read 没有记录供后续 write 验证的文件版本。隔离复现：模型 read 原内容 → 外部修改文件 → 模型 write 基于原内容的新全文，返回 success，最终文件为 `model from old baseline`，外部变更被覆盖。原子替换避免半文件，不等于防止覆盖别人已完成的编辑。

可引入规范路径对应的已知全文 hash/不存在状态，在执行覆盖前验证；冲突返回失败，让现有失败反馈机制接管。成功写后更新已知版本，连续预测写仍在原输出流执行。部分读取、未知已有文件、显式覆盖与新建文件应各有明确策略，而非悄悄改变所有历史写语义。实例锁只协调本实例，哈希检查与替换之间仍可能被不合作的外部编辑器抢写；不能宣称无竞态 CAS。

## 建议主会话亲读的五个入口

调研期间的本地只读镜像根目录：`temp/zcode-scout/source/`。下列“本地相对路径”均相对此根目录，保留原仓库目录结构与源码行号。临时镜像已在收尾时可恢复回收；固定提交链接是长期引用。

### 1. CommandInbox：把输入重试变成可查询的受理结果

- 源码：[command-inbox.ts:117–207](https://github.com/zai-org/ZCode/blob/29628c9acdb81b703bbd4080c207a0e7ce5e276e/apps/zcode-cli/packages/bootstrap/src/zcode-protocol-v4/command-inbox.ts#L117-L207)，[lookupExact:284–306](https://github.com/zai-org/ZCode/blob/29628c9acdb81b703bbd4080c207a0e7ce5e276e/apps/zcode-cli/packages/bootstrap/src/zcode-protocol-v4/command-inbox.ts#L284-L306)。本地相对路径：`apps/zcode-cli/packages/bootstrap/src/zcode-protocol-v4/command-inbox.ts`。
- **已读事实：** session+commandId 定位命令；先锁命令键，再锁 session，拿到 session 锁后再次查重。执行中的 final Promise 和仍活跃的输入单独保留，不被普通 settled LRU 淘汰；settle 再释放 session gate。查找顺序含 in-flight、live、settled 和四种持久事实回查。`decide` 第 308 行起校验 revision/logEpoch/目标，`retryAck` 第 446 行保留失败语义。
- **准确边界：** 这是内存 map/gate 加持久事实回查接口，不能称其自身为 durable inbox，更不能称跨重启 exactly-once。命令键本身也不是请求内容指纹验证，续想若借用仍应拒绝“同 ID、不同内容”。
- **真实接线：** [v4-bridge.ts:577–595](https://github.com/zai-org/ZCode/blob/29628c9acdb81b703bbd4080c207a0e7ce5e276e/apps/zcode-cli/packages/bootstrap/src/zcode-protocol/v4-bridge.ts#L577-L595) 创建 `PersistentCommandIndex`，调用 `loadPersistentCommandFacts(store, sessionId, {discardAdmittedOnLoad: !live})`；[1618–1624](https://github.com/zai-org/ZCode/blob/29628c9acdb81b703bbd4080c207a0e7ce5e276e/apps/zcode-cli/packages/bootstrap/src/zcode-protocol/v4-bridge.ts#L1618-L1624) 把四路 lookup 接入 gateway；[948–967](https://github.com/zai-org/ZCode/blob/29628c9acdb81b703bbd4080c207a0e7ce5e276e/apps/zcode-cli/packages/bootstrap/src/zcode-protocol/v4-bridge.ts#L948-L967) 先 await 保存持久 fact，再更新索引。gateway 的 `v4-gateway.ts:648–673` 把这些 host hook 交给 Inbox。
- **持久事实是什么：** [persistent-command-facts.ts:35–132](https://github.com/zai-org/ZCode/blob/29628c9acdb81b703bbd4080c207a0e7ce5e276e/apps/zcode-cli/packages/bootstrap/src/zcode-protocol-v4/persistent-command-facts.ts#L35-L132) 从 transcript 锚点、timeline/child session entries、discarded/cancelled 输入记录重建回执；离线会话的 admitted 输入被标记丢弃并返回明确失败，不自动重放。索引文件 `persistent-command-index.ts:44–99` 按 session 懒加载并校验 workspace 身份。
- **保护窗口与证据缺口：** 同一活进程中的并发重试由 pin/gate 保护；已有持久事实可以在内存淘汰或重建后被查回。尚未形成持久事实的崩溃窗口、底层 store 事务/fsync 以及事实提交和实际副作用的原子关系，本次没有逐路径证明，不能由这几段接线推出绝对保证。
- **最小迁移：** run 接收客户端稳定 command_id+内容 hash，服务端持久记录 accepted/terminal，提供 query；成功回执丢失时查同 ID，不盲重发。没有证据的状态保留 unknown，沿用当前人工核对保护。它位于用户输入边界，不改变延迟工具 FIFO 成功不断流。

### 2. admitPrompt：让 busy 判断、预留与入队属于同一处

- 源码：[prompt-admission.ts:20–125](https://github.com/zai-org/ZCode/blob/29628c9acdb81b703bbd4080c207a0e7ce5e276e/apps/zcode-cli/packages/core/src/runtime/methods/prompt-admission.ts#L20-L125)。本地相对路径：`apps/zcode-cli/packages/core/src/runtime/methods/prompt-admission.ts`。
- **已读事实：** busy 分支明确 rejected/steered/queued；第 99 行先 `reserveTurnStart`，再第 102 行提交可取消 runtime command，取消回调释放预留。执行 completion 和“已受理”返回分离，返回值携带 turnId。避免调用者各自执行“先看空闲、稍后再开跑”。
- **对应续想：** renderer 拥有队列、service 拥有单次运行，输入身份和生命周期跨两处。最小迁移是 service/runtime 暴露一个 admission 方法，集中验证、持久受理、启动预留和队列取消。先只支持下一轮 FIFO；不要顺便引进 ZCode 的 guide/steer/foreground promotion 体系。
- **流式核心影响：** 用户输入队列与模型生成中的工具队列必须分开。不得在 delayed 工具每次成功后调度新模型回合，也不应把排队用户文本无条件插进尚未完成的输出流。
- **未验证：** 本次读了 admission 本函数，没有证明其所有下游队列存储与执行路径的崩溃一致性。

### 3. ConversationProjectionStore：用水位消除“快照加增量”的猜测

- 源码：[conversationProjectionStore.ts:699–724](https://github.com/zai-org/ZCode/blob/29628c9acdb81b703bbd4080c207a0e7ce5e276e/packages/ui/src/v4/conversationProjectionStore.ts#L699-L724)，[restart/loadOlder:960–1030](https://github.com/zai-org/ZCode/blob/29628c9acdb81b703bbd4080c207a0e7ce5e276e/packages/ui/src/v4/conversationProjectionStore.ts#L960-L1030)，[ACK 投影核对:1247–1275](https://github.com/zai-org/ZCode/blob/29628c9acdb81b703bbd4080c207a0e7ce5e276e/packages/ui/src/v4/conversationProjectionStore.ts#L1247-L1275)。本地相对路径：`packages/ui/src/v4/conversationProjectionStore.ts`。
- **已读事实：** `toSeq <= current.seq` 丢弃迟到/重复帧；`fromSeq !== current.seq` 不硬拼，触发恢复。connect 用 generation 拒绝旧异步结果，runtime 重启使 generation 前进。历史分页按 logEpoch/游标核对后合并。已获 accepted/duplicate 回执却没有相应权威输入投影时，超时请求恢复，而不是伪造服务端已存在的消息。
- **最小迁移：** 先把 service snapshot 与 delta 放入同一 epoch/seq 命名空间，renderer 只做校验后投影。再用稳定 rowId 表达用户/助手记录和分页，最后才考虑订阅恢复帧。保留本地简单 IPC，不引入完整 V4 网关。
- **流式核心影响：** 序号是观察层协议，不应变成模型输出的同步等待点。工具 intent 的必须落盘约束继续在 service 执行前履行，普通文本投影可合并发送。
- **未验证：** 未运行其前后端恢复联调；客户端处理代码不能独立证明 publisher 的每条日志都满足同样水位契约。

### 4. WindowHostController：读所有权边界，暂不迁移远程聚合

- 源码：[windowHostControllerService.ts:39–44](https://github.com/zai-org/ZCode/blob/29628c9acdb81b703bbd4080c207a0e7ce5e276e/packages/desktop/src/host/windowHostControllerService.ts#L39-L44)，[服务状态容器:136–180](https://github.com/zai-org/ZCode/blob/29628c9acdb81b703bbd4080c207a0e7ce5e276e/packages/desktop/src/host/windowHostControllerService.ts#L136-L180)。本地相对路径：`packages/desktop/src/host/windowHostControllerService.ts`。
- **已读事实：** sourceKey 区分本地/远程、remoteSessionId 与 workspaceIdentity；taskKey 含 workspace 身份。Host 维护来源、在线状态、订阅和刷新 flight/generation，向下取各 task service 的事实，在 Host 形成聚合投影。不是把相同路径字符串直接当成全局唯一工作区。
- **对应续想：** 现在 Electron main/preload 负责安全 IPC 与进程桥接，Python service 负责会话/运行，renderer 负责显示与草稿，这个规模不缺第四个总控层。只应明确 runtime 是运行和受理事实来源，UI 不能成为唯一队列所有者；以后真有远程工作区再考虑 source/task 身份组合。
- **最小迁移及风险：** 本轮不迁移。可在接口文档明确每类状态唯一所有者，避免 UI/main/service 各存一个“真 running”。直接搬远程聚合会增加状态面，且对流式 FIFO 没有直接收益。
- **未验证：** 本次未启动远程 Host，不保证其离线/多窗口所有路径无竞态。

### 5. Write handler + FS adapter：借新鲜度前置条件，别误称哈希 CAS

- 主入口：[write.ts:97–146](https://github.com/zai-org/ZCode/blob/29628c9acdb81b703bbd4080c207a0e7ce5e276e/apps/zcode-cli/packages/core/src/tool/handlers/write.ts#L97-L146)，[新鲜度检查:278–336](https://github.com/zai-org/ZCode/blob/29628c9acdb81b703bbd4080c207a0e7ce5e276e/apps/zcode-cli/packages/core/src/tool/handlers/write.ts#L278-L336)。本地相对路径：`apps/zcode-cli/packages/core/src/tool/handlers/write.ts`。
- 必须一起读的底层：[fs/index.ts:291–323](https://github.com/zai-org/ZCode/blob/29628c9acdb81b703bbd4080c207a0e7ce5e276e/apps/zcode-cli/packages/adapters/src/fs/index.ts#L291-L323)，[473–483](https://github.com/zai-org/ZCode/blob/29628c9acdb81b703bbd4080c207a0e7ce5e276e/apps/zcode-cli/packages/adapters/src/fs/index.ts#L473-L483)，[775–776](https://github.com/zai-org/ZCode/blob/29628c9acdb81b703bbd4080c207a0e7ce5e276e/apps/zcode-cli/packages/adapters/src/fs/index.ts#L775-L776)。本地相对路径：`apps/zcode-cli/packages/adapters/src/fs/index.ts`。
- **已读事实：** 覆盖已有文件前要求完整的已知读取状态，拒绝未读/部分读和检测到的陈旧版本；写入传 `expectedRevision`，成功后更新 readFileState，使模型后续基于自身写入继续工作。不是只有 snapshot/journal 的事后留痕。
- **局限是明确源码事实：** adapter 的 expectedRevision 校验只比较 `mtime:${Math.trunc(mtimeMs)}:size:${sizeBytes}`，虽然返回值有 hash，却没有在此比对 hash。校验与 atomicWrite 之间还有独立 await。handler 在具备时间/大小信息时也会提前返回比较结果。因此同毫秒同大小变化、检查后的外部写入不能据此得到充分保护；本次未运行 ZCode 来复现这些窗口。
- **最小迁移：** 借“read 建立已知版本 → 覆盖前验证 → 冲突反馈 → 自己写成功后更新版本”，结合续想已有路径锁，优先使用内容 hash 而非只靠 mtime/size。对于现有文件的首次盲写另行定义策略。不要照搬 handler 的普遍 approval 元数据，使每个可预测成功写都暂停；那会损害续想核心。

## 取舍与验证边界

建议顺序：命令 ID/内容指纹与可查询回执 → service/runtime 队列归属 → epoch/seq/rowId 投影 → 文件新鲜度策略。Host 远程聚合暂缓。前三者主要补用户输入和观察层，不需要把 parser、延迟 FIFO 或工具失败反馈重写。

源码检索了该固定提交中 `*.test.*`/`*.spec.*` 的 JS/TS 文件名，仅找到四个现有测试文件：services 的 importedClaudeRecovery、nonCliAcpRetirement、providerConfigMigration，以及 ui 的 nonCliAcpRetirement。没有找到这五个入口的对应命名测试；这不等于仓库不存在其他形式的检查。本次没有安装运行 ZCode，也不声称这些机制已由实测证明。若真的迁移，必要的本地针对验证应覆盖：同 ID 并发与不同内容、接受后丢回执、受理落盘前后崩溃边界、队列取消/重启不盲重放、重复 delta/断档/旧 epoch、已读文件被外部更改。无需为调研先搭建整套 ZCode。

本报告中的“事实”来自上述代码或明确隔离复现；“最小迁移”是设计建议；未证明的崩溃原子性、底层持久化强度与外部文件 CAS 保证均已标为限制。当前确定的 ghost running bug 已经收口，没有把研究扩成续想新版本重构。

## 主会话亲读后的决定

主会话已亲读五个入口的关键实现，并补看CommandInbox.lookupExact/decide、v4-bridge持久回查接线和FS adapter的真实revision检查；没有仅依据子代理概述作结论。认可上述优先级。

- **先做输入契约**：稳定command ID/内容指纹、持久接收事实与查询接口；现有unknown仍保留人工核对，不以盲重试冒充可靠。然后把已受理FIFO移到service/runtime，UI只保留未提交草稿与暂态展示。
- **再做可核对的界面状态**：快照/增量共享session+epoch+seq，重复丢弃、缺口补快照；随后才加稳定消息ID和历史分页。保持现有私有stdio桥接。
- **再做写前版本检查**：沿已读版本追踪文件，冲突进入现有失败反馈；成功的可预测工具仍不断流。不能把mtime/size或者atomic rename当作完整的内容比较交换。
- **暂缓大规模移植**：远程Host聚合、多端订阅框架、guide/steer以及整套monorepo需要真实需求再引入；当前Python内核无需更换。

这次已落地的代码修复只有启动保存失败回滚；服务18项通过，最新Windows独立包通过真实文件系统故障/恢复重试验证，详见`../evidence/v0.7-desktop/start-failure.json`。其余均为有据的后续设计建议，未冒称实现。
