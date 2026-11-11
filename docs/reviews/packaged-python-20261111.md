# 桌面 Python 工具运行时修复 · 2026-11-11

## 问题与根因

P1：0.9.0 Windows 桌面包在没有外部 Python 的环境执行 `tf-python`，返回 `WinError 2`。正式 frozen backend 使用裁剪后的 PATH（仅 Windows/System32 与 Windows）、移除 `THINKFLOW_PYTHON` / `PYTHONHOME` / `PYTHONPATH` 后已复现。证据：[0.9.0 基线](../evidence/packaged-python-before-20261111.json)。

原实现始终启动 `THINKFLOW_PYTHON` 或裸 `python`。PyInstaller 后端 EXE 只运行服务入口，不能充当独立的 Python 解释器；“桌面自带运行时”的交付缺少工具执行所需的解释器。

## 修复

- `scripts/desktop/python-runtime.cjs` 获取官方 CPython 3.12.10 Windows x64 embeddable ZIP，并核对固定 SHA-256。完整解包，携带标准库 zip、原生扩展、DLL、解释器、许可证与原始 `._pth`；不从后端已使用的 import 猜测工具所需模块。
- `scripts/build-desktop.cjs` 将运行时放入后端的 `python/`。现有 OpenConsole.exe 构建项保留；便携 ZIP 与安装程序都从同一个完整应用目录生成。
- `src/executor.py` 优先使用显式 `THINKFLOW_PYTHON`；冻结桌面默认使用后端旁的 `python/python.exe`；源码/CLI 默认继续调用系统 `python`。
- `scripts/desktop/python_stdin.py` 只适配 bundled stdin 执行：保留 `._pth` 隔离，显式加入当前工作区导入路径，创建独立 `__main__` 模块，保持 `sys.argv=['-']` 与 `<stdin>` 文件名。使用 `-X utf8`，避免隔离模式忽略 UTF-8 环境变量。代码仍经独立子进程、原执行器 cwd/env、输出捕获与取消链路运行。

## 依赖来源与保留输入

本机构建解释器为 `C:/Users/andy1/AppData/Local/Programs/Python/Python312/python.exe`，版本 3.12.10 x64。检查项目产物、uv 缓存及 Downloads 后，没有可复用的官方 embeddable ZIP。

- [官方 3.12.10 发行页](https://www.python.org/downloads/release/python-31210/)
- [官方 amd64 ZIP](https://www.python.org/ftp/python/3.12.10/python-3.12.10-embed-amd64.zip)
- [官方签名资料](https://www.python.org/ftp/python/3.12.10/python-3.12.10-embed-amd64.zip.sigstore)：`messageSignature.messageDigest` 中 SHA-256 为 `4acbed6dd1c744b0376e3b1cf57ce906f9dc9e95e68824584c8099a63025a3c3`。
- [官方 embeddable 与 UTF-8 说明](https://docs.python.org/3.12/using/windows.html#the-embeddable-package)：发行包提供编译后的标准库；不含 pip、Tk 或 Python 文档。项目第三方依赖通过显式解释器选择。

缓存保留于 `artifacts/build-inputs/python-3.12.10-embed-amd64.zip`，供后续构建与离线构建复用；不进入 Git。运行时为应用局部依赖，不修改系统或用户 PATH。

## 验证

`tests/test_python_runtime.py` 的常规用例检查源码/冻结/显式覆盖优先级。设置 `THINKFLOW_TEST_PYTHON_RUNTIME` 为构建后的 `resources/backend/python` 可对实际解释器执行其余用例。

- 解释器选择的四种组合：通过。
- `tests/test_efficiency_runtime.py`：25 项通过，覆盖原有 Python 工具及流式批量执行行为。
- 构建脚本 `node --check`：通过。
- 实际 embeddable：通过正式构建 helper 从缓存解包，再在裁剪 PATH 环境运行 `tests/test_python_runtime.py -v`，5 项全部通过（2.046 秒）。覆盖中文 stdin/输出、csv/sqlite3/statistics/pathlib/ssl/hashlib/bz2/lzma/ctypes、工作区模块导入与 chdir 后导入、`__future__`/`__main__`、真实独立进程、显式外部解释器覆盖、退出码 7/traceback、完整输出与预览截断、取消后的输出留存及停止延迟写入。
- 运行时原始 `python312._pth` 保持 `python312.zip`、`.` 与注释掉的 `import site`，实跑确认 `isolated=1`、`utf8_mode=1`。
- 下载缓存大小 11,133,606 字节，SHA-256 与官方签名资料中的 digest 一致。证据：[定向检查结果](../evidence/packaged-python-runtime-20261111.json)。
- 最终发行包由主会话统一构建，独立质检将亲验 frozen backend 的无系统 Python 场景。

本轮临时目录 `temp/packaged-python-20261111/` 已核对只包含下载签名资料和独立验证的解包 runtime；没有该目录中的存活进程。检查后回收整个临时目录，仅保留正式构建输入 ZIP 与归档证据。

修复不触及 session journal、用户真实配置、模型调用或现有 0.9.0 发行产物。
