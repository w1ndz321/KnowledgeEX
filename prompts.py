"""
论文元数据分类与 anchor 知识抽取 prompt。

当前流程每篇论文调用一次 metadata prompt，并默认调用两次 anchor extraction prompt。
"""

import re


LEVEL1_DISCIPLINES = [
    "数学", "信息科学与系统科学", "力学", "物理学", "化学",
    "天文学", "地球科学", "生物学", "农学", "林学",
    "畜牧、兽医科学", "水产学", "基础医学", "临床医学", "预防医学与卫生学",
    "军事医学与特种医学", "药学", "中医学与中药学", "工程与技术科学基础学科", "测绘科学技术",
    "材料科学", "矿山工程技术", "冶金工程技术", "机械工程", "动力与电气工程",
    "能源科学技术", "核科学技术", "电子、通信与自动控制技术", "计算机科学技术", "化学工程",
    "纺织科学技术", "食品科学技术", "土木建筑工程", "水利工程", "交通运输工程",
    "航空、航天科学技术", "环境科学技术", "安全科学技术", "管理学", "经济学",
    "政治学", "法学", "军事学", "社会学", "民族学",
    "新闻学与传播学", "图书馆、情报与文献学", "教育学", "体育科学", "统计学",
]
LEVEL1_DISCIPLINES_TEXT = "、".join(LEVEL1_DISCIPLINES)


# ─── 学科分类 + Metadata 修正 Prompt ──────────────────────────

DISCIPLINE_SYSTEM_PROMPT = """你是一位严谨的学术论文元数据校验与学科分类专家。根据论文首页/前部原文，以及在前部范围以外补充的摘要或引言，完成两个任务：
1. 校验并修正自动匹配得到的标题、发表年份和 DOI
2. 判断论文的主学科及确有必要的交叉辅助学科

## 一级学科列表（level1 必须从中选择）

{discipline_list}

## 正则预提取结果

以下是通过正则自动提取的候选元数据，仅供核验，不能直接照抄。候选可能把页眉、作者、章节标题、引用文献 DOI 或参考文献年份误识别为本文信息：
- 预提取标题: {regex_title}
- 预提取年份: {regex_year}
- 预提取DOI: {regex_doi}

## 输出格式

```json
{{
  "title": "论文完整标题",
  "year": 2026,
  "doi": null,
  "primary_discipline": {{"level1": "一级学科名称", "level2": "二级学科名称", "level3": "三级学科名称"}},
  "secondary_disciplines": [
    {{"level1": "一级学科名称", "level2": "二级学科名称", "level3": "三级学科名称"}}
  ],
  "keywords": ["keyword1", "keyword2", "keyword3"]
}}
```

## 元数据核验规则

1. **title**：必须取自本文首页或前部原文中的完整论文标题。忽略期刊名、会议名、页眉页脚、作者/单位、章节标题、图表标题、项目页面标题和参考文献标题；可去除 Markdown 标记和异常空白，但不得改写标题。
2. **year**：仅填写本文明确出现的发表、接受、预印本提交或版权年份；不得使用引用文献年份或实验数据年份。无法从提供原文确认本文年份时输出 null，即使候选值非空。
3. **doi**：仅填写明确属于本文的 DOI，规范化为 `10.` 开头的 DOI 字符串，不保留 URL 前缀及末尾标点；引用文献中的 DOI 不属于本文。无法确认时输出 null，即使候选值非空。
4. 候选 title/year/doi 与论文原文冲突时必须修正；候选无法被本文原文支持时必须清空为 null（title 除外，尽力从首页提取）。

## 学科分类规则

1. **primary_discipline 必填**：代表论文核心研究问题和主要贡献；`level1` 必须逐字选自上面的一级学科列表，不得自造类别。
2. `level2` 和 `level3` 应给出从一级学科逐层细化的领域名称；能判断时必须填写，证据不足时填 null，不得用论文标题代替学科。
3. **secondary_disciplines**：仅当另一学科实质参与研究问题、数据、方法或结论时输出数组；仅使用通用技术工具或在背景中提及不算交叉学科。每个辅助学科同样必须包含列表内的 `level1` 及可判断的 `level2`/`level3`。
4. 主学科不要在辅助学科中重复。
5. **keywords**：输出 3-5 个最相关的英文原文术语，优先覆盖研究对象、方法和任务。"""

DISCIPLINE_USER_PROMPT_TEMPLATE = """## 论文内容

{paper_content}

请审查修正元数据并输出学科分类和关键词。"""


def _normalized_prompt_content(text: str) -> str:
    """用于抑制前部文本与已解析章节的重复输入，不改变实际提交文本。"""
    text = re.sub(r"[*_`#]", "", text)
    return re.sub(r"\s+", " ", text).strip().casefold()


