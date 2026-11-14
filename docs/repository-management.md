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

## 反馈如何进入工作

Issue表单分别给缺陷、改进建议和使用提问添加`bug`、`enhancement`、`question`标签。其他现有分类继续保留，维护者按讨论内容调整；空白Issue也可提交。

| 标签 | 含义与下一步 |
|---|---|
| `needs info` | 缺少复现或关键使用信息；维护者指出需要补充哪一项，信息齐全后移除 |
| `confirmed` | 问题已复现，或改进的场景与范围已确认；这不表示功能已完成 |
| `help wanted` | 范围明确，欢迎外部协助；正文写清入口、预期结果和现有验证方式 |
| `good first issue` | 适合首次贡献的小任务，同时提供足够定位信息 |

PR由CODEOWNERS路由给维护者，结合相关CI、代码和实际行为审查。需要修改时在原PR继续；重复问题链接到已有讨论，解决后关联对应提交或版本再关闭。版本号、发布说明和分发由维护者统一处理，避免贡献者重复升版。

贡献来源、开发入口与按范围验证在[中文指南](../CONTRIBUTING.md)和[English guide](../CONTRIBUTING.en.md)维护。新增贡献沿用MIT并保留来源和署名；目前无需额外CLA或DCO流程。保持人工决定受理与合并，不启用新贡献者自动关闭、批量封禁或固定时间自动关闭Issue。

## 参照与取舍

参考[Cindy贡献指南](https://github.com/makecindy/cindy/blob/3a474759524e8bf804bda4ddf6d2f4c3188918e2/CONTRIBUTING.md)的入口分流、开发资料定位和实际验证说明，[Pi贡献指南](https://github.com/earendil-works/pi/blob/827383a15262905f82e418483825a49dbb1adbf5/CONTRIBUTING.md)的核心克制、简短提案和提交人理解实现，以及[OpenCode的维护者分派](https://github.com/anomalyco/opencode/blob/5bf661138b1524fdbfafa82fd8cde1918cf05f73/.github/CODEOWNERS)。Cindy的团队审批/DCO和Pi的新贡献者准入流程对应其各自组织；本仓库使用上面的直接主分支维护与开放反馈方式。[GitHub规则说明](https://docs.github.com/en/repositories/configuring-branches-and-merges-in-your-repository/managing-rulesets/available-rules-for-rulesets)说明了保护规则的实际作用。
