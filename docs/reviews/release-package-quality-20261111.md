# 0.9.0 正式桌面包定向验收

2026-11-11。最终发布候选为 `artifacts/desktop-2026-11-11T11-00-34-883Z/`；首轮完整后端流程在 `artifacts/desktop-2026-11-11T10-46-17-041Z/` 验证，最终包仅复核下述原失败项及直接影响。两轮均直接运行 `ThinkFlow-win32-x64/resources/backend/thinkflow-service.exe`，没有运行源码服务、重新安装或重复整套 UI 检查。

## 问题

**P3，请求头版本仍为 0.8.0，已关闭。** 首轮两次实际 HTTP 请求的 `User-Agent` 均为 `ThinkFlow/0.8.0`，对应 `src/agent_loop.py` 的硬编码。集成者改为读取公开 `__version__` 后重建；最终包实发 localhost 请求已确认 `User-Agent: ThinkFlow/0.9.0`。

**P1，打包终端缺少 OpenConsole.exe，已关闭。** 安装包实现者定位到 PyInstaller 未收集 ConPTY helper，并通过只补 helper 的 A/B 确认根因。最终包已带 `resources/backend/_internal/winpty/OpenConsole.exe`（1,089,376 字节）。本审查者直接启动最终打包后端，独立复核两个真实终端：分别计算输出 `TFQA_A_42`、`TFQA_B_63`；第二个 `exit 7` 后退出码为 7，第一个仍能计算输出 `TFQA_STILL_144` 且保持存活。输入命令把标记字符串分段拼接并执行乘法，完整预期标记不存在于原始输入，排除了只看到键盘回显的假通过。

## 实物结果

| 检查 | 实际结果 |
|---|---|
| 版本 | Setup PE 的 FileVersion/ProductVersion、ThinkFlow.exe PE 版本、app.asar 内 package.json 均为 `0.9.0` |
| 私有 NDJSON | 冷启动打包后端 → initialize → open_workspace → run → shutdown，均收到有效回执；两个后端进程均退出 0 |
| 本地 SSE 与文件副作用 | 两次 localhost HTTP 请求；真实 `write#1` 写出 `bundle-output.txt`，`read#2` 读回 `packaged-0.9.0-actual-output`，两条成功账本 |
| 增量模块随包可用 | 流仍打开、write 完成事件已收到时，磁盘 `.changes.jsonl` 为 2,729 字节；此时真实目标文件已存在且内容正确 |
| 完整保存与输出 | 轮结束后的 JSON checkpoint 含两条完整回执；`.changes.jsonl` 为零字节，`.index` 已生成，`.outputs` 内有一个实际输出文件；第二次请求含 read 原文 |
| 跨进程恢复 | 完整关闭首个后端，再启动同一打包 EXE；同一会话的消息和账本逐项相等，`recovery_required=false` |
| 恢复不重放 | 重启前后 HTTP 请求总数保持 2，文件仍为唯一预期内容；导出含 write/read 账本 |

原始结果见 [结构化证据](../evidence/release-package-quality-20261111.json)。这些检查实际经过打包的 provider、parser、executor、output_store、session_delta 和 desktop_service 路径，能覆盖本轮新增模块未随包收集的直接问题。

最终发布候选定向验收 **PASS**，上述两项均已关闭。最终包只重查双终端执行、退出隔离与实际 HTTP 请求头：1 次模型请求、2 个终端，后端正常退出 0。结构化证据的 `final_candidate` 保存最终路径和结果；首轮写入/读取、持久化及重启证据仍保留，未冒称在最终包重复整轮检查。UI、ZIP 解压运行与安装行为由对应交付检查另行记录。

## 清理

两轮均使用 `temp/release-package-quality-20261111/`，每轮结束立即清理：workspace、应用数据、THINKFLOW_HOME、用户目录映射与 TEMP/TMP 均隔离。服务通过 shutdown 关闭终端后退出，HTTP server 关闭、线程退出；各轮专属目录核对归属后送入回收站。结构化证据与本报告保留在 docs；未触碰真实桌面实例、剪贴板、用户配置或凭据。
