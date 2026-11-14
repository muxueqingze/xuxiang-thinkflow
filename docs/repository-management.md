# 仓库管理

日常开发、首页展示与发布来源统一使用 `main`。维护者直接提交主分支；其他开发分支由用户另行指定。外部贡献通过 fork 和 PR，由维护者决定合并。

## 已启用的保护

| 对象 | 规则 | 正常工作方式 |
|---|---|---|
| `main` | [禁止删除与非快进推送](https://github.com/muxueqingze/xuxiang-thinkflow/rules/27248681)，无绕过名单 | 保留正常提交、快进推送及PR合并；不要求维护者先创建开发分支 |
| `v*`版本标签 | [禁止改写和删除已有标签](https://github.com/muxueqingze/xuxiang-thinkflow/rules/27248684)，无绕过名单 | 继续创建新版本标签，修订内容使用新版本 |
| Actions | 默认只读令牌，不允许工作流批准PR；checkout不保留凭据 | push与PR运行现有CI；首次外部贡献运行工作流需维护者批准 |
| 凭据 | GitHub密钥扫描与推送保护 | 发布内容仍按改动范围核对 |
| 漏洞报告 | [GitHub私密报告](https://github.com/muxueqingze/xuxiang-thinkflow/security/advisories/new) | 普通问题继续使用Issues |

目前唯一有仓库写入权限的账号是 `muxueqingze`。[CODEOWNERS](../.github/CODEOWNERS)将PR审查请求指向维护者；它负责分派审查，不额外要求第二位维护者批准。CI状态应在合并外部贡献或发布前核对。

## 发布与协作入口

- 安装与使用：[README](../README.md)。npm `latest` 是默认安装通道；推广当前版本时同步默认通道与安装说明。
- 源码与发行版本：`main`负责当前源码；已发布版本通过tag与GitHub Releases保留。
- 贡献：[CONTRIBUTING](../CONTRIBUTING.md)；版本和私密报告范围：[SECURITY](../SECURITY.md)。
- 提交作者使用实际维护者的Git身份。身份修正应用于后续提交，历史作者与发布标签保持原记录。

参考了[OpenCode的维护者分派](https://github.com/anomalyco/opencode/blob/5bf661138b1524fdbfafa82fd8cde1918cf05f73/.github/CODEOWNERS)、[Pi的问题复现字段](https://github.com/earendil-works/pi/blob/47e9d74f9186de37e700693fac0f6e56a499657a/.github/ISSUE_TEMPLATE/bug.yml)与[GitHub规则说明](https://docs.github.com/en/repositories/configuring-branches-and-merges-in-your-repository/managing-rulesets/available-rules-for-rulesets)。使用与当前维护规模相配的规则，保留直接开发与开放反馈入口。
