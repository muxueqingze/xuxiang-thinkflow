# 在 Windows 运行 ZCode 源码参考

固定官方源码提交 [b95ebaffeab641907b38652f45c1502bd762972d](https://github.com/zai-org/ZCode/tree/b95ebaffeab641907b38652f45c1502bd762972d)。参考版仅供 ThinkFlow 桌面交互对照。

1. 将完整源码放在与 ThinkFlow 同级的 `ZCode` 目录。全新 checkout 可从 ZCode 根目录按上游文档运行 `corepack pnpm bootstrap`，安装依赖并生成桌面运行资源。现有本机 checkout 已包含这些依赖和生成文件。
2. 首次使用新 checkout 时，从 ZCode 根目录用 `git apply` 应用本文旁的 `windows-reference.patch`。它为参考运行关闭 Windows 资源管理器集成、协议注册和遥测，并提前将 Electron 数据目录指向隔离配置。现有本机 checkout 已应用。
3. 从 ThinkFlow 根目录显式运行 `pwsh -NoProfile -File docs/research/zcode-reference/run-reference.ps1`。若 checkout 位于其他目录，在末尾加 `-Source <ZCode 绝对路径>`。脚本会显示该次独立样例目录，启动匿名假模型并执行 ZCode 的 `dev:runtime`。
4. 首次引导选「使用 API key」后选择「暂时跳过」，进入编程模式。选择「本地对照」模型即可走样例回复。提示它「用命令写一份本机测试用的小文件」可观察一次真实 Bash 审批。夹具只监听 `127.0.0.1:18764`，用固定占位值，只有主动运行参考脚本时才会启动。
5. 关闭 Electron 会结束本地夹具。保存所需证据后，将当次独立样例目录移入回收站。

五张实拍位于 [源码交互证据](../../evidence/zcode-reference-20261111)。对照结论见 [ZCode 交互研究](../zcode-interaction-20261111.md)。
