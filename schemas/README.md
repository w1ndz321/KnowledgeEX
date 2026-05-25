# 最终 JSON 输出契约

本文件说明 `json_output/<source_key>.json` 中交付给下游使用的内容。每篇通过
`convert` 检查并完成抽取的 PDF 对应一个 JSON 文件；被判为重复或解析文本不足的文件不产生可用抽取 JSON。

## 顶层结构

| 字段 | 类型 | 含义 |
|---|---|---|
| `metadata` | `object` | 论文身份、元数据、学科分类和抽取运行信息 |
| `entries` | `array<Entry>` | 从论文中抽取出的知识条目；每条都附带原文证据 |

```json
{
  "metadata": {
    "doc_id": "doc_51050ced82df75c143b15126",
    "source_file": "batch_0001/paper_a.pdf",
    "title": "Example Paper",
    "primary_discipline": {
      "level1": "计算机科学技术",
      "level2": "人工智能",
      "level3": "知识表示"
    }
  },
  "entries": [
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
        "anchor_text": "Example Method improves retrieval accuracy.",
        "original_text": "带上下文的原始论文文本。",
        "match_method": "exact"
      },
      "confidence": 0.95
    }
  ]
}
```

## Metadata

| 字段 | 类型 | 生成方式 | 含义 |
|---|---|---|---|
| `doc_id` | `string` | 程序生成 | 文档内容身份，格式为 `doc_<source_pdf_sha256_96>`，绑定原始 PDF 字节 |
| `source_file` | `string` | 程序生成 | 相对于配置 `PDF_DIR` 的 PDF 路径，例如 `batch_0001/paper_a.pdf` |
| `source_pdf_sha256_96` | `string` | 程序生成 | 原 PDF 的 SHA-256 前 24 个十六进制字符，用于 PDF 去重和 `doc_id` |
| `converted_text_sha256_96` | `string` | 程序生成 | 清洗后 Markdown 文本的 SHA-256 前 24 个字符，用于正文重复识别 |
| `title` | `string` | LLM 校验修正 | 本文完整标题 |
| `year` | `integer \| null` | LLM 校验修正 | 可由本文原文确认的发表、接受、提交或版权年份 |
| `doi` | `string \| null` | LLM 校验修正 | 仅属于本文的规范 DOI；无法确认时为 `null` |
| `abstract` | `string` | 程序解析 | 从 Markdown 识别出的摘要文本 |
| `introduction` | `string` | 程序解析 | 从 Markdown 识别出的引言文本 |
| `primary_discipline` | `Discipline` | LLM 输出 + 程序校验 | 论文主要研究归属；`level1` 必须来自预定义一级学科表 |
| `secondary_disciplines` | `array<Discipline> \| null` | LLM 输出 + 程序校验 | 确有实质交叉贡献时记录的辅助学科 |
| `keywords` | `array<string>` | LLM 输出或原文关键词 | 代表研究对象、方法或任务的关键词；原文未提供时 prompt 目标为 3-5 个英文术语 |
| `extraction_info` | `object \| null` | 程序生成 | extract 阶段的模型、时间、失败组和重试信息 |

### Discipline

| 字段 | 类型 | 含义 |
|---|---|---|
| `level1` | `string` | 固定一级学科，例如 `计算机科学技术`、`生物学`、`材料科学` |
| `level2` | `string \| null` | 二级方向，例如 `人工智能` |
| `level3` | `string \| null` | 更具体的研究方向，例如 `知识表示` |

### Extraction Info

| 字段 | 类型 | 含义 |
|---|---|---|
| `extraction_model` | `string` | 本次抽取使用的模型名称 |
| `extraction_timestamp` | `string` | 抽取完成时的 UTC 时间 |
| `extraction_method` | `string` | 当前为 `anchor_grouped` |
| `retry_groups` | `array<string> \| null` | 本次是否仅重试此前失败的抽取组 |
| `failed_groups` | `array<string> \| null` | 仍未成功完成的抽取组 |

## Entry 通用外壳

