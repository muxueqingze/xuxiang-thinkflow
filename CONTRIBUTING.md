# 参与 ThinkFlow

问题反馈与功能建议可以直接发 [Issue](https://github.com/muxueqingze/xuxiang-thinkflow/issues)。说明使用版本、CLI或桌面、运行平台，以及能复现问题的简短步骤；日志只需相关片段。

涉及密钥泄露或可利用漏洞时，使用 [私密漏洞报告](https://github.com/muxueqingze/xuxiang-thinkflow/security/advisories/new)。

维护者直接在 `main` 开发。外部贡献请 fork 仓库，并向 `main` 提交 PR。PR写清解决什么问题、行为如何改变，以及实际做过的验证；界面改动附对应截图即可。

从 [代码地图](docs/代码地图.md) 查找实现。ThinkFlow重视连续可预测执行、独立信息合批与最小充分信息；修改应提高实际工程效果，沿用已有模块和契约。

验证按改动范围选择：Python入口是 `python tests/run_all.py`，桌面接口入口是 `npm --prefix desktop test`。具体交互或发布包使用对应已有脚本，不为小改动机械重跑全部实验。

许可证保持 [MIT](LICENSE)，不另收取贡献者许可协议。
