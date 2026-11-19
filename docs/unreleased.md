# 待发布改动

以下改动位于 `main`，当前发布版为 [0.10.1](release-0.10.1.md)。

## 外部贡献

以下改进由 [@x1051445024](https://github.com/x1051445024) 贡献，维护者在合并时补齐了历史版本续接、空答复和切换模型时的行为。

- 终端可补全 `/models refresh`。会话回放保留中文、换行和字面标记，从历史版本继续时，后续进度和工具输出单独保存。[#5](https://github.com/muxueqingze/xuxiang-thinkflow/pull/5) · [#6](https://github.com/muxueqingze/xuxiang-thinkflow/pull/6)
- 服务端确认流式响应结束后断线，ThinkFlow 仍能交付。Anthropic 起始文本也能完整显示。[#7](https://github.com/muxueqingze/xuxiang-thinkflow/pull/7)
- `/status` 按 token 显示缓存命中率和统计覆盖范围。可为 OpenAI 兼容接口配置路由键，为 Anthropic Messages 显式开启缓存。临时覆盖在切换模型后继续生效。[#8](https://github.com/muxueqingze/xuxiang-thinkflow/pull/8) · [#10](https://github.com/muxueqingze/xuxiang-thinkflow/pull/10)

## 维护者更新

- 长程任务可复用已确认的文件版本和历史输入，逐阶段验收与双方完整用量见[对照实验](research/long-workflows-20261119.md)。
- Windows 桌面构建从冻结后的后端提取 Python 依赖清单，将对应版本的许可文本和终端原生组件声明写入安装目录及便携包的 `THIRD-PARTY-NOTICES.txt`。
- SDK 的固定任务可在收尾时检查本地产物或回执。只有缺项才请求补做，满足条件可同轮交付。[用法](configuration.md#固定任务的收尾验收) · [早停对照](research/early-stop-20261119.md)
