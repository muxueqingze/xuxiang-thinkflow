# ThinkFlow — 命令格式协议

> 版本：1.6
> 日期：2026-11-10

---

## 一、设计原则

1. **主路径扫描 thinking 流，正文 parser 只做兼容兜底。** 职责上仍鼓励模型只在 thinking 输出命令；DeepSeek 等模型偶尔漏到正文时，框架负责执行并从显示/消息历史剥离命令块。
2. **默认只执行 canonical `tf-` 标签。** `<tf-write>` 才是执行协议；普通 `<write>` 更像文档/XML 示例，默认不会被 agent 执行。
3. **工具语义决定反馈要求，信息依赖决定反馈时机。** 独立read/bash/python在同次生成中集中声明，批次结束后一起反馈；后续依赖结果的操作等下一轮。显式`need_result`或`confirm`立即反馈，真实失败立即打断。
4. **流式扫描 + 结构化解析。** 开标签用逐字符 scanner 找不在引号里的 `>`，属性用 quoted-attribute parser，避免 `>`、引号转义和跨 chunk 截断导致误判；不用 LLM 做格式转换，降低延迟和成本。

属性支持双/单引号、匹配属性定界符的反斜线转义，以及XML五个命名实体 `&quot;`、`&apos;`、`&lt;`、`&gt;`、`&amp;`。只解码一次；未知实体和普通Windows路径反斜线原样保留。正文不做属性实体解码。CLI和桌面默认使用文本协议；`--native-tools`或配置`enable_native_tools=true`启用原生兼容。shell属性按上述规则转义，多行Python直接放入tf-python正文。

---

## 二、命令块格式

### 2.1 有正文内容的命令（开闭标签）

```xml
<tf-write id="001" path="D:/novel/ch01.md">
天空灰蒙蒙的，主角站在十字路口。
红绿灯闪烁着，像某种倒计时。
</tf-write>
```

### 2.2 无正文/自包含命令（自闭合标签）

```xml
<tf-mkdir id="002" path="D:/output/chapters" />
<tf-bash id="003" cmd="git add -A" />
<tf-touch id="004" path="D:/output/.keep" />
<tf-copy id="005" path="D:/output/a.md" dest="D:/backup/a.md" />
<tf-read id="006" path="D:/input/prompt.txt" />
```

### 2.3 带属性的完整示例

```xml
<tf-write id="004" path="D:/config.json" need_result="true">
{"port": 3000, "host": "localhost"}
</tf-write>

<tf-edit id="005" path="D:/app.py">
<old>print("hello")</old>
<new>print("hello world")</new>
</tf-edit>

<tf-bash id="006" cmd="npm install express" need_result="true" />
```

---

## 三、属性定义

| 属性 | 必需 | 默认值 | 说明 |
|------|------|--------|------|
| `id` | 是 | — | 最多32位 ASCII 十进制数字戳记。系统提示告知起始值，模型递增。 |
| `path` | read/write/append/mkdir/touch/copy/edit 必需 | — | 文件/目录路径。 |
| `dest` | copy 必需 | — | 复制目标路径。 |
| `cmd` | bash 必需 | — | Shell 命令。 |
| `need_result` | 否 | `false` | `true` 时触发强制打断，执行后立即把结果返回模型。 |
| `injected` | 否 | `0` | 0=未注入全局上下文，1=已注入。由解析器维护，模型输出时填 0 或省略。 |

路径规则：相对路径按 agent 的 `cwd` 解析；绝对路径保留。`read` 工具默认最多返回 `max_read_chars` 字符，避免把巨型文件一次性塞回上下文。首次命令载荷和执行器实际返回的结果完整反馈；正文已原样存在于本次有效请求时才精确去重。已反馈的本机旧账本按独立预算归档。
### 3.1 Tool Flow 分流

ThinkFlow 不把所有工具都旁路化。工具按执行语义分流：

