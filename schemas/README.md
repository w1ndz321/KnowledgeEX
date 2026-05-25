# 知识抽取输出与状态库约定

## 流程

```text
PDF
  -> convert: PDF 指纹去重、带页码 Markdown、转换文本指纹去重
  -> preprocess: 元数据修正和学科分类
  -> extract: 两组 anchor 抽取、程序定位并扩展 evidence
```

`convert` 保留解析到的图题和 Markdown 表格，并在每页前写入 `<!-- PAGE N -->`。`extract` 只让 LLM 输出短的逐字 `anchor_text`，随后由程序匹配回同一 Markdown，补回页码和更长的 `original_text`。正文证据保存 anchor 覆盖的完整句子及前后各最多 5 句，最多 5000 字符；表格证据仅扩展为 caption、表头、命中行和邻近解释，不把整张表的无关数值复制到每条知识中。

## 文档身份

| 字段 | 含义 |
|---|---|
| `metadata.doc_id` | `doc_<source_pdf_sha256_96>`，由程序生成，绑定原始 PDF 字节 |
| `metadata.source_file` | 相对于配置 `PDF_DIR` 的源文件路径，例如 `biology/model.pdf` |
| `metadata.source_pdf_sha256_96` | 原始 PDF 的 SHA-256 前 24 个十六进制字符（96 bit） |
| `metadata.converted_text_sha256_96` | 清洗后 Markdown 文本的 SHA-256 前 24 个十六进制字符（96 bit） |

SHA-256 仍完整计算，只持久化 96 bit 前缀用于索引和 ID。对于约 8000 万篇的非对抗语料，96 bit 的随机碰撞风险可忽略；64 bit 不适合作为这个规模的唯一身份字段。

`doc_id` 不由标题、文件名、DOI 或 LLM 生成。标题修正和文件重命名不会改变同一 PDF 的 `doc_id`。`doi` 已保存在元数据中，不另建重复的 `paper_id` 字段。

## Entry 外壳

所有知识类型使用相同结构：

```json
{
  "entry_id": "doc_51050ced82df75c143b15126_method_0001",
  "type": "method",
  "payload": {
    "name": "Example Method",
    "method_type": "model"
  },
  "evidence": {
    "section": "Methods",
    "page": 3,
    "anchor_text": "逐字复制的短原文锚点。",
    "original_text": "程序定位后扩展的、供下游二次处理的原文上下文。",
    "match_method": "exact"
  },
  "confidence": 0.95
}
```

`entry_id` 由程序在最终去重后按 `doc_id + type + 序号` 生成，例如 `doc_..._concept_0001`。LLM 不输出任何 ID、页码或匹配状态。内部会用不写入 JSON 的比较键合并同一类型、同一 payload 和同一 anchor 的重复条目。

`entry_id` 的作用是当前抽取结果内的引用标识，尤其用于 relation 链接 concept；它不承诺在重新抽取后稳定不变。当条目增删或排序变化时，同类型后续序号可能改变。跨结果溯源应依赖稳定的 `doc_id` 与 `evidence`。

### Evidence

| 字段 | 生成方 | 说明 |
|---|---|---|
| `section` | LLM | 锚点所在章节名，可为空 |
| `anchor_text` | LLM | 输入中逐字复制的短连续锚点，保留在最终 JSON 中供审计 |
| `page` | 程序 | 命中页码；无法匹配为 `null` |
| `original_text` | 程序 | 从源 Markdown 扩展的较长上下文 |
| `match_method` | 程序 | `exact`、`unique_fragment` 或 `unmatched` |

`exact` 是经过排版归一化后的完整锚点字面匹配。`unique_fragment` 只在至少 80 字符的连续片段在全文唯一出现时使用。程序不使用语义 fuzzy match 强行绑定改写过的证据。

正文 `original_text` 的扩展规则是：定位 anchor 所覆盖的句子，追加其前 5 句和后 5 句；遇到文首、文末或 5000 字符上限时提前停止。句子切分会保护小数和常见论文缩写（如 `e.g.`、`Fig.`、`et al.`）。表格仍按 caption、表头、命中行和紧邻解释文本处理，不套用句子窗口。

## Payload 字段

