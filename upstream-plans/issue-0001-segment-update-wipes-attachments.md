# 0001 · 更新分段不带 `attachment_ids` 会静默删除该分段的图片附件

- **状态**：🟡 方案定稿，待提 issue + PR
- **类型**：bug（数据静默损坏）
- **影响版本**：1.16.1 实测（自托管）；`main` 上同段代码存在
- **上游 issue**：（待填）
- **PR**：拟分支 `fix-segment-attachment-ids-preserve`
- **复现环境**：自托管 1.16.1，容器 `docker-api-1` / `docker-db_postgres-1`，`chunk_structure=hierarchical_model` 的知识库

## 1. 现象

只改分段文字（或只改摘要）的 API 调用，会把该分段**所有图片附件绑定删掉**：

- 分段正文里的 markdown 图 `![图1](/files/tools/<id>.jpg)` 仍在，正文渲染正常
- 但分段卡片正文**下方那一排图片缩略图消失**（前端 `segment-card/index.tsx:272` 的 `<ImageList images={attachments}/>`，`attachments` 来自 `segment_attachment_bindings` JOIN `upload_files`）
- 检索带图（`#context_files#`）随之失效
- **接口返回 200，无任何警告**；DB 里只留下孤儿 `upload_files` 行

实测破坏面（本项目一个知识库）：76 份文档 / 1699 个分段被清空 / 3471 处引用全部丢失。

## 2. 根因（代码级）

| 位置 | 行为 |
|---|---|
| `api/services/dataset_service.py:3805` | `SegmentService.update_segment()` 的 `try` 体**收尾处无条件**调用 `VectorService.update_multimodel_vector(segment, args.attachment_ids or [], dataset, session=session)` —— `None` 被 `or []` 折叠成"空集合"，无法区分「没传」与「明确清空」 |
| `api/services/vector_service.py:295-312` | 取旧绑定集合 → `if set(new) == set(old): return` → 否则 `delete(SegmentAttachmentBinding).where(segment_id == segment.id)`；新集合为空时**删完直接 return，不重建** |
| `api/services/entities/knowledge_entities/knowledge_entities.py:252-262` | `SegmentUpdateArgs.attachment_ids: list[str] | None = None`（默认 `None` 就是这个"缺省即清空"的语义） |
| `api/controllers/service_api/dataset/segment.py:79-80` | service API 直接把 body 的 `segment` 反序列化成 `SegmentUpdateArgs` |
| `api/controllers/console/datasets/datasets_segments.py:100-106`、`:534-540` | console 侧 `SegmentUpdatePayload.model_dump(exclude_none=True)` → 未传 `attachment_ids` 时同样落成 `None` |

两个反直觉点（实测）：

1. **正文一字不改也照删**。`args.content == segment.content` 与"内容有变"两条分支共用同一段收尾代码，只要旧绑定非空、新集合为空，就删。
2. **只写 `summary` 也照删**（`{"segment": {"summary": "..."}}` 就是一次完整 update）。

### 前端为什么没暴露这个问题

`web/app/components/datasets/documents/detail/completed/segment-detail.tsx:54-56` 会把 `segInfo.attachments` 预填进 state，保存时 `use-segment-list-data.ts:287` 原样回传 `attachment_ids` ⇒ **UI 编辑是安全的**，只有 API / SDK / 脚本路径踩坑。这也说明"缺省即清空"不是有意设计，只是边界没处理。

## 3. 最小复现（隔离库，5 分钟）

```bash
# 1) 建临时库（必须显式指定 embedding，否则默认模型禁用会 400）
POST /v1/datasets
  {"name":"tmp","indexing_technique":"high_quality","embedding_model":"bge-m3",
   "embedding_model_provider":"xinference","permission":"only_me"}

# 2) 建文档：markdown 图片指向一个真实存在的 tool_file（必须真 markdown；HTML <img> 永远拿不到绑定）
POST /v1/datasets/{ds}/document/create-by-text
  {"name":"t.txt","doc_form":"hierarchical_model","indexing_technique":"high_quality",
   "process_rule":{"mode":"custom","rules":{
      "parent_mode":"paragraph",
      "segmentation":{"separator":"\n\n","max_tokens":800,"chunk_overlap":0},
      "subchunk_segmentation":{"separator":"\n\n","max_tokens":200,"chunk_overlap":0}}},
   "text":"### t\n\n![图1](/files/tools/<tool_file_id>.jpg)\n\n正文。\n"}

# 3) GET .../segments  → 该段 attachments = 1        ✅
# 4) 只改正文、不带 attachment_ids：
POST /v1/datasets/{ds}/documents/{doc}/segments/{seg}
  {"segment": {"content": "<同一段正文>（改一点）", "regenerate_child_chunks": false}}
# 5) GET .../segments  → 该段 attachments = 0        ❌（正文里的图还在）
#    —— 传「内容完全相同」的 content 同样会删
# 6) DELETE /v1/datasets/{ds}  清场
```