def build_discipline_prompt(abstract: str, introduction: str, paper_head: str,
                            regex_title: str, regex_year, regex_doi) -> tuple[str, str]:
    """构建学科分类和 metadata 修正 prompt。"""
    content_parts = []
    if paper_head:
        content_parts.append(f"### 论文首页/前部原文（用于核验 title / year / DOI）\n\n{paper_head}")
    included_content = _normalized_prompt_content(paper_head)
    for section_name, section_text in (("Abstract", abstract), ("Introduction", introduction)):
        normalized_section = _normalized_prompt_content(section_text)
        if not normalized_section or normalized_section in included_content:
            continue
        content_parts.append(f"### {section_name}\n\n{section_text}")
        included_content = f"{included_content} {normalized_section}".strip()
    paper_content = "\n\n".join(content_parts) or "无内容"

    sys_prompt = DISCIPLINE_SYSTEM_PROMPT.format(
        discipline_list=LEVEL1_DISCIPLINES_TEXT,
        regex_title=regex_title or "未提取到",
        regex_year=str(regex_year) if regex_year else "未提取到",
        regex_doi=regex_doi or "未提取到",
    )
    user_prompt = DISCIPLINE_USER_PROMPT_TEMPLATE.format(paper_content=paper_content)
    return sys_prompt, user_prompt


# ─── Anchor 抽取定义 ──────────────────────────────────────────

_TYPE_FIELD_HINTS = {
    "concept": "term, normalized, std_label",
    "relation": "head_term, relation_type(enhances|inhibits|causes|creates|influences|belongs_to|measures|uses|compares|precedes|derives), relation_surface, tail_term；head_term/tail_term 使用原文中的清晰实体名，已输出为 concept 的端点优先复用其 term 或 std_label",
    "dataset": "name, modality(text|image|tabular|time_series|multimodal|other), domain",
    "data_specification": "spec_type(format_rule|quality_standard|env_requirement|metadata_standard), description",
    "method": "name, method_type(model|algorithm|protocol|software|instrument|preprocessing|field_research|textual_analysis|survey|interview)",
    "experiment": "task, setup",
    "quantitative_result": "quantity, value（保留原文单值、区间或关键多值文本）, unit, context, result_type(main_result|baseline|ablation|measurement|threshold)",
    "performance_result": "metric, compared_to",
    "conclusion": "（仅需 evidence）",
    "claim": "（仅需 evidence）",
    "future_work": "（仅需 evidence）",
    "limitation": "（仅需 evidence）",
}

