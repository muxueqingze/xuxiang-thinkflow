# 2026-10-11 效率对照装置

2026-11-10 worker增加每轮response_structure：只记录两个输出通道的字符数、标签名/id及原生函数名，定位响应与执行链是否一致，不保存推理正文。真实Go空正文stop已通过完全相同messages hash的单请求回放确认；回放用量另计，不混入完整任务成绩。

meter记录实际tool_choice。Pi观察器以最后一轮状态判断结束，已恢复的中间API错误仍保留在events，避免粘滞last_error把最终stop误记成失败。修复前原始result不重写，独立审查依据真实message_end/turn_end与交付文本给出派生结论；缺失usage仍为未知。

执行路线诊断可传`--thinkflow-native-tools enabled/disabled`，将现有enable_native_tools配置交给ThinkFlow worker；默认default，沿用被测源码自己的应用默认值。Pi/Claude保持自身工具入口。配置写入manifest与harness，preflight记录有效值。此开关同时影响工具通道和DeepSeek历史reasoning规则，不能把其结果单独归因于一个机制。

2026-11-09末轮恢复修正：当前源码的连续失败预算默认null，显式数字配置仍可停止；worker记录实际effective_consecutive_failure_limit。旧2a4f4b8基线及go-lazy-tools之前源码的默认值是3，三题旧基线均未触发，该区别在正式报告披露。它不是输出、轮数或时长额度，也不能用失败截停后的低token冒充完整交付。

2026-11-09 增加实际本机 Pi 1.0.1 与自定义 OpenAI 兼容供应商。默认三组为 `thinkflow baseline pi`；原有 `claude` 路径保留，可用 `--harnesses` 显式选择。Pi CLI 来自本机 npm shim 所指安装，保留原生系统提示与默认 read/bash/edit/write；只加载本目录 `pi_worker.mjs` 记录工具事件及完整工具结果，不改提示、历史或工具实现。每题生成独立 profile/models/settings，关闭个人资源发现及启动联网，不读取个人配置。Pi 与 ThinkFlow 均关闭自动压缩，不加回合、任务时长或输出 cap。

Go 当前入口与模型 ID 由主会话确认后传入；key 只从指定环境变量在正式运行时读取，prepare 不读取 key：

```powershell
python -B bench/efficiency_20261011/run.py --prepare --name go-pilot --tasks receipt_package pricing_refactor frame_decoder --harnesses thinkflow baseline pi --base-url https://opencode.ai/inference/go/openai/v1 --model deepseek-v4.1-flash --key-env OPENCODE_GO_API_KEY
python -B bench/efficiency_20261011/run.py --run --name go-pilot --tasks receipt_package pricing_refactor frame_decoder --harnesses thinkflow baseline pi --base-url https://opencode.ai/inference/go/openai/v1 --model deepseek-v4.1-flash --key-env OPENCODE_GO_API_KEY
```

未传 `--base-url`、`--key-env` 时继续原官方 DeepSeek/DPAPI 流程；默认 model 仍为 `deepseek-flash`。`--base-url` 是对应协议的基础 URL，自定义 OpenAI Go 地址用于 thinkflow/baseline/pi，原 Claude 使用其 Messages 协议入口。

转发器以自有 `User-Agent: ThinkFlow-efficiency-benchmark/20261109 (<harness>)` 标识实际实验客户端，并按实验目录及 job 生成稳定 `x-opencode-session`；不冒充 OpenCode/Pi 客户端。identity、upstream、实际 model、完整原始 usage、原始与转发 generation 字段保存在 meter.json。Pi SDK 自动给出的 max_tokens 在 meter 处删除，原值保留在 incoming_generation，转发请求交由 provider 默认决定。缓存字段兼容 DeepSeek hit/miss 与 prompt_tokens_details.cached_tokens，保留原始字段；缺少用量明确标为不完整。每10秒输出 job、请求数、已观测 token，meter 每批响应更新，可实时检查。

离线验证：原装置12项测试通过；实际 Pi 1.0.1 经 loopback SSE fixture 两轮完成真实 `python public_tests.py`，工具结果与 OK 输出可见，缓存24/未缓存26、两请求70总 token；转发 model/high、稳定自有身份及 SDK cap 删除均已核对。本次验证只访问 loopback，未读取供应商凭据。

固定复用 `bench/harness_benchmark_20260929/tasks.py` 的六题、公开种子和隐藏判题器。
参赛者：当前工作树 ThinkFlow、`2a4f4b8` 原源码 ThinkFlow、实际安装的 Claude Code 2.1.294。
Baseline 用 `git archive` 解压进本目录 ignored `baseline/`，不切换分支。固定随机顺序，任务串行运行。