| flow | 含义 | 默认工具 |
|------|------|----------|
| `delayed` | 可预测副作用，不需要中间观察结果，默认不中断推理 | `write`、`append`、`mkdir`、`touch`、`copy`、`edit` |
| `blocking` | 独立调用集中声明，完整批次结果返回后再判断 | `read`、`bash`、`python`、搜索、联网、skills、custom command tools |
| `confirm` | 必须经反馈边界处理的扩展工具类型 | 由工具注册表声明 |

`flow` 与 `risk` 是两个维度；bash 是 blocking 且 high risk。`security.approval_mode` 提供 `auto`、`approve_all`、`request_all`；桌面 balanced 模式对高风险工具逐次询问，批准只对当前调用生效。CLI 保留原策略，没有桌面授权回调时返回明确的未授权错误。路径约束与命令过滤不等于操作系统沙箱。

默认安全规则：

- 文件工具默认只能访问 `cwd` 内路径，除非配置 `security.allowed_roots = null` 或传 `--allow-outside-cwd`。
- read 默认拒绝常见密钥文件（如 `.env`、私钥、npm/pypi 凭证），除非显式 `allow_sensitive_paths`。
- bash 默认 `safe` 模式，拦截明显危险命令，设置超时和输出截断，并只透传基础环境变量。需要开放工作区时可用 `--sandbox open` 或 `--trust-workspace`。

---

## 四、工具定义

### 4.1 write

写入文件（覆盖）。

```xml
<tf-write id="N" path="路径" [need_result="true"]>
文件完整内容
</tf-write>
```

- 成功：非空内容写入磁盘。默认不通知模型。
- 空正文会失败；如果目标是创建空文件，请使用 `tf-touch`。
- `need_result="true"`：执行后打断，返回写入字节数。
- 同一路径多次写入：串行执行，后覆盖前。

### 4.2 mkdir

创建目录（含父目录）。

```xml
<tf-mkdir id="N" path="路径" />
```

几乎不会失败。默认不通知。

### 4.3 append

追加写入文件。

```xml
<tf-append id="N" path="路径" [need_result="true"]>
追加内容
</tf-append>
```

- 成功：非空内容追加到文件尾部。默认不通知模型。
- 空正文会失败；空追加没有可审计副作用。
- 文件不存在时创建文件和父目录。

### 4.4 touch

创建空文件或更新 mtime。

```xml
<tf-touch id="N" path="路径" />
```

默认不通知模型。

### 4.5 copy

复制文件。

```xml
<tf-copy id="N" path="源路径" dest="目标路径" />
```

- `path` 是源文件，`dest` 是目标文件。
- 源文件不存在或目标路径越界时失败并打断。

### 4.6 bash

执行 Shell 命令。

```xml
<tf-bash id="N" cmd="命令" [need_result="true"] />
```

- 无论是否带 `need_result`，均先等待之前的 FIFO 写入完成，再执行并返回 stdout/stderr/exit_code。
- 普通调用在本批生成结束后统一返回结果；同批参数必须已知，不能依赖尚未返回的信息。
- 显式`need_result=true`立即形成反馈边界，同一流块中其后的命令记skipped；模型拿到结果后用新id重新决定。

### 4.7 edit

编辑文件（精确替换）。

```xml
<tf-edit id="N" path="路径" [need_result="true"]>
<old>要替换的文本</old>
<new>替换后的文本</new>
</tf-edit>
```

- oldText 必须在文件中唯一存在。
- 替换失败（oldText 找不到）：视为执行失败，打断。

### 4.8 python

```xml
<tf-python id="N">
from pathlib import Path
assert Path("main.py").exists()
print("checked")
</tf-python>
```

正文是多行Python源码，经stdin交给独立解释器；不经过shell转义、不创建临时脚本。与bash共用cwd、环境、输出、权限、超时和取消。每次调用无持久变量。Python用于数据处理与集中检查，产物修改用受版本管理的文件工具。默认批次反馈，`need_result=true`立即反馈。

### 4.9 原生 tool_use

传统tool_call通道由`ToolRegistry`动态注册。显式启用后，首批开放read/write/edit/bash/python/read_skill/load_tools；其余从同一registry按需load_tools，显式native_tools名单优先。完整工具能力包括：

