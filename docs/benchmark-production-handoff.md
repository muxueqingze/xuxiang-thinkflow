# 生产配置基准接力 · 2026-09-29

状态：全部生成、唯一事故补测、独立复核和本地后置修补完成。没有活动模型进程，不要重复启动benchmark。正式报告在 `bench/harness_benchmark_20260929/reports-production/README.md`。

## 已完成

- 冻结核心91e491167564f625334b812d6f0a422d2315b23e，原六题×两次×三harness；OpenAI请求省略输出budget，生产worker不设总轮次/总时间/续写预算，不做自动上下文压缩。Pi/OpenCode使用实际模型能力元数据与只移除SDK输出budget的本地钩子。
- 原始目录 `artifacts/benchmark-20260929/production-91e4911`；唯一恢复 `production-91e4911-observer-recovery`。36有效正常交付（各12/12）；原36尝试中35正常、1观察器故障，不冒称首次36全正常。
- 有效token：ThinkFlow3,845,627、Pi1,397,532、OpenCode1,732,556；平均105.38/54.62/59.90秒。全部实际37尝试397请求7,022,746token，原中断47,031计入。没有length截断，17次输出超旧8K。
- 独立核对原/补测manifest完全相同，后置修补前38文件指纹一致；397请求预算字段缺失、消息hash一致、usage完整。三份发布JSON离线重评分8/8。25次有效ThinkFlow文件操作与stream重叠。
- 三harness均12份最终实现上的真实测试成功与公共测试证据；五份特殊脚本/壳命令经过人工复核，保留依据。证明执行成功，不证明模型自建测试覆盖充分。
- 核心新增13项、桌面Node13项通过。早期全套parser/core通过，unittest唯一旧Anthropic fixture修复后5项定向复核；后置装置/报告12项和monitor5项通过。当前提交远端CI以PR检查为准。
- 37个专用CLI profile核对绝对路径与完成状态后已送回收站，剩余0；专项审查temp已回收。原始日志、失败记录、代码产物和旧报告保留。

## 观察器事故与修复

repair_routes-thinkflow-r2因父进程GBK stdout不能编码U+FFFD而被run.py误终止。产物虽8/8，exit1且无harness收尾，原4请求47,031token保留。第22项结束前在下一待跑目录设置只有OBSERVER_PAUSE.txt的暂停门，触发原incomplete guard在任务边界退出；门核对后回收。原冻源不变，以PYTHONIOENCODING=utf-8恢复同名实验，已完成样本全部跳过。之后只对事故项同题/同prompt/同repeat补一次，正常8/8、16请求304,630token。

全部生成结束并核验指纹后，永久将monitor进度JSON改ASCII转义；run_one捕获可选progress异常并保存observer_errors，不再kill worker，关键meter.finish失败仍传播。离线复现GBK和注入观察器异常均通过。这些后置修补不属于被测91e4911，未来新跑须新建实验名，不要续入旧数据。

## 判断与下一步

8K/24请求/300秒是此前装置的人为条件，用它代表生产表现有误。取消以后，ThinkFlow仍更慢、更费；有效token较旧轮观察值+81.1%、时间+37.1%，不是本轮性能提升。具体损耗包括协议恢复、自建测试修订、验收后多余探测和历史重传；未发现native bash成功stdout丢失。案例阶段成本不等于可以直接省掉的量，详情见独立cost-cases报告。

下一步若继续优化，针对已记录的协议歧义、解析错误非正文诊断、验收后及时收尾开展最小实验；不得恢复额度来压分，也不再次无目标全量重跑。六题不等于原前端/长篇小说基准。旧桌面包未重打，本轮更新源码和报告，不发布Release/npm，不使用Claude Code或Pro。
