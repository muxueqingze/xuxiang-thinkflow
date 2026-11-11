# 配置与嵌入

当前源码提供共享配置解析和运行时工厂。CLI、桌面及嵌入宿主使用相同执行器、工具注册表、会话原文和压缩机制；宿主选择自己的配置来源和持久存储。工程指引可替换，工具协议由核心注入。

## 直接修改配置

以下命令只操作配置，不启动模型或查询模型列表：

```bash
thinkflow config show --sources
thinkflow config get compaction.max_chars --sources
thinkflow config set compaction.max_chars 120000 --project
thinkflow config set interfaces.web.enabled false --project
thinkflow config unset compaction.max_chars --project
thinkflow config path --project
```

`set`、`unset`默认写用户配置，加`--project`写项目覆盖；`--cwd`选择项目目录，`--config`选择指定文件。默认show/get显示合并结果，加`--project`或`--global`则单独预览该文件层。set只保存这项修改，unset移除该项并恢复继承，不把有效配置全文复制进项目。保存供下一次启动使用，已有会话可以带`--resume`重启。会话中的`/model`、`/thinking`、`/sandbox`仍可直接调整当前运行。

值支持JSON布尔、数字、null、对象、数组，普通文字可直接填写。show/get显示核心实际默认值；`--sources`显示文件、环境变量、命令行或默认来源。API key等凭据字段显示为已配置，不输出原值。

CLI默认按以下顺序读取：

1. `~/.thinkflow/config.json`（或`THINKFLOW_HOME/config.json`）。
2. 项目根目录原有的`config.json`，仅当它被识别为ThinkFlow配置。
3. 项目`.thinkflow/config.json`。
4. 本次运行参数。

对象递归合并，数组整体替换，false、0和null按字段契约保留。显式`--config`使用独立文件，不再混入上述文件层；环境变量继续只补齐未配置的连接和模型字段。项目位置按`--cwd`或当前目录确定。配置中的profiles按层展开，项目的模型覆盖可以继承用户层的端点与凭据。

单次尝试不改磁盘：

```bash
thinkflow --set max_run_turns=null --set compaction.max_input_tokens=48000
thinkflow --set providers.local.model=my-model --provider-profile local
thinkflow --system-prompt my-guidance.md
```

明确的模型、端点等CLI选项覆盖选定profile；`--set`中的直接字段最后应用。项目共享设置使用不含凭据的覆盖文件，连接凭据可以继续使用用户配置或现有环境变量入口。

## 桌面设置

常用连接、模型、思考、权限及工程指引继续使用表单。「更多运行配置」编辑一个JSON对象，可配置：

`compaction`、`context`、`interfaces`、`tool_protocol`、`max_read_chars`、`max_retries`、`retry_backoff_seconds`、`max_auto_continues`、`max_consecutive_failures`、`delivery_verify`、`auto_verify_runnable_artifacts`、`max_delivery_fix_attempts`、`native_tools`、`disabled_native_tools`。

例如：

```json
{
  "compaction": {"max_input_tokens": 48000, "keep_recent_tokens": 12000},
  "interfaces": {"web": {"enabled": false}},
  "max_read_chars": 100000
}
```

删除字段恢复核心默认，填`{}`恢复全部额外参数。错误保留输入原文和原设置；空闲时保存应用到当前会话，保留上下文、回执与输出引用。运行中设置入口保持禁用。

运行轮数和运行秒数可留空，保存为 `null` 表示不设该项限制；秒数可用小数，例如 `0.25`。切换 OpenAI/Anthropic 时，已有工具调用与结果在发送时转换为目标协议，保存的历史保持原样。

桌面用户设置保存在应用自己的settings.json，凭据继续使用safeStorage；它不自动读取CLI的全局或项目配置文件。两种入口共用核心参数及装配，但各自选择持久配置来源。

## 上下文预算

| 参数 | 默认 | 作用 |
|---|---:|---|
| `compaction.enabled` | true | 自动压缩开关 |
| `compaction.max_messages` | 80 | 历史消息数触发条件，0关闭此条件 |
| `compaction.max_chars` | 200000 | 历史字符触发条件，0关闭此条件 |
| `compaction.max_input_tokens` | 0 | 按实际待发输入估算的额外触发阈值，0关闭 |
| `compaction.keep_recent_messages` | 30 | 按消息数保留近期历史 |
| `compaction.keep_recent_tokens` | 0 | 大于0时改为按估算token保留连续近期历史 |
| `compaction.max_summary_chars` | 16000 | 摘录摘要的字符预算 |