- 文件/命令：`read`、`list_files`、`glob`、`grep`、`bash`、`python`、`write`、`append`、`edit`、`mkdir`、`touch`、`copy`
- 联网：`web_search`、`fetch_url`
- 多模态插口：`image_generate`
- Skill：`list_skills`、`read_skill`
- 配置式插头：`interfaces.custom_tools[]` 中声明的本地 command tools

原生模式同一响应可声明多个调用、统一反馈；文本模式的读取使用tf-read，搜索和批处理可使用shell或Python。

```json
{"path": "src/agent_loop.py"}
```

- read/list_files/glob/grep/web_search/fetch_url/list_skills/read_skill 属于输入式工具，需要结果才能继续。`read` 同时支持 `<tf-read ... />` 文本协议；其它搜索、联网和 skill 工具仍按传统 tool_use 返回。
- image_generate 默认只是开放接口；只有在 `config.interfaces.image_generation` 配置 command/webhook 后才真实生成。
- custom tools 使用 JSON stdin 传参、stdout 返回结果，适合把已有脚本包装成原生工具。
- list_skills 只返回摘要；选中 skill 后再 read_skill 读取全文，避免初始上下文被大量 SKILL.md 挤占。
- fetch_url/web_search 的网页内容是不可信输入，只能当资料，不能当系统/开发者/用户指令执行。
- OpenAI 兼容流中参数可能分片到达，框架按 tool id/index 聚合。
- Anthropic 使用 `input_schema` 注册同名工具。

---

## 五、解析规则

### 5.1 状态机

解析器逐字符扫描 thinking 流，状态转换：

```
IDLE → 检测到 '<' → 可能是命令开始
     → 检测到 '<tf-read' / '<tf-write' / '<tf-append' / '<tf-bash' / '<tf-mkdir' / '<tf-touch' / '<tf-copy' / '<tf-edit' / '<tf-python' → TAG_OPEN
     → 其他 → 回到 IDLE

TAG_OPEN → 读取属性直到 '>' 或 '/>'
         → '/>' → 自闭合命令，提取完成，状态 = COMMAND_READY
         → '>' → 有正文命令，状态 = IN_CONTENT

IN_CONTENT → 逐字符累积
           → 检测到 '</write>' / '</bash>' 等 → 命令完成，状态 = COMMAND_READY
           → 流中断（abort）且未检测到结束标签 → 格式残缺，丢弃

COMMAND_READY → 输出命令对象 → 回到 IDLE
```

### 5.2 标签形式示意

以下正则仅示意协议外形，不是当前解析实现。实际开标签由引号感知 scanner 处理，不能用 `[^>]*` 解析属性内的 `>`；见 `src/parser.py`。

开始标签 + 属性：
```python
TAG_OPEN = r'<tf-(?P<tool>read|write|append|mkdir|touch|copy|bash|edit|python)\s+(?P<attrs>[^>]*?)(?P<self_close>/?)>'
```

属性提取：
```python
ATTR_ID = r'id="(?P<id>\d+)"'
ATTR_PATH = r'path="(?P<path>[^"]*)"'
ATTR_DEST = r'dest="(?P<dest>[^"]*)"'
ATTR_CMD = r'cmd="(?P<cmd>[^"]*)"'
ATTR_NEED_RESULT = r'need_result="(?P<need>true|false)"'
ATTR_INJECTED = r'injected="(?P<injected>[01])"'
```

结束标签：
```python
TAG_CLOSE = r'</tf-(?P<tool>read|write|append|mkdir|touch|copy|bash|edit|python)>'
```

edit 子标签：
```python
EDIT_OLD = r'<old>(?P<old>.*?)</old>'
EDIT_NEW = r'<new>(?P<new>.*?)</new>'
```

### 5.3 边界情况处理

