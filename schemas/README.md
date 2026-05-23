# 知识抽取系统 Schema

与 `extract.py` / `schema.py` / `prompts.py` 实际逻辑完全一致。

---

## 一、抽取流程

```
PDF → convert(MD) → preprocess(元数据+学科, 1次LLM) → extract(多组LLM, 组间串行/并行可配)
```

| 阶段 | 功能 | 并发 |
|------|------|------|
| convert | PDF → Markdown | 多进程，`CONVERT_WORKERS` |
| preprocess | 正则提取元数据 + LLM修正标题/DOI + 学科分类 | 多线程，`WORKERS` |
| extract | 按分组多次调 LLM，自由组合 12 种知识类型 | 多线程，`WORKERS`，组间串行/并行可配 |

---

## 二、Metadata 结构

每篇论文的 `metadata` 对象：

| 字段 | 必填 | 说明 |
|------|------|------|
| `doc_id` | 是 | MD5(title\|stem) 前16位 |
| `source_file` | 是 | 原始 MD 文件名 |
| `title` | 是 | LLM 修正后的标题 |
| `year` | 否 | LLM 修正后的出版年 |
| `doi` | 否 | LLM 修正后的 DOI |
| `abstract` | 否 | 正则提取的摘要 |
| `introduction` | 否 | 正则提取的引言 |
| `primary_discipline` | 是 | `{"level1": "...", "level2": "...", "level3": "..."}` |
| `secondary_disciplines` | 否 | `[{"level1": "...", "level2": "...", "level3": "..."}, ...]` |
| `keywords` | 是 | 关键词列表 |
| `extraction_info` | 是 | 见下 |

### extraction_info

| 字段 | 说明 |
|------|------|
| `extraction_model` | LLM 模型名称 |
| `extraction_timestamp` | ISO 8601 时间戳 |
| `extraction_method` | `"grouped"` |
| `retry_groups` | 增量重试的组名（首轮为 null） |
| `failed_groups` | 本轮失败的组名（全成功为 null） |

---

## 三、公共字段

每条知识条目（entry）都包含以下公共字段：

| 字段 | 必填 | 说明 |
|------|------|------|
| `type` | 是 | 知识类型，取值见下方 12 种 |
| `<type>_id` | 是 | 类型 ID，格式 `doc_xxx_?N`（见各类型定义） |
| `evidence` | 是 | `{"section": "...", "original_text": "原文摘录 8-10 句"}` |
| `confidence` | 是 | 置信度 0.0~1.0 |

### Evidence 规范

```json
{
  "section": "Abstract",
  "original_text": "原文直接摘录，8-10个完整句子，禁止改写..."
}
```

1. **忠实性** — `original_text` 必须是原文直接摘录，禁止改写
2. **完整性** — 至少 8-10 句完整段落，保留实验条件、数值、对比基准等全部细节
3. **关联性** — relation 的 evidence 必须明确体现 head→tail 语义关系
4. **防幻觉** — 论文缺乏实质学术内容时返回空 entries

---

## 四、知识类型定义（12 种，自由组合抽取）

### 1. concept — 关键概念、术语、实体

| 字段 | 必填 | 说明 |
|------|------|------|
| `concept_id` | 是 | `doc_xxx_cN` |
| `term` | 是 | 原文术语 |
| `normalized` | 是 | 规范化中文名 |
| `std_label` | 否 | 标准缩写 |

---

### 2. relation — 概念间语义关系

`head` / `tail` 通过多级匹配（精确→去括号→子串→去括号子串）关联为 concept_id。匹配不上则置空字符串，但 `head_term` / `tail_term` 始终保留原文 term。

| 字段 | 必填 | 说明 |
|------|------|------|
| `relation_id` | 是 | `doc_xxx_rN` |
| `head` | 是 | 起始概念 concept_id（匹配不上为 `""`） |
| `head_term` | 是 | 起始概念原文 term |
| `relation_type` | 是 | `enhances` / `inhibits` / `causes` / `creates` / `influences` / `belongs_to` / `measures` / `uses` / `compares` / `precedes` / `derives` |
| `relation_surface` | 是 | 原文中表达关系的短语 |
| `tail` | 是 | 目标概念 concept_id（匹配不上为 `""`） |
| `tail_term` | 是 | 目标概念原文 term |

---

### 3. dataset — 论文使用/产生的数据集

| 字段 | 必填 | 说明 |
|------|------|------|
| `dataset_id` | 是 | `doc_xxx_dN` |
| `name` | 是 | 数据集名称 |
| `modality` | 否 | `text` / `image` / `tabular` / `time_series` / `multimodal` / `other` |
| `domain` | 否 | 所属领域 |