输入估算在待反馈工具结果加入、请求投影完成之后进行，包括实际messages、system和tools，使用紧凑JSON的UTF-8字节数÷4向上取整。触发后只做一次本地整理并重新构建请求，不增加摘要API。供应商reported usage独立保存，不用旧轮用量冒充当前请求大小。`/status`显示最近待发请求估算；这是近似计数。

阈值用于决定何时整理历史。当前用户完整要求、末条消息及其完整原生工具批次仍保留，因此实际输入可以超过阈值；不会为了凑预算截断这些内容。两个新参数默认0，保持现有策略。模型窗口、摘要预算和任务所需近期内容应一起选择；全任务用量看实际API计量。

## 嵌入核心

安装包中的公开入口为`thinkflow`；仓库内开发也可从`src`导入。配置预览通过`build_agent_config`构造实际dataclass，不建立HTTP客户端，也不加载工作区提示词。`create_agent`接受已解析的配置和显式回调；不导入CLI。

```python
import asyncio
from thinkflow import create_agent, load_configuration, SessionStore, ToolSpec

async def main():
    cwd = "."
    config, sources = load_configuration(cwd).resolve()
    # 此示例使用已配置的明确模型ID；模型发现由宿主按需调用model_registry。
    config.update(enable_native_tools=True,
                  native_tools=["read", "write", "edit", "python", "project_info"])
    store = SessionStore(cwd=cwd)
    agent = create_agent(config, cwd=cwd, session_store=store)

    async def project_info(_arguments):
        return "当前项目使用Python标准库。"

    agent.tool_registry.register(ToolSpec(
        name="project_info", description="获取项目技术信息",
        parameters={"type": "object", "properties": {}}
    ), project_info)
    try:
        if store.exists():
            agent.load_snapshot(store.load())
        await agent.run("查看项目并继续当前任务。")
    finally:
        await agent.close()

asyncio.run(main())
```

只用内存配置时直接传dict给create_agent；宿主需要事件时传`event_sink`，需要授权交互时传`approval_handler`。自定义工具通过现有ToolRegistry注册，声明flow及risk，沿用现有信息合批和回执链。无需新建一套插件执行器。

绑定 `session_store` 后，执行意图、已完成工具回执和运行终态会自动保存，原 `event_sink` 继续接收事件；载入已有快照仍由宿主显式调用。也可用 `store.bind(agent)` 为已有 agent 绑定或切换持久存储。执行日志位于快照旁的 `.execution.jsonl`，输出位于 `.outputs`。

工具 handler 可返回字符串、`ToolResult` 或 `ExecutionResult`。结构化结果中的失败、stdout/stderr、完整输出引用会保留到实际反馈；覆盖已有工具后 direct、tf、native 使用同一 handler，运行中新增工具会进入 `load_tools` 目录。需要实际调用 ID 的 handler 可注册 `pass_call_id=True`，接收 `handler(arguments, call_id=...)`。

## 本轮依据与验证

2026-11-11固定核对Pi提交`682c7b93`：借鉴[设置分层](https://github.com/earendil-works/pi/blob/682c7b93ce3e0a52b579c2b65b4c06bb392e7180/packages/coding-agent/src/core/settings-manager.ts#L65)与[显式SDK装配](https://github.com/earendil-works/pi/blob/682c7b93ce3e0a52b579c2b65b4c06bb392e7180/packages/coding-agent/src/core/sdk.ts#L48)。Pi的资源集合及扩展热重载另有生命周期规则，本轮复用ThinkFlow现有工具入口。

完整本地parser/core及291项unittest通过，桌面15项测试通过；实际Electron验证编辑、错误保留、保存、重启及恢复默认。独立验收复核配置层级、运行中/空闲配置切换、实际请求预算和完整工具批次，并修复预算压缩后的账本起点偏移。[独立报告](reviews/configuration-20261111.md)。另从独立临时wheel实际导入公开SDK、注册并执行自定义工具、运行包内配置CLI，均通过；wheel与构建副本当轮清理，[包验证记录](evidence/configuration-package-20261111.json)。本轮未调用付费模型，不更新历史Pi对照比例；完整长任务策略对照沿[接力](efficiency-handoff.md)继续。

随后按 Pro 报告接通持久化、工具真实分发、跨协议投影和准确请求计量，验证见[本轮记录](reviews/pro-implementation-20261111.md)。