| 情况 | 处理 |
|------|------|
| 正文包含 `</write>` | 模型需转义为 `<\/write>`。解析器检测到转义序列时还原。 |
| 正文包含 XML 特殊字符 | 不需要转义。解析器按原始文本提取。 |
| 命令块被 abort 截断 | 检测到不完整（有开始标签无结束标签）→ 丢弃，记日志。 |
| id 跳号 | 允许；运行状态按最大已记录数字戳记继续。 |
| id 重复 | 解析器校验。重复 → 记 error，打断。 |
| 属性顺序不固定 | 正则按名提取，不依赖顺序。 |
| 属性缺失（如 write 没有 path） | 解析器校验。必需属性缺失 → 视为格式错误，丢弃，记日志。 |
| 命令块出现在正文 | text parser 兜底执行，`SafeTextStreamFilter` 会跨 chunk 剥离 canonical 命令块，渲染和消息历史都不残留工具标签。 |
| 普通 `<write>` 示例 | 默认不执行；显式 `--allow-legacy-tags` 才兼容旧标签。 |

---

## 六、执行指引

框架协议正文与推荐工程指引分别定义于`src/prompts.py`。`AgentLoop.build_system_prompt`根据当前真实工具集合默认注入协议，供CLI、桌面和直接嵌入调用共用。自定义或关闭工程指引均保留此协议。

`src/cli.py:resolve_system_prompt`只选择工程/用户指引并加载工作区规则：非空`system_prompt`或`--system-prompt 文件`替换推荐工程指引；`use_builtin_system_prompt=false`且自定义为空时关闭推荐指引，`--no-system-prompt`关闭工程/用户指引。工作区规则独立由`context.enabled`控制。`--print-system-prompt`显示实际合成结果。桌面设置提供推荐／自定义／关闭，默认推荐。

连续执行、信息合批、FIFO及need_result是框架能力与协议；项目风格、最小必要信息、验证方法和交付习惯是可选工程建议，不设强制验收门禁。完整用法在`src/skills/thinkflow/SKILL.md`按需加载。

- 已知且独立的信息需求一起声明，例如同一轮输出三个tf-read，再结束生成。
- 依据结果确定修改后，连续输出文件操作，并在同次生成末尾安排已经知道要做的检查。FIFO保证先落盘再检查。
- 信息结果和真实失败完整保留；选择所需文件、搜索匹配和输出字段，避免重复索取相同内容。
- 检查通过且没有相关新修改时交付。新失败或具体未满足需求才继续，不为假想故障扩张实现与检查。
- id按运行状态给出的戳记递增；只执行完整canonical命令，代码围栏中的示例不执行。
- 指引与有效工具配置一致；CLI切换provider同步原生说明，保留工作区上下文和自定义提示。

---

## 七、注入格式

下一轮 API 调用时，未注入（injected=0）的命令会作为 command ledger 注入上下文。ledger 不只是“成功/失败日志”，还包含 flow、risk、hash 和 summary，方便模型稳定对账，也方便 session 恢复后继续长程任务。

```text
[THINKFLOW COMMAND LEDGER — 上一轮可审计工具记录]
以下是上一轮执行过的结构化命令记录。hash 用于对账；summary 用于快速恢复上下文。

<write id="001" path="D:/novel/ch01.md" status="success" flow="delayed" risk="low" hash="a1b2c3d4e5f60718" bytes="17831">
天空灰蒙蒙的，主角站在十字路口。
（示意正文；首次反馈保留完整载荷）
</write>
  summary: write 17831 bytes to D:/novel/ch01.md

<mkdir id="003" path="D:/output" status="success" flow="delayed" risk="low" hash="91aa22bb33cc44dd" />
  summary: mkdir D:/output

<bash id="004" cmd="git status --short" status="success" flow="blocking" risk="high" hash="abcd1234abcd1234" exit_code="0" />
  summary: bash exit_code=0 stdout= stderr=

[END COMMAND LEDGER]
```

- 成功命令：首次完整反馈载荷；当前请求中已有相同完整正文时只返回回执元数据。
- 信息批次：按声明顺序合并所有未反馈的成功blocking结果，入消息后标记已反馈；快照恢复不重新执行。
- 失败的命令：保留 status="failed"、summary 和错误信息，模型下一轮必须根据错误调整。
- need_result 命令：结果详细注入（stdout/stderr/exit_code 或写入详情），并立即进入下一轮。
## 八、上下文压缩

