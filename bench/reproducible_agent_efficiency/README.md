# 可重复实验：Agent Efficiency Same Prompt

本目录保存续想 ThinkFlow/TK 与 Claude Code/CC 在同一题目 prompt 下的可重复对比实验资产。

## 固定输入

- `prompt.txt`：本轮使用的题目 prompt，已在本目录固定保存。
- `usage_compare_report.latest.json`：本轮原始 usage 对比数据快照。
- `usage_compare_report.latest.md`：本轮人工可读报告快照。

## 本轮输出位置

- TK 输出目录：`bench_compare_latest/tk`
- CC 请求输出目录：`bench_compare_latest/cc`
- CC 实际输出目录：`<external-workspace>/agent-efficiency-lab`

CC 本轮没有遵守请求输出目录，实际把项目创建到了 `agent-efficiency-lab`。因此本轮 usage 可作为成本样本；交付合规性上，TK 与 CC 不完全等价。

## 复现实验流程

1. 清理或新建隔离输出目录。
2. 使用同一个 `prompt.txt` 作为任务说明。
3. 先运行 TK/ThinkFlow，记录 session snapshot 与 build log。
4. 再运行 CC/Claude Code，确保 Anthropic bridge 在 `8765` 端口记录 usage JSONL。
5. 分别运行前端构建命令验证交付：
   - TK：在 `bench_compare_latest/tk` 下运行 `npm run build`
   - CC：在其实际输出目录下运行 `npm run build`
6. 生成对比报告，至少记录：API calls、prompt tokens、cached tokens、effective input、completion tokens、total tokens、cache hit ratio、构建是否通过、输出路径是否符合要求。

## 关键判据

- `effective_input = prompt_tokens - cached_tokens`
- 缓存命中完全免费时，输入成本按 `effective_input` 估算。
- 真实成本还需要叠加 completion tokens。ThinkFlow 的核心优势来自更少的 agentic turn fragmentation 与更少的返修轮次，而不是删除工具上下文。

## 本轮结论

TK 用 7 次 API 调用完成并通过构建；CC 用 104 次 API 调用完成并通过构建，但输出目录不符合要求。按本轮数据，即使把缓存命中输入 token 视为完全免费，TK 仍在总 token 与 completion token 上保持明显优势。
