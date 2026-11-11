# Desktop / CLI 交互独立定向验收 · 2026-11-11

基线 `969377c`，检查当前工作树。验收者实际启动隔离 Electron，审阅实现、DOM 和截图；不使用模型端点，不读取或修改正常 Desktop/Codex 登录。

## 发现与收口

1. **P2，窄窗展开右栏后重开侧栏，挤掉会话标题与操作。已修复并独立复核。**
   - 1000×700 外窗对应 986×664 内容区；右栏展开至 542.29px 后重开 240px 左栏，会话只剩 203.71px，标题宽 0，输入正文宽 122.38px，终端按钮右边 464 超出右栏起点 443.71，实际截图出现按钮遮挡、引用文件竖排。
   - 根因位于 `desktop/renderer/workbench-layout.js` 的侧栏开合/拖拽与 `workspace-panel.js` 的展开阅读宽度计算：右栏尺寸没有随剩余空间调整。
   - 修复后的统一 `constrainPaneWidths` 保留会话 400px。独立复验所有面板同时打开、展开阅读后重开侧栏：左栏 306px、会话 400px、右栏 280px；标题 124.29px，输入 318.67px，终端按钮右边 654.29 小于右栏起点 706。实物层级、排版和操作区恢复正常。
   - 原始 DOM：[qa-layout.json](../evidence/interaction-20261111/qa-layout.json) `expanded`；复核：[qa-fixed.json](../evidence/interaction-20261111/qa-fixed.json)、[最终窄窗截图](../evidence/interaction-20261111/qa-overlap-fixed.png)。

2. **P2，高级 JSON 输入 `null` 后修改分类字段抛异常且无可见提示。已修复并独立复核。**
   - 高级配置输入 `null`，切换上下文分类，把输入 token 预算填为 300，再切分类。`settings-panel.js` 分类 change handler 直接访问 `target[part]`，抛出 `Cannot read properties of null (reading 'compaction')`；JSON 仍为 null，用户看不到错误。
   - 修复后分类写回验证对象结构，返回高级配置并聚焦原 JSON，显示修正提示。实测保留 `null` 原文，`pageerror=[]`。
   - 原始异常：[qa-null-config.json](../evidence/interaction-20261111/qa-null-config.json)；复核：[错误提示截图](../evidence/interaction-20261111/qa-json-error-fixed.png)、[qa-fixed.json](../evidence/interaction-20261111/qa-fixed.json)。

3. **P2，终端底部纯黑色余量条。已修复并独立复核。**
   - 独立浅色/深色截图及主会话两张工作台截图均可见一条贯穿终端内容区的纯黑条，浅色界面尤其突兀。
   - 在 986×664 内容区命中 `(500,651)`，元素为 `.xterm-viewport`，计算背景 `rgb(0,0,0)`，rect `(322,464.67,648,191.33)`。终端按完整字符行铺正文后，底部余量露出 xterm 默认黑色 viewport。
   - 修复位置：`desktop/renderer/styles.css` 的 `.terminal-view .xterm-viewport`，背景复用 `--surface`。不涉及 PTY 或输出数据链。
   - 第二阶段独立启动确认 viewport 计算背景为 `rgb(255,255,255)`，浅色截图底部黑条消失。

## 实际核对

- Electron 使用专属 `THINKFLOW_TEST_USER_DATA`，实际回读确认 profile 路径；启动环境彻底删除 `ELECTRON_RUN_AS_NODE`。
- 真 ConPTY 经 xterm 键盘输入 `Write-Output "QA_TERMINAL_OK"`，缓冲区确认结果；键入 `exit 7` 后标签显示“已退出 7”；切工作区创建新的 PowerShell 1，旧视图销毁。
- 侧栏以 40ms 间隔快速开关反转，终态为 inert、宽约 0、内容固定宽样式清空。终端分隔条键盘上移 16px，面板从 248 增至 264px。实际鼠标拖拽侧栏至上限。
- 分类预算值 12345 写入高级 JSON；高级 JSON 改为 23456 后分类同步读取。设置模态打开时尝试聚焦背景 prompt，焦点留在设置分类。
- 已查看 1000×700 所有面板、1380×900 深色、浅色设置及主会话浅/深 Git diff 截图。正常布局留白、文字层级、面板风格一致；窄窗问题按上项修复收口。
- CLI 审阅 `cli.py`、`tui.py`、`interactive_controller.py`、`renderer.py` 实现，并实际运行单项 `python -m unittest discover -s tests -p test_cli_interaction.py -k cancel_pauses_fifo -v`：1 项通过。覆盖停止暂停 FIFO、连续取消、原文换行和缩进、运行中禁止切会话、退出等待保存、退出不消费后续队列。未重复全量测试。
- 阅读 Git 前后端与 service 路由，实际查看主会话 Git diff 截图；三类 Git diff 运行结果由主会话负责，本报告不冒称独立重跑。

