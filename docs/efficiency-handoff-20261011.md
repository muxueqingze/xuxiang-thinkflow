# 工程能力与执行效率优化 · 2026-10-11

> 此页保留暂停时的原始工作记录；2026-11-09 已恢复，当前状态统一见 [efficiency-handoff.md](efficiency-handoff.md)。下文 verify 原型已在恢复工作中撤出。

## 目标与验收

按主人要求改进续想的实际工作机制：保持信息反馈、失败恢复与工程验证，在同模型、同任务条件下减少总 token 和不必要请求。Claude Code 是实测对照对象，不能把传统逐条调用的模拟器当成 Claude Code，也不能用降低输出预算制造优势。

## 起点

- 源码起点 `2a4f4b8`，工作树干净，原分支 `codex/thinkflow-desktop-v06` 比远端多 1 个本地提交。
- 已读 README、架构、生产实测接力及成本案例；Python 3.12.10、Node 24.15.0 已存在。
- 旧生产配置 ThinkFlow 12/12 交付，但 384.56 万 token，高于 Pi/OpenCode；未测当前 Claude Code，不能沿用旧比较宣称领先。
- 正在跑现有全部离线基线。DeepSeek 复用项目已有私有 DPAPI 读取入口，密钥不进入源码、命令参数或报告。

## 执行单元

1. 核对协议、历史组装、工具结果和验收状态的真实瓶颈；独立只读分析覆盖证据缺口。
2. 在原运行链内改进可预测工具的反馈与上下文，不丢失失败结果、用户契约或执行证据；补必要回归。
3. 用固定工程题比较当前实现/旧基线/实际 Claude Code，保持模型与输出参数一致，保存用量、退出状态、产物评分、主动验证和流中执行证据；失败样本全部保留。
4. 独立挑刺验收，定向修复，再整理源码、证据、使用说明与接力。未经同条件证据不宣称全面超过 Claude Code。

## 风险与回退

- 推理字段属于供应商协议，不能任意删除当前工具轮的 reasoning_content。
- 不能把写入成功或 shell exit 0 自动等同需求完成。
- 现有桌面包不含新源码；是否重打以本轮实现范围和验证结果确定。
- 回退以起点提交及新增提交为界；不改 Codex 登录或全局认证配置，不改写历史报告。

## 进行中 / 下一步

**已按主人要求暂停（2026-10-11），未经主人恢复指令不继续。** 后台全部离线测试命令已经结束（exit 0）；独立审查子代理已中断，其他两个子任务已完成，无正式付费实验运行。

### 已保存的工作树

- 当前分支 `codex/thinkflow-efficient-execution`，基于 `2a4f4b8`；本轮修改尚未提交、推送、发布或重打桌面包。
- 内核新增 `verify(cmd, paths)`，通过原 bash 权限/执行链，将成功检查绑定到声明文件的实际版本；计划引用过期验证会拒绝，计划证据受账本保留保护。
- runtime 和延迟回执并入末尾原生工具 observation；完整 reasoning 保持。仅已原样存在于 reasoning 的成功写入正文去重，普通 blocking 成功反馈去除重复头尾。
- 移除强制启动 `read_skill` 往返；现有同轮 tf 写入后 native 验证路径增加真实文件/子进程回归。
- 新文件 `src/feedback.py`、`tests/test_efficiency_runtime.py`；原位修改 agent_loop/context/executor/task_plan/cli 和内置 skill。
- 新对照装置 `bench/efficiency_20261011/` 和 `tests/test_efficiency_benchmark.py`，支持旧版/新版/实际 Claude Code，输出/调用/总时长预算均未另设上限。装置12项离线验证通过，包括实际 Claude 2.1.294 接 mock endpoint 执行 Bash。
- 新内核12项定向测试通过；全套 `python -B tests/run_all.py` 命令已 exit 0，日志 `docs/evidence/efficiency-20261011-python.txt`。尚未完成独立验收。

### 实际模型请求

- 官方 DeepSeek 模型列表 GET 200，Flash 可见。
- 仅一次 Anthropic 兼容端点连通请求，未设置 max_tokens，enabled/high，200，输入35/output27 token，end_turn。没有工程题付费对照，不能据此宣称节省比例或超过 Claude Code。
- 凭据复用现有 DPAPI，只在进程内使用，没有写入代码或报告。

### 恢复时先处理的已确认问题

独立审查已复现、尚未修复：

1. 长 write 正文在 record 阶段先裁至8K，再与完整 reasoning 比较，插入的CLIPPED标记导致去重不生效；10,010字符用例仍回灌约4,253字符。应保留未反馈的完整载荷或建立确切对应，不能用模糊头尾匹配冒称无损去重。
2. verify 已执行但 native tool_result 尚未写入时的快照恢复，会经 build_injection 默认4K路径截断长输出，尽管记录仍有11,043字符原结果；中部诊断丢失。应让未反馈的信息型回执完整反馈一次。
3. 原有 failed tf-bash 失败反馈也有4K截断/重复stderr；与“完整blocking反馈”目标有关，恢复后统一处理并补定向回归。

准备中的修复尚未落盘，不能当成已实现。处理上述项后请独立审查者复核，再冻结代码启动 Claude pilot 和同题正式对照。`bench/efficiency_20261011/artifacts/offline-validation` 的开发期清单已过期，保留且不得续入正式数据；旧实验和基线原件保持。