所有知识类型共享同一外层结构，区别仅在 `type` 和 `payload`。

| 字段 | 类型 | 生成方式 | 含义 |
|---|---|---|---|
| `entry_id` | `string` | 程序生成 | 当前 JSON 内的条目标识，格式为 `<doc_id>_<type>_<序号>` |
| `type` | `string` | LLM 输出 | 知识类型，见下方知识类型表 |
| `payload` | `object` | LLM 输出并校验 | 该知识类型专属的结构化字段 |
| `evidence` | `Evidence` | LLM 给锚点，程序回填定位 | 支撑本条知识的原文证据与匹配状态 |
| `confidence` | `number` | LLM 输出 | 该条抽取的置信度，取值范围 `0-1` |

`entry_id` 用于同一次抽取结果内部引用，尤其是 `relation` 链接 `concept`。重新抽取后条目顺序可能变化，因此跨版本追溯应使用稳定的 `doc_id` 和 `evidence`，不能依赖 `entry_id` 不变。

## Evidence

| 字段 | 类型 | 生成方式 | 含义 |
|---|---|---|---|
| `section` | `string` | LLM 输出 | 证据所在章节名称；无法识别时可为空 |
| `page` | `integer \| null` | 程序定位 | anchor 命中的 PDF 页码；无法定位时为 `null` |
| `anchor_text` | `string` | LLM 逐字复制 | 用于回到源 Markdown 定位的短原句、表格行或 caption |
| `original_text` | `string` | 程序扩展 | 提供给下游二次处理的较长原文上下文 |
| `match_method` | `string` | 程序判定 | anchor 匹配质量，见下表 |

| `match_method` | 含义 | 下游使用建议 |
|---|---|---|
| `exact` | 经排版归一化后，完整 anchor 在原文中直接命中 | 可作为高可信证据直接使用 |
| `unique_fragment` | 完整 anchor 未命中，但至少 80 字符的连续片段在全文唯一命中 | 可使用，必要时人工复核格式差异 |
| `unmatched` | 无法可靠定位回源文档 | 保留知识候选，但证据溯源时应排除或人工复核 |

正文 `original_text` 为命中句及其前后各最多 5 句，总长度最多 5000 字符。表格证据不扩展整张表，只保留相关 caption、表头、命中行和邻近解释文本，避免无关数值污染下游。

## 知识类型概览

| `type` | 表示什么 | 典型用途 | `payload` 字段 |
|---|---|---|---|
| `concept` | 理解论文所需的核心实体或概念 | 实体规范化、关系端点 | `term`, `normalized`, `std_label` |
| `relation` | 两个核心实体之间由论文证据支持的关系 | 构建知识图谱、推理链 | `head_entry_id`, `tail_entry_id`, `head_term`, `tail_term`, `relation_type`, `relation_surface` |
| `dataset` | 本文实际使用的数据集、benchmark 或研究样本 | 数据来源溯源、复用评估 | `name`, `modality`, `domain` |
| `data_specification` | 数据采集、生成、过滤、标注或质量标准 | 复现实验和数据治理 | `spec_type`, `description` |
| `method` | 本文提出或关键依赖的方法、模型、仪器或协议 | 方法复用、方法检索 | `name`, `method_type` |
| `experiment` | 支撑主要结论的关键实验或研究设计 | 实验复现、证据结构化 | `task`, `setup` |
| `quantitative_result` | 本文产生的关键数值结果 | 定量比较、结果证据检索 | `quantity`, `value`, `unit`, `context`, `result_type` |
| `performance_result` | 方法与基线、变体或标准方案的关键对比 | 性能比较、方案选择 | `metric`, `compared_to` |
| `claim` | 作者要论证的核心主张或假设 | 论点检索、证据链起点 | `{}` |
| `conclusion` | 经实验或分析得到的主要结论 | 结论聚合、证据问答 | `{}` |
| `limitation` | 作者明确承认的本文局限 | 风险评估、适用范围判断 | `{}` |
| `future_work` | 作者明确提出的后续方向 | 研究机会检索 | `{}` |