| `type` | `payload` 字段 |
|---|---|
| `concept` | `term`, `normalized`, `std_label` |
| `relation` | `head_entry_id`, `tail_entry_id`, `head_term`, `tail_term`, `relation_type`, `relation_surface` |
| `dataset` | `name`, `modality`, `domain` |
| `data_specification` | `spec_type`, `description` |
| `method` | `name`, `method_type` |
| `experiment` | `task`, `setup` |
| `quantitative_result` | `quantity`, `value`, `unit`, `context`, `result_type` |
| `performance_result` | `metric`, `compared_to` |
| `claim` | 空对象 `{}`，语义由 evidence 表达 |
| `conclusion` | 空对象 `{}`，语义由 evidence 表达 |
| `limitation` | 空对象 `{}`，语义由 evidence 表达 |
| `future_work` | 空对象 `{}`，语义由 evidence 表达 |

`relation.head_entry_id` 和 `tail_entry_id` 由程序用规范化 term/缩写精确链接到本 JSON 中的 `concept.entry_id`。无法精确链接时字段为 `null`，但保留原文 term 和 evidence，供下游继续处理。

## 状态库

`db/<source>.db` 的 `papers` 表保存阶段状态、指纹、消耗统计和跳过审计信息。与去重及溯源直接相关的字段如下：

| 字段 | 说明 |
|---|---|
| `source_key` | 任务主键，为相对于配置 `PDF_DIR` 的 PDF 路径，例如 `biology/model.pdf` |
| `doc_id` | PDF 指纹生成的文档 ID |
| `source_pdf_sha256_96` | convert 前计算的原 PDF 指纹 |
| `converted_text_sha256_96` | convert 后清洗 Markdown 指纹 |
| `duplicate_of` | 被判定重复时所对应的保留任务 `source_key` |
| `skip_code` | 结构化跳过原因 |
| `skip_detail` | 可读的具体解释 |
| `skip_reason` | 兼容旧查询的组合文本，格式为 `<skip_code>: <skip_detail>` |

### 跳过原因

| `skip_code` | 发生阶段 | 含义 | 是否已耗费转换时间 |
|---|---|---|---|
| `duplicate_pdf` | convert 前 | 原始 PDF 指纹已存在；该文件不再解析 | 否 |
| `duplicate_markdown` | convert 后 | PDF 不同，但清洗后 Markdown 文本与已保留文档相同；不进入 LLM 阶段 | 是 |
| `insufficient_text` | convert 后 | 解析出的文本少于 `MIN_MD_CHARS`，通常表示扫描件或解析质量不足 | 是 |

状态库为两个指纹字段和 `skip_code` 建立索引，避免去重检查随着语料增长退化为整表扫描。
状态约定：`duplicate_pdf` 记录为 `convert/preprocess/extract=skipped`；`duplicate_markdown` 和 `insufficient_text` 因为 Markdown 已经生成，记录为 `convert=done, preprocess/extract=skipped`。
单独执行下游阶段的 `--force` 不会清除 convert 判定的跳过记录；需要重新判定被排除文件时，应从 `convert --force` 开始。
升级自旧状态库时，`convert` 会仅为缺少新指纹的历史 `done` 记录补算一次 PDF 指纹和已有 Markdown 指纹，使之后新增的重复文件仍能在正确阶段被拦截。
被判为 `duplicate_pdf` 或 `duplicate_markdown` 的任务会移除其已生成的重复 Markdown 以及旧 JSON/debug 产物；`insufficient_text` 保留 Markdown 供解析质量诊断，但移除旧抽取 JSON/debug，避免下游误用已跳过结果。

状态库打开旧版本 `stem` 主键表时会自动迁移为 `source_key`：旧的平铺任务 `ace` 会成为 `ace.pdf`。新的嵌套输入会分别记录为 `biology/model.pdf` 和 `chemistry/model.pdf`，不会因文件名相同而共享状态或产物。`--source` 仅限制本次处理范围，不改变同一文件的 `source_key` 或状态库。

## 运行与迁移

完整流程：

```bash
conda run --no-capture-output -n myagent python run.py all --force --debug
```

查看产物和 anchor 定位统计：

```bash
conda run --no-capture-output -n myagent python run.py inspect
conda run --no-capture-output -n myagent python run.py status
```

旧 JSON 使用平铺类型字段和旧 ID，不能与新格式增量混合。正式 JSON 要求 `doc_id` 来自原始 PDF 指纹，不能绕过 convert 从旧 Markdown 生成；首次切换到本契约时应从 `convert` 开始完整重跑。

处理单篇或嵌套路径下的单篇时，`--paper` 接受相对于 `PDF_DIR` 的路径，可省略 `.pdf`，例如 `--paper biology/model`。