原始实测摘要见 [qa-observations.json](../evidence/interaction-20261111/qa-observations.json)，初始终端截图见 [qa-1000-dark.png](../evidence/interaction-20261111/qa-1000-dark.png)。每次探针均在 finally 中关闭 Electron 及其后端/终端。收尾已确认无专属 profile 路径的存活进程；专属 `temp/interaction-qa-20261111`（profile、两工作区、探针）及十个重复/中间证据文件已送回收站，`TempExists=False`。仅保留本文使用的七项证据。

## 当前结论

没有发现 P0/P1。两项交互 P2 及终端余量条均已由主会话修复并独立复核。用户追加权限弹窗与上下文展示细节，本轮仅完成已述差异的阶段检查；新交互完成后按新差异复验。本报告不作为全部交互工作的最终交付。

## 第二阶段：输入区细节（追加）

仅检查新增 `composer-controls.js` 与授权区域，不重复第一阶段全量工作流。实际查看主会话 permissions / approval / context / permissions-1000 截图，再独立启动 Electron、配置本机回环 SSE，真实产生 24 行命令授权并选择拒绝。执行两次本地模型响应，无公网请求。

### 新发现

1. **P2：上下文菜单 Esc 关闭触发 popover 重入异常。已修复并独立复核。** 上下文图标 focus→Enter→Tab 到“整理上下文”→Esc，`closeComposerPopover.hidePopover()` 把焦点还给图标，图标同步 focus 事件再次 `showPopover()`，抛 `InvalidStateError: Invalid to show a popover during another show operation`。栈位于 `composer-controls.js` 的 openComposerPopover、contextOpen、closeComposerPopover 和 keydown handler。应在关闭时完成焦点转交，避免 hide 期间重开。

2. **P2：授权换态留下上下文浮层且焦点落回 BODY。已修复并独立复核。** 运行中点上下文固定浮层，真实 `approval_required` 到来后 composer 隐藏，浮层仍打开并遮住长命令右半，`document.activeElement` 为 BODY。`renderApproval` 首次进入新 request_id 应关闭输入区菜单，并把焦点转到授权卡；后续刷新不抢焦点。见 [授权与浮层重叠](../evidence/interaction-20261111/qa-detail-approval-with-context.png)。

3. **P2：展开长命令出现嵌套滚动，收起按钮沉出可视区域。已修复并独立复核。** 1000×700 内容区 986×664；授权面板 bottom=656，命令 pre 高228 / scrollHeight514，但收起按钮 y713.33–737.83，超出视口。画面出现内外双滚动条，用户要滚动外容器才能找收起。建议命令正文单独滚动、展开/收起控制固定在正文外。见 [长命令展开](../evidence/interaction-20261111/qa-detail-long-expanded.png)。

### 本阶段通过项

- 权限菜单初始聚焦当前“平衡”，ArrowDown 到“开放”，Esc 关闭并返回 prompt。
- 上下文可通过 Tab 到达操作按钮。
- 24 行命令完整进入授权正文，拒绝后“保留  草稿\n第二行”的空格、换行原样恢复。
- 1000×700 全面板模式，权限浮层仍在视口内；[截图](../evidence/interaction-20261111/qa-detail-all-panels-menu.png)已亲看。
- xterm viewport 黑底修复通过。

实测 [qa-detail-observations.json](../evidence/interaction-20261111/qa-detail-observations.json) 保存异常栈、DOM、原文和请求数。原始复现进程已退出，两份原始测试 profile 已回收。


### 第二阶段失败项修复与新轨道复核

- 新启动隔离 Electron 只复核上述三项与直接影响，`errors=[]`。上下文 Esc 关闭后焦点回 prompt；真实授权到来时 `contextOpen=false`、`focus=approval`，没有误聚焦允许按钮。拒绝后草稿原文保留。
- 24 行命令展开后正文成为单一滚动区：命令 pre y437.49–564.83 / clientHeight127 / scrollHeight514；收起按钮 y564.83–589.33、允许按钮 y601.33–635.33，均在授权卡 bottom648 内。实际点击收起成功。两张修复截图均已亲看：[授权换态](../evidence/interaction-20261111/qa-detail-fixed-approval-with-context.png)、[长命令展开](../evidence/interaction-20261111/qa-detail-fixed-long-expanded.png)。[实测 JSON](../evidence/interaction-20261111/qa-detail-fixed.json)
- 新排版 1380×900 草稿输入轨道宽672px，标题贴近输入，左侧引用/权限与右侧上下文/模型/发送分组明确。[宽窗草稿](../evidence/interaction-20261111/qa-detail-track-1380-draft.png)
- 1000×700 所有面板打开：会话400px、输入轨道368px、footer342.67px，左右操作组78.65/125.11px，发送28×28px，控件没有碰撞或越出卡片。两行草稿保持正常行高；权限和模型浮层均可见。[全面板](../evidence/interaction-20261111/qa-detail-track-1000-all.png)、[权限](../evidence/interaction-20261111/qa-detail-track-1000-policy.png)、[模型](../evidence/interaction-20261111/qa-detail-track-1000-model.png)、[DOM记录](../evidence/interaction-20261111/qa-detail-track.json)
- 上述截图逐张查看，修复后没有新的功能或审美阻塞。原三项 P2 收口。主会话随后新增的 DeepSeek 思考快捷入口另按新增差异核对，不计入本次通过范围。
`temp/interaction-qa-detail-20261111` 的复核 profile、工作区及三个探针均已送回收站，回读 `TempExists=False`；已确认没有使用该路径的存活测试进程。正式证据保留于本报告所链目录。

