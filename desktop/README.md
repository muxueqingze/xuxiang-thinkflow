# 续想桌面应用

Electron 只负责窗口、系统对话框与加密设置。Python 通过私有 stdin/stdout NDJSON 驱动同一个 ThinkFlow 内核；应用没有 HTTP 监听端口。

开发启动（仓库已安装 Python 依赖）：

```powershell
cd desktop
npm ci
npm start
```

多个 Python 版本时，启动前设置 `THINKFLOW_PYTHON` 为所需解释器的完整路径。开发模式从仓库目录运行 `python -u -m src.desktop_service`。打包模式运行 `resources/backend/thinkflow-service.exe`，不依赖系统 Python。`npm run package` 复用仓库 `scripts/build-desktop.cjs` 构建完整 Windows 文件夹。

首次打开选择工作区，并填写兼容端点与模型。密钥使用 Electron safeStorage 的 Windows 系统加密，保存到当前用户应用目录的 `settings.json`；留空保留已有密钥，勾选清除才删除。界面只能获取是否保存密钥。会话按工作区隔离存储。

v0.8 将任务接收和待发队列移至后端，支持精确回执查询、停止/重启恢复、文件预览与引用、差异与版本检查回退、任务验收清单和模型连接诊断。会话搜索/整理、最近工作区、草稿、Enter发送与Ctrl+K沿用。详情与边界见 [桌面指南](../docs/desktop.md)。

权限策略是应用工具规则，不是操作系统沙箱。界面展示逐次授权参数和账本；异常中断留下不确定操作时，须核对实际文件并确认恢复，旧操作不会自动重放。

验证：

```powershell
npm test
npm run test:smoke
npm run test:ux
npm run test:workbench
node tests/electron-workbench.cjs --packaged
```

smoke 测试用真实 Electron 与 Python 服务，连接测试启动的本地 SSE 端点，不调用远程模型。工作区、加密测试配置、截图放在 `desktop/temp/smoke-*`，与真实用户数据隔离。Playwright 只在测试运行期间启用自动化连接，没有常驻远程调试端口。

入口：`main.cjs`（来源校验/方法白名单/对话框），`preload.cjs`（窄 API），`backend.cjs`（NDJSON），`settings.cjs`（校验/原子保存/密钥加密），`navigation.cjs`（登记目录与恢复），`renderer/`（中文界面与安全文本渲染）。