长程会话默认启用确定性 auto-compact。压缩不调用模型，不引入不可检查的语义改写；框架只把较旧的 `messages` 汇总成一条普通 user 消息，并保留最近消息原文。

压缩消息以 `[THINKFLOW COMPACTED CONTEXT]` 开头，包含被压缩消息数量、角色、tool_call id、内容片段等。为了保持 OpenAI-compatible tool message 合法性，压缩边界不会把 `tool` result 单独留下。

摘录会丢失细节。已反馈的账本最多保留4096条，正文/输出保留总预算约200万字符；被裁剪处有明确标记，hash与摘要保留，未反馈结果不丢弃。桌面展示最近120条消息和500条回执，单条长消息显示末尾32000字符，完整展示对话可导出。它不是文件版本仓库。

---

## 九、版本历史

| 版本 | 日期 | 变更 |
|------|------|------|
| 1.6 | 2026-11-10 | 工具协议由AgentLoop默认注入；工程指引可替换/关闭；工作区规则独立，桌面三模式与实际提示预览。 |
| 1.5 | 2026-11-10 | 默认流式协议、独立信息调用整批反馈、tf-python、与有效配置同步的指引；保留即时依赖与失败边界。 |
| 1.4 | 2026-09-29 | 强制信息边界、native去重/失败短路、取消清理、实例事件、运行限额、桌面意图恢复与授权。 |
| 0.1 | 2026-06-29 | 初版。定义 write/mkdir/bash/edit 四种工具、XML 标签格式、状态机解析规则。 |
| 0.2 | 2026-06-30 | 补 read 原生 tool_use、cwd 路径规则、正文 fallback、Windows/会话恢复等生产化约束。 |
| 0.3 | 2026-06-30 | 补开源默认安全策略、bash policy、环境变量白名单、API 重试和真实 smoke 脚本。 |
| 0.4 | 2026-06-30 | 补确定性 auto-compact、工具日志截断、状态/诊断/初始化命令。 |
| 0.5 | 2026-06-30 | 补正文安全流式过滤，工具块可跨 chunk 剥离，普通文本即时显示。 |
| 0.6 | 2026-06-30 | 默认切到 canonical `tf-` 协议，补全 native tools、slash completion、安全预设和底部上下文状态。 |
| 0.7 | 2026-06-30 | native tools 改为动态 ToolRegistry，新增 web_search/fetch_url/image_generate/list_skills/read_skill/custom command tools。 |
| 0.8 | 2026-06-30 | 新增 append/touch/copy 旁路工具，补 Markdown 最终渲染、finish_reason=length 自动续写、usage savings 与特殊渲染。 |
| 0.9 | 2026-06-30 | 系统提示词默认空；内置协议提示词改为显式 opt-in，并移除作者身份信息默认注入。 |
| 1.0 | 2026-06-30 | 曾将内置 harness 提示词改为默认注入，自定义提示词完整覆盖；补文件整理、完成报告、工具运行状态和回合命令摘要。 |
| 1.1 | 2026-06-30 | 补工具 kind 分流、代码块示例防误执行、流式 transport error 续写、敏感配置拦截/脱敏与 fetch_url SSRF 防护。 |
| 1.2 | 2026-06-30 | 曾恢复为默认空系统提示词（显式 opt-in）。（勘误于 1.3：beta.2 发布代码实际为默认注入内置提示词，本行按代码行为修正描述。） |
| 1.3 | 2026-08-25 | 内置 `thinkflow` skill 随包分发（`src/skills/thinkflow/SKILL.md`，npm/pip 双渠道收录）：完整 harness 使用指南改为 skill 按需拉取，系统提示词精简为协议骨架 + `read_skill("thinkflow")` 指引，用户同名 skill 可覆盖内置。补 edit 原子写入、id 去重滚动窗口（parser 512 / 执行层 4096）、`--version`、CLAUDE.md 工作区上下文兼容。 |
