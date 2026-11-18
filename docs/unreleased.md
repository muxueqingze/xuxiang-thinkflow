# 待发布改动

以下改动位于 `main`，当前发布版为 [0.10.1](release-0.10.1.md)。

## 外部贡献

- CLI 补全加入 `/models refresh`。[@x1051445024](https://github.com/x1051445024) · [#5](https://github.com/muxueqingze/xuxiang-thinkflow/pull/5)
- 会话回放保留中文、换行和字面标记。合并时保留了同一会话的不同历史版本，从历史续接会单独保存进度和工具输出。[@x1051445024](https://github.com/x1051445024) · [#6](https://github.com/muxueqingze/xuxiang-thinkflow/pull/6)
- 流式响应在服务端确认结束后断线仍能交付，Anthropic 起始文本也能完整显示。合并时补齐了 CLI 和 SDK 的空答复行为。[@x1051445024](https://github.com/x1051445024) · [#7](https://github.com/muxueqingze/xuxiang-thinkflow/pull/7)
- `/status` 显示缓存命中率和统计覆盖范围。计算按 token 加权，只纳入命中量和完整输入量都可确定的响应。[@x1051445024](https://github.com/x1051445024) · [#8](https://github.com/muxueqingze/xuxiang-thinkflow/pull/8)
- 可为 OpenAI 兼容接口配置缓存路由键，为 Anthropic Messages 显式开启提示缓存。合并时修正了临时覆盖在切换模型后失效的问题。[@x1051445024](https://github.com/x1051445024) · [#10](https://github.com/muxueqingze/xuxiang-thinkflow/pull/10)

## 维护者更新

- 长程任务可复用已确认的文件版本和历史输入，逐阶段验收与双方完整用量见[对照实验](research/long-workflows-20261119.md)。
- Windows 桌面构建从冻结后的后端提取 Python 依赖清单，将对应版本的许可文本和终端原生组件声明写入安装目录及便携包的 `THIRD-PARTY-NOTICES.txt`。
