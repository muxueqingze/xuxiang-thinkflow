# CI失败定位与最小修复 · 2026-11-10

读取截至`d46fb40`的最近20次CI：16次成功、4次失败。只保留定位结论与原始运行链接，不复制整份日志。

| 运行 | 原因 | 状态 |
|---|---|---|
| [42748202711 · 6d85c06 PR](https://github.com/muxueqingze/xuxiang-thinkflow/actions/runs/42748202711/job/129605288766) | `test_bounded_bash.py`在pid.txt创建但未写完时读到空串；取消前异常又引发临时目录WinError32。另`test_efficiency_runtime.py`用25ms流间隔假定所有写入完成，流结束时序断言失败。251项结果为1失败、2错误。 | 本次修复两处测试竞态；不改产品逻辑。 |
| [42735445136 · 3815b2d PR](https://github.com/muxueqingze/xuxiang-thinkflow/actions/runs/42735445136/job/129563419483) | 同一`test_same_stream_writes_then_real_shell_check`的流结束时序断言失败。 | 与本次事件同步修复对应。 |
| [42663147346 · 82427438 PR](https://github.com/muxueqingze/xuxiang-thinkflow/actions/runs/42663147346/job/129319687161)、[42663135864 · push](https://github.com/muxueqingze/xuxiang-thinkflow/actions/runs/42663135864/job/129319645005) | 浅克隆缺历史基线，`git rev-parse 2a4f4b8`返回128。 | 已由`48c818e1`为checkout增加`fetch-depth: 0`修复。 |

## 对照证据

同一`6d85c06`的[分支push](https://github.com/muxueqingze/xuxiang-thinkflow/actions/runs/42748195634)与[v0.8.0标签push](https://github.com/muxueqingze/xuxiang-thinkflow/actions/runs/42748337347)均首次通过，三次运行使用相同Windows runner镜像、Python 3.12.10与依赖版本。PR实际合并提交`73c4456`与`6d85c06`的Git tree均为`c16c9165ecdf70b76b76ba4c2e134a20d1262ad0`，不存在测试内容差异。

后续`d46fb40`的[PR](https://github.com/muxueqingze/xuxiang-thinkflow/actions/runs/42748692232)与[push](https://github.com/muxueqingze/xuxiang-thinkflow/actions/runs/42748686560)通过；该提交只改文档，不能据此判定上述竞态已修复。失败日志未显示安装或服务基础设施故障，具体错误发生在测试同步与清理路径。

## 本次修复与验证

- PID夹具先写完并关闭`pid.pending`，再原子替换成`pid.txt`，使文件出现代表PID已完整发布。取消测试用finally确保任务取消并等待结束，前置断言失败也会收尾。
- 流式测试夹具在第一帧后等待第二条写入的tool_completed事件，再发送后续工具调用和结束帧；FIFO和“写入在流结束前完成”的原断言保持。等待设有测试超时，不增加固定sleep。
- 使用项目现有unittest加载方式定向运行取消子进程、超时子进程、同流写入后真实shell检查三项：全部通过，2.478秒。启用`-B`及PYTHONDONTWRITEBYTECODE；三个本轮临时目录均已消失，子进程终止断言通过。未运行全套、外部模型或远程CI重跑。

运行逻辑保持原样；修改两个测试夹具、本文档与接力索引。远程CI结果以集成提交之后的实际运行为准。

## 2026-11-11：Windows目录别名断言

`b7ac69c`的[失败运行](https://github.com/muxueqingze/xuxiang-thinkflow/actions/runs/42867612372)中269项测试仅一处失败：输出原文的实际目录使用`runneradmin`，测试期望路径使用Windows短名`RUNNER~1`，二者指向同一目录。`38fc9a1`将字符串路径相等改为`Path.samefile`比较目录实体，运行逻辑不变。该提交的[push](https://github.com/muxueqingze/xuxiang-thinkflow/actions/runs/42870619195)及[PR](https://github.com/muxueqingze/xuxiang-thinkflow/actions/runs/42870634186)均通过。

## 2026-11-11：桌面 Git 路径与终端编码

`d34ef25` 的 [PR](https://github.com/muxueqingze/xuxiang-thinkflow/actions/runs/42983581066)和 [push](https://github.com/muxueqingze/xuxiang-thinkflow/actions/runs/42983573845)检查均在新桌面服务测试中失败：Git 目录断言再次误用长/短路径字符串比较；英文 Windows 的 PowerShell Console 默认编码将中文输出变为问号。前者沿用 `Path.samefile`；后者在新建终端时设置 UTF-8，中文输出断言保持。局部真实 CP437→UTF-8 复现及两项回归通过，详见[交互节点验证](reviews/interaction-20261111.md#github-windows-runner-的两处差异)。
