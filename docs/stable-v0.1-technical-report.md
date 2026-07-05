# ThinkFlow Stable v0.1 技术报告

日期：2026-07-01  
状态：第一个稳定实验版本候选

## 结论

ThinkFlow 现在已经从概念验证进入可重复实验阶段。核心链路包括：流式解析、命令验证、执行队列、失败中断、全局上下文注入、交付前验证、usage 统计和 TK/CC 同 prompt 成本对比。

本轮同题实验里，TK/ThinkFlow 相比 CC/Claude Code 展示出压倒性价格优势。即使按最保守口径，把缓存命中输入 token 视为完全免费，TK 仍然在 API 调用次数、总 token、completion token 和交付合规性上有优势。

## 本轮 benchmark 数据

| 指标 | TK / ThinkFlow | CC / Claude Code |
|---|---:|---:|
| API calls | 7 | 104 |
| Prompt tokens | 124,515 | 2,246,442 |
| Cached tokens | 92,672 | 2,231,040 |
| Effective input | 31,843 | 15,402 |
| Completion tokens | 33,344 | 61,155 |
| Total tokens | 157,859 | 2,307,597 |
| Cache hit ratio | 74.43% | 99.31% |

输出验证：

- TK：在请求目录 `bench_compare_latest/tk` 完成交付，`npm run build` 通过。
- CC：`npm run build` 通过，但实际输出到 `<external-workspace>/agent-efficiency-lab`，没有遵守请求目录 `bench_compare_latest/cc`。

## 已稳定能力

1. Parser / Executor 分离

解析器只负责从模型输出中识别并验证结构化命令，执行器只接收已验证 `Command`。不完整标签、属性缺失、重复 id 等问题会作为协议错误返回模型，而不是把残缺命令送去执行。

2. FIFO 命令执行队列

模型高速输出时，命令先进入队列。执行器一次只执行一条命令，前一条完成后才执行下一条，避免 `npm install` 与 `npm run build` 这类命令并发抢状态。

3. 失败事务语义

如果第 2 条命令失败，第 3 条及后续已入队命令会标记为 `skipped`，不会产生副作用。返回给模型的失败反馈会说明：成功命令、失败命令、被跳过命令。模型从失败点继续修复。

4. 全局上下文工具账本

工具调用正文和执行结果保留在全局上下文中。这是 ThinkFlow 的核心功能，不以删除 write/edit 正文换成本。刚 edit 过的文件内容必须能被后续需求解析和二次 edit 使用。

5. `tf-read` 信息型工具

`tf-read` 已作为 blocking / information-bearing 工具接入。读取结果会自动进入下一轮上下文，确保模型基于真实文件内容继续工作。

6. 交付前验证

对检测到的前端项目，交付前会自动尝试安装依赖并运行 build。验证失败会把错误反馈给模型，驱动返修；验证通过后再视为可交付。

7. Usage 统计持久化

session snapshot 中保存 usage，包括 API calls、prompt/completion/total、cached tokens、effective input、cache hit ratio 和每轮细节。这样可以把 benchmark 从一次性观察变成可复查数据。

8. 可重复实验资产

新增 `bench/reproducible_agent_efficiency/`，保存固定 prompt、usage 快照、报告快照和复现实验说明。

## 关键设计取舍

ThinkFlow 的优势不是把工具上下文变少，而是把确定性副作用从传统 agent loop 中解耦出来，减少无意义的“调用工具、等待、再请求、再调用”的碎片化回合。

CC 的缓存命中率极高，但需要非常多轮 API 调用。本轮 CC 104 次调用，TK 7 次调用。即便 CC 的 cached input 免费，completion token 与调度轮次仍然带来成本和时间劣势。

TK 当前 cache hit ratio 低于 CC，原因不是保留工具正文本身，而是工具账本注入批次还不够 cache-friendly。后续优化应集中在“更早、更稳定地提交完整账本”，而不是削减账本内容。

## 已知问题

- TK 的工具账本注入仍偏集中，第一次进入大批量账本时会形成 cache miss。
- 交付前验证目前以 npm build 为主，错误结构化程度还不够。
- CC 对比需要更严格的隔离工作目录，否则可能不遵守输出路径，影响交付合规性比较。
- benchmark 自动化还没有完全一键化，CC bridge、端口和日志路径仍需人工确认。

## 下一阶段优化方向

1. Context Checkpoint

当未注入工具账本超过阈值时，主动做 checkpoint continuation，把已提交工具正文和结果提前固定成稳定前缀，使后续调用获得更高缓存命中。

2. Ledger Batch Stabilization

按稳定主题分批提交完整账本，例如配置、类型、组件、样式、测试、构建日志。保留全文，但减少大块新上下文一次性注入。

3. Structured Build Repair

把 build/test 错误解析成文件、行号、错误码、错误消息，引导模型只读/改失败相关文件，减少返修时的上下文扰动。

4. Benchmark Harness 一键化

把 TK 和 CC 的执行、bridge 启动、usage 抓取、构建验证、路径合规检查、压缩归档和报告生成串成可重复命令。

5. Fairness Isolation

CC 测试改为在隔离 cwd 内运行，并要求“在当前目录创建项目”，避免绝对路径不遵守导致结果不公平。

6. Cost Model

新增价格配置文件，支持按不同供应商价格计算：普通输入、cached input、输出 token、reasoning token。报告同时给出 optimistic / realistic / pessimistic 三档成本。

## 稳定版本判断

可以把当前提交视为 ThinkFlow 的第一个稳定实验版本：核心执行链路可运行，安全边界可测试，交付前验证可用，同 prompt benchmark 有数据闭环。

它还不是产品化稳定版。下一步重点是把实验 harness 自动化和 cache-friendly checkpoint 做实。
