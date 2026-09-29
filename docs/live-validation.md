# v0.8 真实模型验证

2026-09-29；官方DeepSeek端点、`deepseek-flash`。测试使用隔离工作区与私有测试profile，不接触真实项目文件。模型列表实际返回Flash与V4 Pro，连接诊断成功；本轮只调用Flash。

协议依据：[官方入口](https://api-docs.deepseek.com/)、[思考模式及工具后续轮](https://api-docs.deepseek.com/guides/thinking_mode/)。实现显式控制thinking，并保留原生工具请求后续轮需要的reasoning_content；界面只显示活动状态。

## 已获得的执行证据

| 场景 | 结果 | 观测 |
|---|---|---|
| 连续创建3个独立文件 | 内容逐文件正确；1次API请求 | 3次写入均先于同轮stream_finished；2.782秒 |
| 原生读取→流式修改→运行断言→更新计划 | 修改正确、CHECK_OK、exit 0，计划引用read/edit/bash真实回执 | 修复回执ID可见性后的样本5轮、8.343秒 |
| 完整读取后，人为插入外部修改 | 第一次append被SHA冲突拒绝，模型重新读取后仅追加一次 | 外部内容保留，4轮、4.375秒 |
| 完整桌面：流式创建Python与Markdown→授权验证→查看diff | 两次delayed写入、一次blocking bash、CHECK_OK；Markdown代码围栏及示例完整 | 最后源码样本2轮、4.929秒，7025个已报告token，无页面错误 |
| 最终Windows独立包的同一完整任务 | 两次写入均先于同轮stream_finished；一次授权、CHECK_OK、exit 0 | 2轮、4.528秒、6447个已报告token；后端来自包内，验证命令使用本机Python，无页面错误 |

原始经过与失败样本没有隐藏：

- 首次mixed计划用了错误回执引用，框架拒绝。原生工具结果补上可读的receipt ID后，后续样本引用正确。
- 首个打包样本使用原生批量write，完成任务但没有兑现默认流式路径。改为默认不公开原生文件输出schema；解析失败才在本次run开放保底，显式allowlist仍保留。
- 一次桌面任务已完成，但并行启动多个Electron测试时截图超时。只重开同一个已完成profile核验，没有重跑模型来“刷通过”。
- 早期脚本要求每轮都返回usage，误拒绝了提前反馈关流的样本。现在区分完整用量与已知下限，不把缺失解释为零。
- Windows命令过度转义的样本没有授权第二个异常命令。框架现明确实际平台/shell，并优先原生read/bash，减少XML内嵌命令转义。
- 交付包样本进一步定位到解析器没有还原`&quot;`等XML属性实体；修复为单次解码五个命名实体和匹配属性定界符的转义，保留普通Windows路径反斜线。用真实Shell输出ATTR_OK及逐字符解析、双重转义回归验证，不靠重试掩盖问题。
- 真实Markdown输出暴露围栏被误过滤，另一样本反复读取/验证直到触发10轮限额。修复文件正文围栏处理，并明确成功后不重复验证；新的同任务样本完整保留代码围栏并在2轮结束。未取消运行限额。

这些是机制与兼容性验证，不是统计基准，不证明普遍提速或降低账单。报告保留失败与恢复成本；没有把estimated_saved_api_calls当实际收益。

## 复现与凭据

`python -B scripts/live_deepseek_eval.py --run`为有限headless场景；`node desktop/tests/electron-live.cjs --live [--packaged]`运行真实桌面，普通测试与CI不会调用它们。凭据从当前进程环境或本机当前用户DPAPI位置读取，不写入源码、报告或命令行参数。公开证据只选择统计与截图，不复制私有profile、模型消息快照或设置文件。

最终独立包结果与离线验证见[v0.8接力](v0.8-handoff.md)和`docs/evidence/v0.8-desktop/`。长时负载、多操作系统、完整成本对照及未签名包的商用分发仍是独立工作；本轮按用户要求不安排Pro终审。