本项目现成脚本：`tmp/multimodal-binding-test/test_binding_lifecycle.py`（`create` / `edit` / `delete` 三个子命令，纯标准库）。

## 4. 影响面

- 命中：任何不带 `attachment_ids` 的 segment update —— 包括**只改 summary**、**改成完全相同的内容**。
- 不命中：UI 编辑（前端会回传当前附件）；带全量 `attachment_ids` 的更新（`set 相等 → 早退`，无副作用）。
- 静默：200 + 无日志（只在 `vector_service` 里对"找不到的 upload_file"打 warning，删绑定本身不打）。
- 只有 multipart 类型的分段有附件才会丢；纯文本分段无感 ⇒ 容易被忽略。

**存量损伤检测 SQL**

```sql
-- 正文里有图、但一个绑定都没有的分段
SELECT count(*) FROM document_segments s
WHERE s.dataset_id = '<ds>'
  AND s.content LIKE '%![%'
  AND NOT EXISTS (SELECT 1 FROM segment_attachment_bindings b WHERE b.segment_id = s.id);

-- 孤儿 upload_files（被删绑定留下的字节）
SELECT count(*) FROM upload_files u
WHERE NOT EXISTS (SELECT 1 FROM segment_attachment_bindings b WHERE b.attachment_id = u.id);
```

**恢复**：`tool_files` 与 `upload_files` 行都还在，可按 `(name,size,mime_type)` 把正文里的 `/files/tools/{id}` 映射回既有 `upload_files` 并重建绑定（实测 3468/3468 命中、sha256 5/5 一致，零新增字节）；或对文档重新索引。

## 5. 修复方案

### 5.1 必须改（后端语义：缺省 ≠ 清空）

```python
# api/services/dataset_service.py（update_segment 收尾）
# before
VectorService.update_multimodel_vector(segment, args.attachment_ids or [], dataset, session=session)
# after
if args.attachment_ids is not None:
    VectorService.update_multimodel_vector(segment, args.attachment_ids, dataset, session=session)
```

配套：`VectorService.update_multimodel_vector` 的签名改成 `attachment_ids: list[str]`（不再接受 `None`），内部那句 `or []` 去掉，避免同一语义在两层都有。

### 5.2 可选/顺带

- console / service API 的 Pydantic 模型用 `model_fields_set` 区分「显式传了 `attachment_ids: []`」与「完全没传」——若 `exclude_none=True` 之外还要区分，建议显式 `list[str] | None = None` + `exclude_unset=True`。当前前端始终显式传，可不做。
- service API 文档补一句：`attachment_ids` 省略 = 不改动附件；传 `[]` = 清空附件。

### 5.3 兼容性与回归风险

- 行为变化只影响"以前靠省略 `attachment_ids` 来清空附件"的调用方 —— 这类调用方实际拿到的是"静默删图"这个 bug，改成显式传 `[]` 即可，属于修 bug 而不是破坏兼容。
- UI 路径前后完全等价（前端一直显式传）。
- 现有单测 `api/tests/unit_tests/services/test_dataset_service_segment.py` 里有 7 处 `assert_called_once_with(segment, [], dataset, ...)`，以及 `test_vector_service.py` 的 `test_update_multimodel_vector_deletes_bindings_and_commits_on_empty_new_ids` —— **这些断言固化了 bug 行为，需要一起改**（改为只在显式传空时删）。这是本次 PR 的主要测试工作量。

## 6. 验证清单