`claim`、`conclusion`、`limitation` 和 `future_work` 不额外复述语义到 `payload`，其完整内容直接保留在 `evidence` 中，以减少输出 token 并避免脱离原文的二次改写。

## Payload 字段字典

### 研究对象、数据与方法

| `type` | 字段 | 类型 | 含义 / 取值说明 |
|---|---|---|---|
| `concept` | `term` | `string` | 原文中出现的概念名称，优先取完整名称 |
| `concept` | `normalized` | `string` | 规范化名称，用于同义概念归并 |
| `concept` | `std_label` | `string` | 明确出现的缩写或标准标签，例如 `RAG` |
| `dataset` | `name` | `string` | 本文实际使用的数据集、benchmark 或研究样本名称 |
| `dataset` | `modality` | `string` | 数据模态，推荐值：`text`, `image`, `tabular`, `time_series`, `multimodal`, `other` |
| `dataset` | `domain` | `string` | 数据所属领域或任务场景 |
| `data_specification` | `spec_type` | `string` | 推荐值：`format_rule`, `quality_standard`, `env_requirement`, `metadata_standard` |
| `data_specification` | `description` | `string` | 影响数据复现或使用的标准/约束描述 |
| `method` | `name` | `string` | 方法、模型、协议、软件、仪器或关键处理步骤名称 |
| `method` | `method_type` | `string` | 推荐值：`model`, `algorithm`, `protocol`, `software`, `instrument`, `preprocessing`, `field_research`, `textual_analysis`, `survey`, `interview` |
| `experiment` | `task` | `string` | 实验试图验证的任务、问题或分析目标 |
| `experiment` | `setup` | `string` | 关键数据、条件、对比对象或实验设置的一句话描述 |

### 关系与结果

| `type` | 字段 | 类型 | 含义 / 取值说明 |
|---|---|---|---|
| `relation` | `head_entry_id` | `string \| null` | 程序链接到本 JSON 中头实体 `concept.entry_id`；无法精确链接时为 `null` |
| `relation` | `tail_entry_id` | `string \| null` | 程序链接到本 JSON 中尾实体 `concept.entry_id`；无法精确链接时为 `null` |
| `relation` | `head_term` | `string` | 原文中的头实体名称 |
| `relation` | `tail_term` | `string` | 原文中的尾实体名称 |
| `relation` | `relation_type` | `string` | 推荐关系值：`enhances`, `inhibits`, `causes`, `creates`, `influences`, `belongs_to`, `measures`, `uses`, `compares`, `precedes`, `derives` |
| `relation` | `relation_surface` | `string` | 原文表达该关系的短语 |
| `quantitative_result` | `quantity` | `string` | 被测量或报告的量，例如 accuracy、样本量、转化率 |
| `quantitative_result` | `value` | `number \| string \| null` | 原文数值、区间或关键多值表达，不强行拆分 |
| `quantitative_result` | `unit` | `string` | 单位或 `%`；无单位可为空 |
| `quantitative_result` | `context` | `string` | 该数值对应的实验、样本或条件背景 |
| `quantitative_result` | `result_type` | `string` | 推荐值：`main_result`, `baseline`, `ablation`, `measurement`, `threshold` |
| `performance_result` | `metric` | `string` | 比较所依据的指标名称 |
| `performance_result` | `compared_to` | `string` | 本文方法所比较的基线、消融变体或标准方案 |

## 设计约定

| 约定 | 目的 |
|---|---|
| LLM 仅输出 `payload`、短 `anchor_text` 和 `confidence` | 减少输出 token，并把证据定位交给可审计程序完成 |
| `doc_id`、`entry_id`、`page`、`match_method` 由程序生成 | 避免 LLM 伪造身份或页码 |
| 每条知识都保留 `original_text` | 让下游可以继续二次抽取、核验或重建更细结构 |
| 数值结果只选关键项，不穷举整张表 | 避免无意义数值污染知识库 |
| relation 端点允许未链接 | 保留有证据的关系信息，同时暴露自动链接的不确定性 |