_TYPE_GUIDANCE = {
    "concept": """**concept 提取要求**：
- 只提取理解论文研究问题、核心方法、主要实验和结论所必需的概念：论文提出的方法/框架、关键任务、核心机制、主要评价对象和 benchmark
- 同一概念的全称、缩写、大小写变体只输出一条；`term` 优先使用首次明确给出全称或全称加缩写的原文形式，`std_label` 填明确缩写
- 不提取泛化词汇、普通章节术语、仅作背景介绍且未参与本文贡献的实体
- 数量服从论文实际贡献结构：覆盖核心实体即可，复杂综述或分类框架可适度增加，但不得用重复项填充""",

    "relation": """**relation 提取要求**：
- 只提取直接表达论文核心方法、机制、实验或结论的关系；关系必须对下游推理有用
- 优先覆盖：方法 uses 数据/关键模块、方法 enhances 指标或能力、实验 measures 指标、方法 compares 基线、机制 causes/influences 发现
- head_term 与 tail_term 必须是原文明确出现的短实体名；当端点已经作为本组 concept 输出时，优先逐字复用该 concept 的 `term` 或 `std_label`，便于程序自动建立链接
- 如果一条关系有直接 evidence 且对主要贡献或结论有价值，不要仅因某个端点未被单独抽成 concept 而省略该关系；程序会保留 term 和 evidence，并在可匹配时链接对应的 entry_id
- 不输出背景中的宽泛关联、推断关系、同义重复关系，或只有一个端点具有明确实体含义的关系
- 数量服从可证实的核心语义连接；内容不足时允许更少，不得为增加条目构造弱关系""",

    "dataset": """**dataset 提取要求**：
- 仅提取本文实际用于训练、评估、统计分析或数据构建的数据集、benchmark 或明确研究样本
- 社会科学中的实际调查样本、访谈记录、档案汇编可视为 dataset
- 对综述论文，不将仅被介绍的外部数据集、工具数据库或应用示例当作本文 dataset，除非作者将其作为综述分析语料
- 当论文在一个评测套件中列举大量 benchmark 时，保留整体评测集合和直接支撑主要结论的少量代表 benchmark，不为每个被列举子 benchmark 单独生成条目
- modality 根据数据性质选择：问卷调查→tabular，文本档案→text，地理信息→image
- 合并同一数据集的别名；name 为必填字段；只保留实际参与本文论证的数据来源""",

    "data_specification": """**data_specification 提取要求**：
- 仅提取与本文数据生成、采集、过滤、标注或评估可复现性直接有关的格式、质量标准、环境要求、元数据标准
- 社会科学中的抽样标准、访谈协议、编码规范同样视为 data_specification
- 不提取普通实现说明或无关平台描述；spec_type 必填：format_rule/quality_standard/env_requirement/metadata_standard""",

    "method": """**method 提取要求**：
- 提取论文提出的方法主体、决定主要结果的关键模块，以及复现实验所需的关键方法
- 不要把每个辅助工具、常规预处理步骤或被比较方法的细小组件全部拆成独立条目
- 自然科学：instrument, protocol, model, algorithm
- 社会科学：survey, interview, field_research
- 人文学科：textual_analysis
- 通用：software, preprocessing
- method_type 必填；只保留影响论文贡献或复现的关键方法""",

    "experiment": """**experiment 提取要求**：
- 提取用于支撑主要结果、消融结论或稳健性判断的关键实验/研究设计
- 自然科学保留关键条件；社会科学保留调查/统计设计；人文学科保留核心分析路径和史料范围
- 不将每个重复基线运行或一般流程说明单独列项
- task 必填，setup 一句话概括关键参数；按支撑主要结论所需的实验覆盖范围提取""",

    "quantitative_result": """**quantitative_result 提取要求**：
- 仅提取本文作者实验、统计分析、消融研究或正式综合分析产生的关键数值结果，覆盖主结果和少量支撑主结论的结果
- 社会科学中的本文样本量、相关系数、p 值、效应量等可以提取
- 若本文是 survey/review，只有作者明确报告的综述语料统计、系统性汇总或作者完成的定量比较才可输出 quantitative_result；应用示例、假设场景、案例回答、被引用工作的单项数值必须排除，即使句子含百分比或看起来像结果
- 必须包含 context 字段，说明完整实验/研究背景
- value 保持原文中的单值、区间或关键多值文本，不为满足数值字段将同一结果拆成重复条目
- 对主结果表、基线表、消融表，优先使用包含关键结果的表格行作为 anchor；每张表只保留支撑主要结论的代表性数据，不逐行穷举
- 排除年份、页数、编号等元信息
- quantity、context、result_type 必填；只保留足以支撑主要结果判断的关键数值""",

    "performance_result": """**performance_result 提取要求**：
- 提取作者方法与明确基线、消融变体或标准方案之间直接支持论文结论的核心对比
- 避免将同一个数值结果分别重复表述为多个性能条目；避免仅为背景描述的定性比较
- 对 survey/review，不将被综述工作的表现或应用示例当作本文 performance_result，除非作者做了明确的综合比较分析
- metric 和 compared_to 为可选字段；只保留支撑论文主要比较结论的结果""",

    "claim": """**claim 提取要求**：
- claim 是论文的"中心论点"——作者试图论证的核心命题
- 区分 claim 和 conclusion：claim 是主张/假设，conclusion 是验证后的结论
- 人文社科论文中，claim 包括作者的理论立场、解释框架
- 只保留可由本文 evidence 直接支持的核心 claim，不重复改写同一主张""",

    "conclusion": """**conclusion 提取要求**：
- conclusion 是经过实验/分析/论证验证后的确定性结论
- 关注"因此"、"结果表明"、"本文发现"等标志性表述
- 不把方法介绍或未来愿景作为 conclusion，不与 claim 重复""",

    "limitation": """**limitation 提取要求**：
- 提取论文明确承认的局限性：方法局限、样本局限、适用范围、假设约束
- 人文社科中的史料局限、方法论边界同样提取
- 不将综述对象的一般缺点误作本文局限；没有明确讨论本文局限性时返回空列表""",

    "future_work": """**future_work 提取要求**：
- 提取论文明确提出的未来研究方向和建议
- 不仅提取"future work"章节，也包括讨论中提到的后续方向
- 不将背景展望或被引用工作建议误作本文 future work；没有明确讨论未来方向时返回空列表""",
}

_GROUP_GUIDANCE = {
    "GA_study_design": """## 本组覆盖目标

- 覆盖论文的研究对象、核心方法、实际数据/benchmark 和关键实验设置，形成理解贡献所需的研究设计骨架
- 本组同时构建 concept 及其核心 relation；已有核心 concept 的 relation 端点应复用同一名称以提高链接率
- 优先保证核心覆盖和去重，不为数量增加背景词、辅助组件或重复别名""",
    "GB_results_claims": """## 本组覆盖目标

- 覆盖作者自己的主要结果、核心结论、明确局限和未来工作
- 内容简短、综述或理论论文按实际可证实结果提取，不人为补足数量
- 优先保留主结果、关键比较与结论证据，不逐行枚举表格数值、不把示例或引用结果当作本文发现""",
}


