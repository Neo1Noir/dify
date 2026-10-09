# upstream-plans · 上游 issue / PR 方案池

本目录**只放**「要向 langgenius/dify 上游提的 issue 与 PR 方案」，每条一个文件，方案定稿后再统一提。
不存放本项目的业务资料（那些在 `微信客服接入/`、`fault_kb/`、`tmp/`）。

- fork：`git@github.com:Neo1Noir/dify.git`（本仓库根目录就是 fork）
- upstream：`git@github.com:langgenius/dify.git`
- 本目录是**本地约定目录**，与上游路径无重叠 ⇒ 从 `origin/main` 开 PR 分支时不会带上它（分支请从 `origin/main` 拉，不要从本地 main 拉）

## 索引

| 编号 | 标题 | 类型 | 状态 | 分支 / 链接 |
|---|---|---|---|---|
| 0001 | 更新分段不带 `attachment_ids` 会静默删除该分段的图片附件 | bug | 🟡 方案定稿，待提 | 拟 `fix-segment-attachment-ids-preserve` |
| 0002 | （占位）END_USER 身份下 `#context_files#` 恒为空（ContextVar 快照重放覆盖授权） | bug | 🔵 已提 issue [#42277](https://github.com/langgenius/dify/issues/42277) | 见本项目 skill `dify-multimodal-attachment-chain` |

## 提交约定（踩过的坑，别重复）

1. **分支一律单段名**（`fix-xxx`），不要 `fix/xxx` —— 沙箱环境下写二级 ref 会「假成功」。
2. 从 `origin/main` 开 worktree，不要污染本地 main。
3. `git config core.autocrlf false` + `.gitattributes eol=lf`，避免 CRLF 噪音。
4. 🔴 **approve / 进 merge queue 之后绝不能再 push**：会 dismiss approval 并掉出队列。要改就开新 PR。
5. lint 抑制是**数量基线**（多与少都会失败）；`Skip Duplicate Checks` 同 tree 会跳 job。
6. 每个方案文件要写到「**别人拿着它就能提**」的程度：现象、根因（文件:行）、最小复现、修法 diff、验证清单、上游文案草稿。

## 文件命名

`issue-<4 位序号>-<短横线英文摘要>.md`，序号只增不复用；模板见 `TEMPLATE.md`。