---

### 4. data_specification — 数据格式规范、质量标准

| 字段 | 必填 | 说明 |
|------|------|------|
| `ds_id` | 是 | `doc_xxx_dsN` |
| `spec_type` | 是 | `format_rule` / `quality_standard` / `env_requirement` / `metadata_standard` |
| `description` | 否 | 一句话摘要 |

---

### 5. method — 方法、模型、算法、仪器

| 字段 | 必填 | 说明 |
|------|------|------|
| `method_id` | 是 | `doc_xxx_mN` |
| `name` | 是 | 方法/模型/仪器名称 |
| `method_type` | 是 | `model` / `algorithm` / `protocol` / `software` / `instrument` / `preprocessing` / `field_research` / `textual_analysis` / `survey` / `interview` |

---

### 6. experiment — 实验设置与流程

| 字段 | 必填 | 说明 |
|------|------|------|
| `experiment_id` | 是 | `doc_xxx_xN` |
| `task` | 是 | 实验任务名称 |
| `setup` | 否 | 实验条件摘要 |

---

### 7. quantitative_result — 科学度量与实验指标

排除年份、页数、编号等元信息。

| 字段 | 必填 | 说明 |
|------|------|------|
| `qr_id` | 是 | `doc_xxx_qrN` |
| `quantity` | 是 | 物理量/指标名称 |
| `value` | 否 | 数值 |
| `unit` | 否 | 单位 |
| `context` | 是 | 实验条件/上下文 |
| `result_type` | 是 | `main_result` / `baseline` / `ablation` / `measurement` / `threshold` |

---

### 8. performance_result — 性能对比/评价

| 字段 | 必填 | 说明 |
|------|------|------|
| `perf_id` | 是 | `doc_xxx_pN` |
| `metric` | 否 | 被比较的指标名称 |
| `compared_to` | 否 | 对比对象 |

---

### 9. claim — 核心主张/发现

仅需 `evidence`，无额外标注字段。

| 字段 | 必填 | 说明 |
|------|------|------|
| `claim_id` | 是 | `doc_xxx_caN` |

---

### 10. conclusion — 经实验验证的结论

仅需 `evidence`，无额外标注字段。

| 字段 | 必填 | 说明 |
|------|------|------|
| `conclusion_id` | 是 | `doc_xxx_clN` |

---

### 11. limitation — 方法局限、适用约束

仅需 `evidence`，无额外标注字段。

| 字段 | 必填 | 说明 |
|------|------|------|
| `limitation_id` | 是 | `doc_xxx_lmN` |

---

### 12. future_work — 未来研究方向

仅需 `evidence`，无额外标注字段。

| 字段 | 必填 | 说明 |
|------|------|------|
| `future_work_id` | 是 | `doc_xxx_fwN` |

---

## 五、分组配置（灵活组合）

`.env` 中 `EXTRACT_GROUPS` 和 `EXTRACT_GROUP_SPEC` 控制抽取哪些知识类型、如何分组：

### 方式一：预设分组（`EXTRACT_GROUPS`）

从以下快捷名中逗号分隔选取：

| 快捷名 | 对应类型 | 说明 |
|------|------|------|
| `concept` | concept | 概念独立抽取 |
| `relation` | relation | 关系独立抽取（不依赖概念列表） |
| `concept_relation` | concept + relation | 概念和关系合并抽取 |
| `dataset_spec` | dataset + data_specification | 数据资源 |
| `method_experiment` | method + experiment | 方法实验 |
| `quant_perf` | quantitative_result + performance_result | 量化结果 |
| `insight_outlook` | claim + conclusion + limitation + future_work | 结论展望 |
**示例**：
```bash
EXTRACT_GROUPS=concept,relation,dataset_spec,method_experiment,quant_perf,insight_outlook  # 推荐：7 组独立
EXTRACT_GROUPS=concept_relation,dataset_spec,method_experiment,quant_perf,insight_outlook   # 5 组
EXTRACT_GROUPS=concept_relation,method_experiment                                            # 只抽部分类型
# 留空 → 默认 5 组（G1~G5）
```

### 方式二：完全自定义（`EXTRACT_GROUP_SPEC`）

设置后覆盖 `EXTRACT_GROUPS`。`|` 分隔组，`+` 连接同组类型：

```bash
# 例：concept 独立、relation 独立、其余全合并
EXTRACT_GROUP_SPEC=concept|relation|dataset+method+experiment+quantitative_result+performance_result+conclusion+claim+future_work+limitation
```

### 组间并行

`EXTRACT_GROUP_PARALLEL`：`1`=串行，`0`=所有组并行，`N`=最多 N 组并发。