```powershell
# 仅准备基线、冻结源码、核对 Claude 版本和 CLI 参数；不会调用模型
python -B bench/efficiency_20261011/run.py --prepare --name formal

# 主会话完成集成之后，明确启动付费实验
python -B bench/efficiency_20261011/run.py --run --name formal --repeats 1

# 缩小固定题清单：首次准备与后续运行必须使用相同参数
python -B bench/efficiency_20261011/run.py --run --name pilot --tasks repair_routes --harnesses thinkflow baseline claude --repeats 1

# 只读已完成任务；不解密密钥、不覆盖、不要求源码仍与当时相同
python -B bench/efficiency_20261011/run.py --name formal --read-job repair_routes-claude-r1

python -B -m unittest discover -s tests -p test_efficiency_benchmark.py
```

没有新增输出 token 上限、请求轮数、任务总时长或工具单次限制。ThinkFlow 的输出上限、运行轮数/时长、自动续写上限显式为 None；自动上下文压缩关闭。Claude 使用自身完整内置工具和系统提示，保留真实 harness 的内部行为，不替换为简化 agent。`--safe-mode`、空 setting-sources、空且 strict 的 MCP 配置用于关闭个人扩展注入；各 job 拥有独立空 CLAUDE_CONFIG_DIR、HOME、APPDATA。不读取、复制或修改本机现有认证。

主进程通过现有 `scripts/live_deepseek.load_key()` 读取 DPAPI。密钥仅保留在 meter 内存；子进程只有随机 loopback token。所有环境密钥先从白名单环境剔除；stdout、stderr、workspace、profile、计量文件均放本目录 ignored `artifacts/`。执行命令和环境变量**名称**可以记录，凭据值不记录。

ThinkFlow 两版走官方 OpenAI chat completions；Claude 走官方 `https://api.deepseek.com/anthropic/v1/messages`。两种协议都使用 Flash、thinking enabled、high、temperature 0。Anthropic meter 将 SDK `max_tokens` 删除，透明记录收到和转发的生成字段；将 thinking.type 设为 enabled、output_config.effort 设为 high。系统、用户、工具消息内容保留，记录前后哈希。若官方 endpoint 拒绝省略 max_tokens，记录失败，不自行增加或缩小输出上限；需要依据真实 pilot 再处理。不同协议、内置系统提示、工具模型和 Claude 自身缓存/压缩等行为是比较限制。

每个请求捕获原始 provider usage（仅计数字段）、HTTP 状态、finish reason、断连、耗时和生成参数。Anthropic input_tokens 按标准契约不含 cache-read/cache-create；总输入将三者相加，cache-read 计为缓存，input+cache-create 计为未缓存，并另列 cache-create。缺失缓存字段时明确标记计量不完整，不能把未知用量当成零。推理 token 只计 provider 明确报告的数字，不推断其正文长度。

`result.json` 保留隐藏评分、进程返回、usage完整性、工具失败、真实观测回合、主动测试回执和流内写入次数。主动测试成功要求实际 tool_result/退出状态或明确测试通过输出；终态 exit 0 本身不代表测试通过。Claude 以独立 assistant message ID 计观测回合，并保留 result.num_turns；provider请求数同时给出，重试可使两者不同。原始 stdout 可能包含 Claude 提供的思考块，只保存在 ignored 本地产物；汇报指标不包含思考正文。

失败任务照常判题并保留。`accepted` 表示隐藏评分通过、进程正常、无基础设施/运行错误；完整工程交付还要核对实际公开检查、最终报告与协议状态。正文DSML残片导致提前结束的样本即使产物得分通过也不计作有效省量。token费用比较还必须核对 usage_complete/cache_usage_complete。已完成 job 自动复用；不完整目录拒绝覆盖，先保留调查并另建实验名。每次任务前后核对冻结源码，防止混跑。隐藏 grader 的检查超时仅用于判题，不限制参赛模型工作时间。

离线验证包含实际 Claude Code 2.1.294 → loopback meter → 本地 SSE fixture 的完整两轮：默认 Bash 执行公开测试，回传真实 tool_result，再正常完成；SDK 输出 cap 被去除且内置 system 保留。`verify` 的主动测试成功要求实际 success 回执、exit_code 0，并保留 stdout/stderr 哈希与 verified_revisions 供核验。该检查没有访问真实模型或外部 provider。

这是本地固定题工程对照，不是行业基准；提示约束工作目录和禁联网不等同于操作系统沙箱。主会话已经单独验证官方 Messages 省略 max_tokens 可以返回 200；完整 Claude ↔ 官方 provider 的实题 pilot 仍需主会话完成。
