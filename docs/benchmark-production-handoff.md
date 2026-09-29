# 生产配置基准接力 · 2026-09-29

当前状态：实现与离线预检已通过，准备commit冻结并启动36次真实生成。上一轮8K结果完整保留。

## 目标与验收

主人要求取消人为输出/上下文预算，正常完成任务后比较。先改装置和必要生产配置、离线核验实际请求，再冻结并运行原六题×两次×三个harness共36次。任务与判题保持原样，以隔离配置变化；前端与长篇写作不在这六题内，不能用本报告代替旧项目场景结论。

生产模式不发送任何输出 token 上限；自动上下文压缩与清理关闭，不设总运行时间、请求数或自动续写次数预算。Pi/OpenCode能力 metadata 来自官方模型列表：DeepSeek-V4.1-Flash，context_window=1048576、max_output_tokens=393216，这些不会变成请求预算。服务端省略参数的默认仍存在，不能宣称物理无限。普通工具输出分页、权限校验、错误重试、连接失败处理保持原生机制。

## 文件与单元

1. src/provider、agent_loop、cli、model_registry、desktop_service 与桌面设置：支持显式 null/省略；离线请求体与None循环验证；失败回退已有Git，不覆盖个人配置。
2. bench/harness_benchmark_20260929 的 adapter、meter、runner、worker：默认production，旧bounded可显式复现。客户端钩子消除其SDK预算；meter仅核验并记录，不偷偷删除遗留预算。消息hash与逐请求usage保留。独立假SSE验证，失败则在付费生成前停止修装置。
3. 冻结源码/协议钩子/任务/依赖指纹后，顺序运行36次，持续观察；不按成绩重试，不在运行中改核心。输出到新的artifacts实验目录，发布到reports-production，不覆盖旧报告。
4. 离线汇总、逐请求完整计量、隐藏检查、关键失败复验及独立定向质检；通过后推送现有PR，不用Claude Code或Pro。

## 已完成与正在做

- OpenAI缺省输出预算改为None；正整数显式设置仍兼容。Anthropic协议需要正整数，缺省在本地明确报错。
- 核心新13项离线测试通过；原全套parser/core通过，192项unittest唯一旧Anthropic fixture缺显式budget，修复该fixture后5项定向通过。
- Pi/OpenCode真实CLI假SSE各一条请求，实际输出限制字段缺失、退出0，证据 production-adapter-probe.json。
- meter10项回归通过（包括>8MB全文、>24请求/1000秒、拒绝残留输出预算）；Node13项通过。
- 独立预检确认三类真实CLI/worker都未携带输出预算，100条/402,990字符历史不压缩，snapshot不修改消息。未发现阻塞问题，批准冻结。

## 风险与下一步

下一步是离线回归与独立预检、commit冻结，再启动生产36次。没有预设成绩。代理若下游断开会继续收取当前上游usage，必须单独说明这部分观测成本。原desktop二进制尚未重打，源码改动不能冒充旧包已更新。