## 最终新增差异定向复核

本节仅检查全屏设置/搜索、真实工具技能目录、思考控件布局、Git 行内差异、多文件标签及本轮 CSS；不重复授权修复或全套测试。另启独立 profile，配置只指向本机未调用的地址，本轮模型请求为零。

### 发现

1. **P2：搜索折叠目录字段后未展开详情。已修复并独立复核。** 搜索“额外目录”→ArrowDown→Enter，技能页显示，但 `.skill-discovery-settings.open=false`，目标字段仍折叠，焦点掉到 BODY。`settings-panel.js` 搜索定位只切分类/focus/scrollIntoView，应先展开字段祖先 details。[原始记录](../evidence/interaction-20261111/qa-final-search-before.json)、[画面](../evidence/interaction-20261111/qa-final-search-directory.png)。

2. **P2：新活动文件标签没有滚入可视范围。已修复并独立复核。** 1000×700 侧栏和346px右栏同时打开，两份文件标签列表 clientWidth269 / scrollWidth307；打开第二份文件后该活动标签的末端及关闭按钮仍处于右侧裁切区。`selectFileView` 未同步滚动活动 tab，用户需手动横滚才可关闭刚打开文件。[截图](../evidence/interaction-20261111/qa-final-file-tabs.png)。

3. **P3：技能正文初次显示的可读面积偏小。已修复并独立复核。** 读取后预览从占位符长成403px高，但只在读取前 scrollIntoView；1000×700时预览下边790，设置正文下边597。首屏先展示重复 name/source/path，真正正文仍在下方。可在读取完成后重新定位预览；不用扩展为新的功能。

### 实际通过的新增行为

- 全屏设置在986×664内容区占满视口，导航224px，正文762px，页脚 y597.33–664；宽窗与小窗均无横向溢出。[宽窗](../evidence/interaction-20261111/qa-final-settings-wide.png)、[小窗](../evidence/interaction-20261111/qa-final-settings-1000.png)。
- 在隔离工作区 `.agents/skills/qa-preview/SKILL.md` 建立真实本地技能；来源筛选“项目·Codex”显示1项，打开后读到独立磁盘正文。[技能预览](../evidence/interaction-20261111/qa-final-skill-preview.png)。初次探针用错项目技能根目录，改为源码规定的 `.agents/skills` 后验证；没有将夹具错误算产品问题。
- 工具目录以 read 搜索实际呈现 read/list_skills/read_skill 三项，并显示真实原生总开关状态。[工具目录](../evidence/interaction-20261111/qa-final-tools.png)。
- 真实隔离 Git 仓库两份修改同时行内展开，每份新增行均为新行号3，旧行号为空；长路径允许横向滚动，行号与增删颜色可辨。[Git多展开](../evidence/interaction-20261111/qa-final-git-inline.png)。
- 同工作区新建会话后两个文件标签和当前文件保留。小窗长模型名显示在96px宽控件内省略，思考24×28、发送28×28；操作行342.67px，右侧控件共182px，不与左侧权限相撞。[长模型名与思考](../evidence/interaction-20261111/qa-final-thinking-long-name.png)。
- 本节全部截图已逐张亲看，实测 `pageerror=[]`。[完整DOM与行为记录](../evidence/interaction-20261111/qa-final-observations.json)。主会话已有六次provider请求体测试，本节不重复调用模型，也不冒称独立重跑这些测试。

本轮应用已正常关闭，原两份测试profile及错误夹具已送回收站；保留正确隔离夹具/探针等待上述失败项的定向复核。


### 最终三项收口

仅复核本节三项失败及直接影响，未重跑已通过单元，`pageerror=[]`。

- 搜索“额外目录”后 details 已展开，焦点为 `interfaces.skills.roots`，字段 y263.50–347.11 完整位于设置正文内。[复核截图](../evidence/interaction-20261111/qa-final-fixed-search.png)
- 第二个活动文件标签自动横滚38px，关闭按钮 x960.15–977.73 位于标签视口右界978之内。实际点击关闭成功，剩余1个标签，切回第一份文件。[复核截图](../evidence/interaction-20261111/qa-final-fixed-file-tabs.png)
- 技能实际正文返回后预览顶部56.21，对齐内容区顶部56；底部458.99位于内容区下界597.33内。初次即能看到实际正文，避免先滚外层寻找。[复核截图](../evidence/interaction-20261111/qa-final-fixed-skill-preview.png)

三张截图逐张亲看，DOM及行为见 [qa-final-fixed.json](../evidence/interaction-20261111/qa-final-fixed.json)。本次新增差异定向复核通过，报告所列问题均已收口；这是本交互单元结论，不是版本级全面终审。
最终复核进程正常退出；检查无使用本次专属路径的存活进程后，已将整个 `temp/interaction-qa-final-20261111`（复核profile、Git夹具、两份探针）送回收站，确认 `TempExists=False`。仅保留报告和必要 qa-final 证据。
