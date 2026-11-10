# 提示词分层定向验收 · 2026-11-10

## 问题

### P2（已修复并独立复核）：显式工具名单可使底层协议与实际 schema 不一致

位置：`src/agent_loop.py:643`及`_enabled_native_tool_names()`的显式allow分支（746–747）。新合成器直接使用该函数的结果；显式allow仅减去deny，未过滤未注册名称，而实际OpenAI/Anthropic schema会过滤registry。

独立内存探针：启用native，设置`native_tools=['unregistered_tool']`，得到enabled集合`{'unregistered_tool'}`、`_openai_provider_tools()==[]`，但`build_system_prompt()`仍含“当前还开放原生工具”。配置本身被现有入口接受，提示词与本次实际接口不一致。建议合成协议时取实际注册且开放的工具名，或让显式allow分支与registry取交集；不增加配置门禁。

## 已核验路径

- 亲读当前prompts/AgentLoop/CLI/desktop_service及桌面IPC、设置持久化、renderer变更；阅读新增4项prompt层测试的真实MockTransport执行内容，没有重跑整套源测试或61%基准。
- 核心协议包含语法、FIFO、信息批次、即时反馈、版本基线、Python进程与shell语义；工程选型、检查安排、是否保留测试文件和交付风格均在可替换BUILTIN指引中。关闭/自定义通过现有每轮build_system_prompt仍合成核心协议。
- 独立内存运行：加载read_skill后导出snapshot，在工程指引关闭的新Agent恢复，核心协议只出现一次；read_skill/load_tools说明随实际开放项出现，旧CUSTOM不从snapshot恢复。OpenAI/Anthropic两种request结构均核对。
- 实际apply_provider_profile关闭native后，原生补充说明消失，核心流式协议保留。没有对用户自定义文字做前缀改写。
- 独立调用DesktopService._replace_agent，依次custom/off/recommended并传入上次snapshot，均保留一次核心协议，且工程指引按当前设置切换。桌面normalize、IPC允许字段及save/load路径均保留use_builtin_system_prompt/system_prompt；默认值覆盖旧设置缺字段的情况。
- CLI工程提示关闭与workspace context分开；context.enabled决定context注入。--print-system-prompt调用AgentLoop同一合成入口并关闭临时client。

修复后共用registered集合，显式allow结果与registry取交集。独立复核OpenAI/Anthropic两种真实request body构造：仅未注册名称时schema为空且不再宣称开放native；未注册名称混合read时仅保留read；read/load_tools配deny后只保留load_tools且相应说明存在；allow全部被deny时schema和native说明同时为空。各场景核心协议和CUSTOM均恰好保留一次；默认core工具仍开放read/load_tools。原失败项及直接影响全部通过，没有增加配置门禁。

本次只执行内存构造和请求体检查，没有发送外部API、创建临时文件或常驻子进程。只新增并更新本报告。P2已闭环，本提示词分层交付节点定向验收通过。
