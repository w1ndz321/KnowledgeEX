# KnowledgeEX Schema

与 `schema.py` / `extract.py` / `prompts.py` 保持同步。

---

## 一、Metadata 结构

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `doc_id` | str | 是 | `doc_{pdf_sha256_96}`，基于 PDF 内容哈希，跨批次唯一 |
| `source_pdf_sha256_96` | str | 是 | 源 PDF 的 96-bit SHA-256 |
| `converted_text_sha256_96` | str | 是 | 转换后 Markdown 的 96-bit SHA-256 |
| `source_file` | str | 是 | 相对于 PDF_DIR 的 PDF 路径 |
| `title` | str? | 是 | LLM 修正后的论文标题 |
| `year` | int? | 否 | 发表年份 |
| `doi` | str? | 否 | DOI（`10.` 开头，无 URL 前缀） |
| `abstract` | str | 否 | 正则提取的摘要 |
| `introduction` | str | 否 | 正则提取的引言 |
| `primary_discipline` | dict | 是 | `{"level1": "一级学科", "level2": "二级学科", "level3": "三级学科"}` |
| `secondary_disciplines` | list? | 否 | 辅助学科数组，结构同上 |
| `keywords` | list[str] | 是 | 3-5 个英文原文关键词 |
| `extraction_info` | dict? | 是 | 抽取信息，见下 |

### extraction_info

| 字段 | 说明 |
|------|------|
| `extraction_model` | LLM 模型名称 |
| `extraction_timestamp` | ISO 8601 时间戳 |
| `extraction_method` | `"grouped_anchor"` |
| `retry_groups` | 增量重试的组名（首轮为 null） |
| `failed_groups` | 本轮失败的组名（全成功为 null） |

---

## 二、Entry 结构（公共）

每条知识条目统一使用以下结构：

```json
{
  "entry_id": "doc_{pdf_hash}_{type}_{index}",
  "type": "concept",
  "payload": { ... },
  "evidence": { ... },
  "confidence": 0.95
}
```

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `entry_id` | str | 是 | 统一 ID，格式 `doc_{hash}_{type}_{index}` |
| `type` | str | 是 | 知识类型，12 种之一 |
| `payload` | dict | 是 | 类型专属字段（见下方） |
| `evidence` | dict | 是 | 原文证据（见下方） |
| `confidence` | float | 是 | 置信度 0.0~1.0 |

### Evidence 结构

```json
{
  "section": "Introduction",
  "page": 3,
  "anchor_text": "LLM 输出的短锚文本，用于在原文中定位",
  "original_text": "从带页码标记的 MD 中精确截取的 8-10 句原文",
  "match_method": "exact"
}
```

| 字段 | 说明 |
|------|------|
| `section` | 来源章节名 |
| `page` | 原文页码（从 `<!-- PAGE {n} -->` 标记提取） |
| `anchor_text` | LLM 输出的锚文本（~100 字），用于回原文定位 |
| `original_text` | 后处理从原文精确截取的完整段落（8-10 句） |
| `match_method` | 匹配方式：`exact` / `unique_fragment` / `unmatched` |

anchor 机制让 LLM 只输出短锚文本 + 页码范围，大幅降低 output token（~30 tokens vs 旧版 ~500 tokens），原文摘录由后处理精确截取，杜绝 LLM 改写原文的幻觉。

---

## 三、知识类型 Payload 定义（12 种）

### 1. concept — 关键概念、术语、实体

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `term` | str | 是 | 原文术语 |
| `normalized` | str | 是 | 规范化中文名 |
| `std_label` | str | 否 | 标准缩写 |

---

### 2. relation — 概念间语义关系

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `head_entry_id` | str? | 否 | 起始 concept 的 entry_id（自动链接） |
| `tail_entry_id` | str? | 否 | 目标 concept 的 entry_id（自动链接） |
| `head_term` | str | 是 | 起始概念原文术语 |
| `tail_term` | str | 是 | 目标概念原文术语 |
| `relation_type` | str | 是 | enhances / inhibits / causes / creates / influences / belongs_to / measures / uses / compares / precedes / derives |
| `relation_surface` | str | 是 | 原文中表达关系的短语 |

`head_entry_id` / `tail_entry_id` 由程序通过多级匹配自动链接到同篇 concept，链接不上则置 null。

---

### 3. dataset — 论文使用/产生的数据集

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `name` | str | 是 | 数据集名称 |
| `modality` | str | 否 | text / image / tabular / time_series / multimodal / other |
| `domain` | str | 否 | 所属领域 |

---

### 4. method — 方法、模型、算法、仪器

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `name` | str | 是 | 方法/模型名称 |
| `method_type` | str | 是 | model / algorithm / protocol / software / instrument / preprocessing / field_research / textual_analysis / survey / interview |

---

### 5. experiment — 研究设计、实验设置

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `task` | str | 是 | 实验任务名称 |
| `setup` | str | 否 | 实验条件摘要 |

---

### 6. quantitative_result — 科学度量与实验指标

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `quantity` | str | 是 | 物理量/指标名称 |
| `value` | float/str? | 否 | 数值（保留原文单值、区间或多值文本） |
| `unit` | str | 否 | 单位 |
| `context` | str | 是 | 实验条件/上下文 |
| `result_type` | str | 是 | main_result / baseline / ablation / measurement / threshold |

---

### 7. performance_result — 性能对比/评价

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `metric` | str | 否 | 被比较的指标名称 |
| `compared_to` | str | 否 | 对比对象 |

---

### 8. data_specification — 数据格式规范、质量标准

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `spec_type` | str | 是 | format_rule / quality_standard / env_requirement / metadata_standard |
| `description` | str | 否 | 一句话摘要 |

---

### 9-12. claim / conclusion / limitation / future_work

四种类型均**仅需 evidence 和 confidence**，payload 为空对象 `{}`。

| 类型 | entry_id 示例 |
|------|------|
| `claim` | `doc_{hash}_claim_0001` |
| `conclusion` | `doc_{hash}_conclusion_0001` |
| `limitation` | `doc_{hash}_limitation_0001` |
| `future_work` | `doc_{hash}_future_work_0001` |

---

## 四、evidence anchor 流程

```
PDF → convert 插入 <!-- PAGE {n} --> → Markdown
     → extract LLM 输出 anchor_text + 页码范围
     → 后处理从 Markdown 精确定位截取 8-10 句原文 → original_text
```

LLM 不再输出完整原文，只输出 ~100 字锚文本 + 页码。后处理 `_resolve_evidence_anchors()` 负责匹配截取。match_method 记录匹配质量（exact > unique_fragment > unmatched）。