- [ ] 单测：`SegmentUpdateArgs()`（不含 `attachment_ids`）→ `update_multimodel_vector` **不被调用**
- [ ] 单测：`attachment_ids=[]` → 仍会清空
- [ ] 单测：`attachment_ids=[当前 ids]` → 早退、无副作用
- [ ] 集成：只改 `summary` 的分段更新，`attachments` 保持
- [ ] 集成：改 `content` 且带 `attachment_ids`，`attachments` 保持
- [ ] 前端回归：编辑分段并保存，缩略图仍在
- [ ] 既有 7 处 `test_dataset_service_segment.py` 断言更新后全绿

## 7. 上游 issue 文案（英文草稿）

```
### Self Checks
- [x] I have searched for existing issues
- [x] I have read the contributing guide
- [x] This is a bug, not a usage question
- [x] I can reproduce this with the latest release (1.16.1)
- [x] I have included the version and environment below

### Which component?
API / knowledge base (dataset segments)

### Version
1.16.1 (self-hosted, docker compose)

### Bug description
Updating a chunk without the `attachment_ids` field silently deletes **all** of its
multimodal attachments. The chunk content (and therefore the inline markdown image)
stays intact, but `segment_attachment_bindings` rows are removed, so the image
thumbnails under the chunk disappear and retrieval stops returning images
(`#context_files#` becomes empty even with `vision.enabled = true`).

The endpoint returns 200 with no warning.

### Steps to reproduce
1. Create a knowledge base with `indexing_technique=high_quality` and
   `chunk_structure=hierarchical_model`.
2. Create a document (create-by-text is enough) whose chunk content contains
   `![img](/files/tools/<tool_file_id>.jpg)`.
3. `GET /v1/datasets/{ds}/documents/{doc}/segments` → the chunk has 1 attachment.
4. `POST /v1/datasets/{ds}/documents/{doc}/segments/{seg}` with
   `{"segment": {"content": "<same-or-slightly-changed content>", "regenerate_child_chunks": false}}`
   (note: **no** `attachment_ids` key).
5. `GET` again → `attachments` is now `[]` while the content is unchanged.
   Passing a completely identical `content` string deletes them as well.

### Root cause
`SegmentService.update_segment` unconditionally calls
`VectorService.update_multimodel_vector(segment, args.attachment_ids or [], ...)`
at the end of its `try` block (`api/services/dataset_service.py:3805`), and
`update_multimodel_vector` deletes every binding of the segment whenever the new
set differs from the old one, returning early when the new set is empty
(`api/services/vector_service.py:295-312`). Because `None` is collapsed into `[]`
by `or []`, "field not provided" is indistinguishable from "explicitly clear".

### Expected behavior
- Field omitted → leave attachments untouched (the current UI always sends the
  current list, so nothing changes for the UI path).
- Explicit `attachment_ids: []` → clear attachments.
- Explicit same list → short-circuit (already the case).

### Suggested fix
```python
if args.attachment_ids is not None:
    VectorService.update_multimodel_vector(segment, args.attachment_ids, dataset, session=session)
```
(plus dropping `or []` inside `update_multimodel_vector` and updating the unit
tests that currently assert the destructive behavior)

### Impact we hit
In our instance, a batch script that only rewrote chunk headers (no
`attachment_ids`) wiped 1699 chunks across 76 documents; the `upload_files` rows
remained as orphans. Happy to open a PR with the fix + tests.
```

## 8. PR 文案

- **分支**：`fix-segment-attachment-ids-preserve`（单段名，从 `origin/main` 的 worktree 拉）
- **标题**：`fix: do not clear chunk attachments when attachment_ids is omitted`
- **改动**：
  - `api/services/dataset_service.py`：`if args.attachment_ids is not None:`
  - `api/services/vector_service.py`：签名去掉 `| None` / 去掉 `or []`
  - `api/tests/unit_tests/services/test_dataset_service_segment.py`：7 处断言改为"未传时不调用"
  - `api/tests/unit_tests/services/test_vector_service.py`：`deletes_bindings_on_empty_new_ids` 改为"显式空列表才删"，新增"未传不改动"用例
- **PR 描述**：引用 issue、贴复现与前后行为对照、说明 UI 路径不受影响、列出验证清单
- **注意**：approve / 进 merge queue 后**不要再 push**