def _build_anchor_prompt(paper_text: str, types: list[str], group_name: str) -> tuple[str, str, str]:
    """构建只要求 LLM 输出证据锚点的 prompt；完整上下文由程序回填。"""
    type_list = "、".join(types)
    field_desc = "\n\n".join(
        f"### {entry_type}\n字段：{_TYPE_FIELD_HINTS.get(entry_type, '')}"
        for entry_type in types
    )
    guidance = "\n\n".join(
        _TYPE_GUIDANCE[entry_type]
        for entry_type in types
        if entry_type in _TYPE_GUIDANCE
    )
    group_guidance = _GROUP_GUIDANCE.get(group_name, "")

    sys_prompt = f"""你是一位学术文献知识抽取专家。从学术论文中提取以下知识类型，覆盖所有学科领域。

## 输出格式

```json
{{"entries": [{{"type": "...", "payload": {{"...该类型专属字段": "..."}}, "evidence": {{"section": "...", "anchor_text": "原文中的一句完整句子或一条表格行"}}, "confidence": 0.95}}]}}
```

每条条目公共字段：
- type: 以下知识类型之一
- evidence.section: 原文所在章节名称，无法判断可填空字符串
- evidence.anchor_text: 仅输出能唯一支持该条目的短原文锚点；从输入中复制一个连续原句或包含关键关系/数值的连续从句，表格使用包含关键值的完整数据行或 caption。必须逐字复制输入，不得改写，且保留 Markdown/数学符号的输入字面形式
- confidence: 0-1 置信度
- payload: 仅放置该类型的专属字段，字段见下方
- 不要输出 entry_id、doc_id、page、original_text、match_method 或 relation 的链接 ID；这些字段由程序生成或回填

## 知识类型字段定义

{field_desc}

## 各类型提取指导

{guidance}

{group_guidance}

## 提取原则

1. **锚点忠实性**：生成条目后，重新从输入文本中直接复制 anchor_text；它必须是连续出现的逐字摘录。禁止压缩总结、翻译、改写、合并不相邻文本或补充解释；例如输入中的 `_𝜏_[2] -Bench` 不得写作渲染后的 `τ^2-Bench`
2. **上下文无需复述**：不要输出长段 original_text。程序会在定位 anchor_text 后自动补回足量原文上下文供下游处理
3. **表格证据**：数值来自表格时，anchor_text 必须逐字符复制包含实体和关键数值的单条表格行（保留可见分隔符和数值格式）；必要时可逐字复制明确描述结果的 caption
4. **重要性优先**：完整覆盖理解论文贡献和主要结论所必需的知识；不要提取低价值细节、同义重复项、示例内容或仅被引用工作的发现
5. **证据准入**：如果找不到能够逐字复制、明确支撑条目的单句、表格行或 caption，则不要输出该条目
6. **数量控制**：数量为软参考而非配额；内容丰富时覆盖关键知识，内容有限时宁缺毋滥
7. **防幻觉**：如果论文缺乏对应类型的实质内容，返回空列表 {{"entries": []}}"""

    user_prompt = f"""## 论文内容

{paper_text}

请提取{type_list}。"""

    return group_name, sys_prompt, user_prompt


def build_grouped_extraction_prompts(paper_text: str, quantitative_text: str | None = None) -> list[tuple[str, str, str]]:
    """构建默认两组 anchor 抽取 prompt；结果与论断组可接收含表格文本。"""
    quantitative_text = quantitative_text if quantitative_text is not None else paper_text
    return [
        _build_anchor_prompt(
            paper_text,
            ["concept", "relation", "dataset", "data_specification", "method", "experiment"],
            "GA_study_design",
        ),
        _build_anchor_prompt(
            quantitative_text,
            ["quantitative_result", "performance_result",
             "claim", "conclusion", "limitation", "future_work"],
            "GB_results_claims",
        ),
    ]


def build_custom_prompt(paper_text: str, types: list[str], group_idx: int) -> tuple[str, str, str]:
    """为任意类型组合构建 anchor prompt。"""
    return _build_anchor_prompt(paper_text, types, f"GX_custom_{group_idx}")


def parse_custom_group_spec(spec: str) -> list[list[str]]:
    """解析 EXTRACT_GROUP_SPEC，例如 `concept|relation|dataset+method`。"""
    groups = []
    for part in spec.split("|"):
        types = [entry_type.strip() for entry_type in part.split("+")
                 if entry_type.strip() in _TYPE_FIELD_HINTS]
        if types:
            groups.append(types)
    return groups
