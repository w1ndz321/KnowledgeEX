"""
kg_prompts.py — 论文知识抽取的 prompt 模板

学科分类 prompt: 传入 abstract+intro（或前N字符），LLM 输出 title/year/doi + 学科 + keywords
知识抽取 prompt: 传入论文全文，LLM 输出 12 类知识条目
"""

import json

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

# ─── 知识抽取输出示例 ───────────────────────────────────────

OUTPUT_EXAMPLE = {
    "entries": [
        {"type": "concept", "concept_id": "doc_xxx_c1", "term": "high-entropy alloy", "normalized": "高熵合金",
         "std_label": "HEA",
         "evidence": {"section": "Abstract", "original_text": "We reveal that high-entropy alloys (HEAs) can efficiently activate the LOM through synergistic multi-path electron transfer. Configurational entropy in these multi-principal-element alloys stabilizes a single-phase solid solution, enabling unique catalytic properties not achievable in binary or ternary systems. Unlike conventional alloys that rely on one or two principal elements, HEAs contain five or more principal elements in near-equimolar ratios, creating a vast compositional space. The high configurational entropy lowers the Gibbs free energy, stabilizing disordered solid solution phases over intermetallic compounds. This entropic stabilization has been exploited in various fields including structural materials, catalysis, and energy storage. The resulting homogeneous elemental distribution at the atomic scale gives rise to unique electronic structures that are fundamentally different from those of dilute alloys."},
         "confidence": 0.96},
        {"type": "concept", "concept_id": "doc_xxx_c2", "term": "oxygen evolution reaction", "normalized": "氧析出反应",
         "std_label": "OER",
         "evidence": {"section": "Abstract", "original_text": "Electrocatalytic oxygen evolution reaction (OER) is key to several energy technologies but suffers from low activity. The sluggish four-electron transfer kinetics of OER remains the primary bottleneck in water splitting and metal-air battery technologies. The OER involves multiple proton-coupled electron transfer steps, each with its own activation barrier, making the overall reaction kinetically demanding. Traditional OER catalysts based on noble metal oxides such as IrO2 and RuO2 exhibit high activity but their scarcity and high cost limit large-scale deployment. Consequently, developing earth-abundant and highly active OER electrocatalysts has become a central challenge in the field of renewable energy conversion. The overpotential required to drive OER at practical current densities directly impacts the overall energy efficiency of water electrolysis systems."},
         "confidence": 0.98},
        {"type": "relation", "relation_id": "doc_xxx_r1", "head": "high-entropy alloy",
         "relation_type": "enhances", "relation_surface": "can efficiently activate",
         "tail": "oxygen evolution reaction",
         "evidence": {"section": "Abstract", "original_text": "We reveal that high-entropy alloys (HEAs) can efficiently activate the LOM for enhanced oxygen evolution activity. Density functional theory calculations confirm that the multi-element composition facilitates electron redistribution, lowering the energy barrier for the rate-determining step. The synergistic effect arises from the coexistence of multiple transition metal atoms in the lattice, each contributing distinct electronic states near the Fermi level. This multi-path electron transfer mechanism bypasses the conventional adsorbate evolution mechanism (AEM), which is constrained by the linear scaling relations between intermediate binding energies. As a result, the HEA catalyst achieves an overpotential of only 280 mV at 10 mA/cm², substantially outperforming both IrO2 (320 mV) and RuO2 (310 mV) benchmark catalysts. Electrochemical impedance spectroscopy further confirmed a significantly reduced charge transfer resistance for the HEA compared to the noble metal benchmarks."},
         "confidence": 0.93},
        {"type": "dataset", "dataset_id": "doc_xxx_d1", "name": "OER activity benchmark",
         "modality": "tabular", "domain": "electrochemistry",
         "evidence": {"section": "Results", "original_text": "We benchmark our HEA against state-of-the-art OER catalysts including IrO2 and RuO2. The benchmark dataset comprises overpotential measurements at 10 mA/cm² from 15 independently synthesized electrodes. All measurements were repeated in triplicate to ensure statistical reliability. The dataset also includes Tafel slope values derived from linear sweep voltammetry at a scan rate of 5 mV/s. Electrochemical impedance spectroscopy data were collected at frequencies ranging from 100 kHz to 0.1 Hz with a 10 mV AC amplitude. Chronopotentiometry stability data were recorded at a constant current density of 10 mA/cm² for 24 hours. The raw data files including polarization curves and Nyquist plots are available in the supplementary information."},
         "confidence": 0.90},
        {"type": "method", "method_id": "doc_xxx_m1", "name": "Raman spectroscopy", "method_type": "instrument",
         "evidence": {"section": "Methods", "original_text": "We analyzed the catalyst surface using Raman spectroscopy following the i-t tests in both KOH and TMAOH solutions. Spectra were collected using a 532 nm excitation laser with 5 mW power, integrating 10 scans of 30 seconds each to achieve adequate signal-to-noise ratio. The laser spot size was approximately 1 μm in diameter, allowing spatially resolved mapping of the electrode surface. Baseline correction was performed using a cubic spline interpolation, and peak fitting was carried out with Lorentzian functions. The spectrometer was calibrated using a silicon standard (520.7 cm⁻¹) before each measurement session. Post-electrochemical Raman measurements were conducted ex situ after carefully rinsing the electrode with deionized water and drying under nitrogen flow."},
         "confidence": 0.95},
        {"type": "experiment", "experiment_id": "doc_xxx_x1", "task": "electrochemical stability test",
         "setup": "three-electrode cell, 1M KOH, 298 K, glassy carbon RDE at 1600 rpm",
         "evidence": {"section": "Methods", "original_text": "All electrochemical measurements were performed at 298 K under ambient pressure using a standard three-electrode configuration with a graphite counter electrode and Ag/AgCl reference electrode. All potentials were iR-corrected and calibrated to the reversible hydrogen electrode (RHE) scale. Chronoamperometry was conducted at a constant potential of 1.53 V vs RHE for 24 hours, with the electrolyte continuously purged with O2 to maintain saturation. The working electrode rotation speed was fixed at 1600 rpm to ensure efficient mass transport and rapid removal of evolved oxygen bubbles. The catalyst loading on the glassy carbon electrode was precisely controlled at 0.2 mg/cm². Three independent electrodes were prepared for each catalyst composition to assess batch-to-batch reproducibility."},
         "confidence": 0.95},
        {"type": "performance_result", "perf_id": "doc_xxx_p1",
         "metric": "overpotential at 10 mA/cm²", "compared_to": "IrO2, RuO2",
         "evidence": {"section": "Results", "original_text": "The FeCoNiCrMn HEA exhibits a low overpotential of 280 mV at 10 mA/cm², outperforming IrO2 (320 mV) and RuO2 (310 mV). This represents a 12.5% and 9.7% improvement respectively. The Tafel slope of 45 mV/dec indicates favorable reaction kinetics compared to 68 mV/dec for pure Co3O4. The mass activity of the HEA catalyst at 1.53 V was 120 A/g, which is approximately 3-fold higher than that of IrO2 (40 A/g). Electrochemical impedance spectroscopy revealed a charge transfer resistance of 12 Ω for the HEA, significantly lower than 38 Ω for IrO2, confirming faster electron transfer kinetics at the electrode-electrolyte interface. Stability tests over 1000 cyclic voltammetry cycles showed negligible degradation in overpotential, confirming the robustness of the catalyst under operating conditions."},
         "confidence": 0.95},
        {"type": "quantitative_result", "qr_id": "doc_xxx_qr1", "quantity": "overpotential", "value": 280, "unit": "mV", "context": "FeCoNiCrMn HEA at 10 mA/cm² current density in 1M KOH",
         "result_type": "main_result",
         "evidence": {"section": "Results", "original_text": "The FeCoNiCrMn HEA exhibits a low overpotential of 280 mV at 10 mA/cm², outperforming IrO2 (320 mV) and RuO2 (310 mV). Chronoamperometry measurements at a constant potential of 1.53 V vs RHE confirmed stable current density over 24 hours of continuous operation with less than 3% degradation. The polarization curve was recorded at a slow scan rate of 2 mV/s to minimize capacitive contributions. The overpotential values were determined at the geometric current density of 10 mA/cm², which corresponds to approximately 10% efficient solar-to-fuel conversion. Error bars represent the standard deviation from measurements on five independent electrodes. The improvement is attributed to the multi-path electron transfer mechanism facilitated by configurational entropy."},
         "confidence": 0.97},
        {"type": "quantitative_result", "qr_id": "doc_xxx_qr2", "quantity": "Tafel slope", "value": 45, "unit": "mV/dec", "context": "FeCoNiCrMn HEA in 1M KOH electrolyte",
         "result_type": "measurement",
         "evidence": {"section": "Results", "original_text": "The Tafel slope was measured to be 45 mV/dec, indicating favorable reaction kinetics and suggesting the lattice oxygen mechanism as the dominant pathway. This value is substantially lower than the 68 mV/dec observed for pure Co3O4 under identical conditions. Tafel analysis was performed on the linear region of the polarization curve after iR correction, covering at least one decade of current density. The low Tafel slope implies that the rate-determining step involves the second electron transfer, consistent with the LOM pathway. Complementary pH-dependent measurements confirmed the LOM assignment, as the overpotential showed negligible dependence on electrolyte pH in the range of 12.5 to 14."},
         "confidence": 0.94},
        {"type": "data_specification", "ds_id": "doc_xxx_ds1", "spec_type": "quality_standard",
         "description": "Standardized three-electrode measurement protocol with iR compensation and RHE calibration",
         "evidence": {"section": "Methods", "original_text": "All electrochemical measurements were performed at 298 K under ambient pressure using a standard three-electrode configuration with a graphite counter electrode and Ag/AgCl reference electrode. The working electrode was prepared by drop-casting the catalyst ink onto a glassy carbon rotating disk electrode (0.196 cm²). All potentials were iR-corrected and calibrated to the reversible hydrogen electrode (RHE) scale. The uncompensated resistance was determined by electrochemical impedance spectroscopy at open circuit potential and compensated at 85% level. The electrolyte was 1 M KOH solution prepared with ultrapure water (18.2 MΩ·cm) and purged with high-purity O2 for 30 minutes prior to each experiment. The Ag/AgCl reference electrode was calibrated against a reversible hydrogen electrode in the same electrolyte before and after each measurement session."},
         "confidence": 0.95},
        {"type": "claim", "claim_id": "doc_xxx_ca1",
         "evidence": {"section": "Abstract", "original_text": "Configurational entropy in high-entropy alloys synergistically activates multiple electron transfer pathways, enabling superior OER performance. This finding challenges the conventional wisdom that catalytic activity is solely determined by the electronic structure of individual active sites. The multi-path electron transfer mechanism provides a new paradigm for designing next-generation electrocatalysts. By incorporating multiple transition metal elements into a single-phase solid solution, the catalyst can exploit a continuum of electronic states to facilitate charge transfer at the electrode-electrolyte interface. This concept extends beyond OER catalysis and may be broadly applicable to other multi-electron reactions such as CO2 reduction and nitrogen fixation."},
         "confidence": 0.96},
        {"type": "conclusion", "conclusion_id": "doc_xxx_cl1",
         "evidence": {"section": "Conclusion", "original_text": "High-entropy alloys enable multi-path electron transfer to synergistically activate the lattice oxygen mechanism, providing a new design strategy for efficient OER catalysts. These results demonstrate that configurational complexity can be harnessed as a design parameter to overcome the activity-stability tradeoff that has long plagued OER catalyst development. The FeCoNiCrMn system serves as a model platform, but the design principles established here are generalizable to other HEA compositions. Our work establishes a direct link between configurational entropy, electronic structure, and catalytic activity, opening new avenues for rational catalyst design. The comprehensive electrochemical characterization combined with DFT calculations provides a mechanistic understanding that can guide future catalyst development."},
         "confidence": 0.96},
        {"type": "limitation", "limitation_id": "doc_xxx_lm1",
         "evidence": {"section": "Discussion", "original_text": "The current study is limited to five-component HEAs in alkaline media; generalizability to acid-stable compositions remains to be demonstrated. Furthermore, the long-term stability beyond 24 hours and the performance under practical device conditions (e.g., membrane electrode assemblies) have not been evaluated. The DFT calculations were performed on idealized slab models that do not capture surface reconstruction effects under operating potentials. Additionally, the precise contributions of individual elements to the overall activity cannot be deconvoluted from the present experimental data alone. The catalyst synthesis method yields polycrystalline samples, and the role of specific crystal facets or grain boundaries in the catalytic process warrants further investigation. Finally, the cost and scalability of HEA synthesis compared to conventional binary or ternary catalysts were not assessed."},
         "confidence": 0.92},
        {"type": "future_work", "future_work_id": "doc_xxx_fw1",
         "evidence": {"section": "Discussion", "original_text": "Future studies should explore HEAs in other electrocatalytic reactions such as CO2 reduction and nitrogen reduction. Additionally, machine learning-guided composition screening could accelerate the discovery of optimal HEA formulations. In-situ/operando characterization techniques such as X-ray absorption spectroscopy and surface-enhanced Raman spectroscopy are needed to directly probe the multi-path electron transfer mechanism under working conditions. Systematic investigation of the effect of each constituent element through targeted substitution experiments would help elucidate individual contributions to catalytic activity. Long-term durability testing under industrially relevant current densities exceeding 500 mA/cm² is essential to assess practical viability for commercial electrolyzer applications."},
         "confidence": 0.93},
    ]
}
OUTPUT_EXAMPLE_JSON = json.dumps(OUTPUT_EXAMPLE, ensure_ascii=False, indent=2)

# ─── 学科分类 + Metadata 修正 Prompt ──────────────────────────

DISCIPLINE_SYSTEM_PROMPT = """你是一位学术论文元数据分析专家。根据提供的论文内容，完成两个任务：
1. 审查并修正论文元数据（标题、年份、DOI）
2. 判断学科归属

## 一级学科列表（level1 必须从中选择）

{discipline_list}

## 正则预提取结果

以下是通过正则自动提取的元数据，可能存在错误（如标题误识别为期刊名、年份不正确等），请根据论文内容审查修正：
- 预提取标题: {regex_title}
- 预提取年份: {regex_year}
- 预提取DOI: {regex_doi}

## 输出格式

```json
{{
  "title": "论文完整标题",
  "year": 2026,
  "doi": "10.xxxx/xxxxx 或 null",
  "primary_discipline": {{"level1": "一级学科名称", "level2": "二级学科名称", "level3": "三级学科名称"}},
  "secondary_disciplines": {{"level1": "一级学科名称", "level2": "二级学科名称", "level3": "三级学科名称"}},
  "keywords": ["keyword1", "keyword2", "keyword3"]
}}
```

## 要求

1. **title**：论文的完整标题，必须从论文内容原文中提取，不要改写。预提取标题仅作参考——如果它明显是章节标题（如 \"II. SLUM MAPPING\"）、大小写错乱（如 \"oPeN DATA\"）、作者列表或期刊名，必须忽略并从论文开头找到真正的论文标题
2. **year**：论文发表年份。从版权信息(©)、Published/Date/Accepted 字段推断，如果预提取年份明显不合理请修正
3. **doi**：DOI 编号，找不到则填 null
4. **primary_discipline 必填**：level1 必须从上述列表中选择，level2/level3 无法确定则填 null
5. **secondary_disciplines**：仅当论文明确涉及其他学科时才填写，否则填 null
6. **keywords**：3-5 个最相关的英文关键词，使用原文术语"""

DISCIPLINE_USER_PROMPT_TEMPLATE = """## 论文内容

{paper_content}

请审查修正元数据并输出学科分类和关键词。"""

# ─── 知识抽取 Prompt ─────────────────────────────────────────

EXTRACTION_SYSTEM_PROMPT = """你是一位学术文献知识抽取专家。从学术论文中提取结构化知识，覆盖自然科学、社会科学、人文学科等所有学科领域。

## 输出格式

```json
{output_example}
```

## 12 种知识类型

1. **concept**: 关键概念、术语、实体（term/normalized/std_label）
2. **relation**: 概念间语义关系（head/tail 必须对应已抽取的 concept term）
   relation_type: enhances | inhibits | causes | creates | influences | belongs_to | measures | uses | compares | precedes | derives
3. **dataset**: 论文使用/产生的数据集（name/modality/domain）
   modality 取值: text | image | tabular | time_series | multimodal | other
4. **method**: 方法、模型、算法、仪器、研究手段（name/method_type）
   method_type 取值: model | algorithm | protocol | software | instrument | preprocessing | field_research | textual_analysis | survey | interview
5. **experiment**: 实验设置与流程（task/setup）
6. **performance_result**: 性能对比/评价（metric/compared_to）
7. **quantitative_result**: 科学度量与实验指标（排除年份、页数、编号等元信息）（quantity/value/unit/context/result_type）
   result_type 取值: main_result | baseline | ablation | measurement | threshold
8. **data_specification**: 数据格式规范、质量标准、环境要求（spec_type/description）
   spec_type 取值: format_rule | quality_standard | env_requirement | metadata_standard
9. **conclusion**: 核心结论（仅 evidence）
10. **claim**: 核心主张/发现（仅 evidence）
11. **future_work**: 未来研究方向（仅 evidence）
12. **limitation**: 方法局限、适用约束（仅 evidence）

## 提取原则

1. **忠实性**：original_text 必须是原文直接摘录，禁止改写或总结
2. **完整性**：original_text 必须是完整段落，至少包含 8-10 个完整句子，保留充足的上下文使得读者无需查看原文即可完全理解该知识条目的含义。必须包含实验结果的具体数值、实验条件、对比基准等所有细节
3. **关联性**：relation 的 head/tail 必须对应已抽取的 concept term
4. **轻标注**：concept/dataset/method/experiment 的标注字段值简短
5. **覆盖度**：原文中存在的就全部提取，不要遗漏也不要编造。每组输出 token 上限8000，组内各类型均衡分配
6. **claim/conclusion/limitation/future_work/data_specification** 不是每篇都有，按实际内容抽取
7. **quantitative_result** 应尽可能提取论文中所有出现的主要数值结果，包括图表中报告的数据、消融实验、基线对比等"""

EXTRACTION_USER_PROMPT_TEMPLATE = """## 论文内容

{paper_text}

请提取知识条目。"""


# ─── Prompt 构建函数 ────────────────────────────────────────

def build_discipline_prompt(abstract: str, introduction: str, paper_head: str,
                            regex_title: str, regex_year, regex_doi) -> tuple[str, str]:
    """构建学科分类+metadata修正 prompt。

    优先使用 abstract+introduction，缺失则用 paper_head（论文前N字符）。
    regex_title/year/doi 传入正则预提取结果，让 LLM 审查修正。
    """
    if abstract or introduction:
        content_parts = []
        if abstract:
            content_parts.append(f"### Abstract\n\n{abstract}")
        if introduction:
            content_parts.append(f"### Introduction\n\n{introduction}")
        paper_content = "\n\n".join(content_parts)
    else:
        paper_content = paper_head or "无内容"

    sys_prompt = DISCIPLINE_SYSTEM_PROMPT.format(
        discipline_list=LEVEL1_DISCIPLINES_TEXT,
        regex_title=regex_title or "未提取到",
        regex_year=str(regex_year) if regex_year else "未提取到",
        regex_doi=regex_doi or "未提取到",
    )
    user_prompt = DISCIPLINE_USER_PROMPT_TEMPLATE.format(paper_content=paper_content)
    return sys_prompt, user_prompt


def build_extraction_prompt(paper_text: str) -> tuple[str, str]:
    return (EXTRACTION_SYSTEM_PROMPT.format(output_example=OUTPUT_EXAMPLE_JSON),
            EXTRACTION_USER_PROMPT_TEMPLATE.format(paper_text=paper_text))


# ═══════════════════════════════════════════════════════════════
#  分组提取 Prompt：将 12 种知识类型拆为 5 组串行抽取
#  Group1: concept + relation
#  Group2: dataset + data_specification
#  Group3: method + experiment
#  Group4: quantitative_result + performance_result
#  Group5: conclusion + claim + limitation + future_work
# ═══════════════════════════════════════════════════════════════

# ─── Group1: concept + relation ─────────────────────────────

G1_OUTPUT_EXAMPLE = {
    "entries": [
        {"type": "concept", "concept_id": "doc_xxx_c1", "term": "high-entropy alloy", "normalized": "高熵合金",
         "std_label": "HEA",
         "evidence": {"section": "Abstract",
             "original_text": "We reveal that high-entropy alloys (HEAs) can efficiently activate the LOM through synergistic multi-path electron transfer. Configurational entropy in these multi-principal-element alloys stabilizes a single-phase solid solution, enabling unique catalytic properties not achievable in binary or ternary systems. Unlike conventional alloys that rely on one or two principal elements, HEAs contain five or more principal elements in near-equimolar ratios, creating a vast compositional space. The high configurational entropy lowers the Gibbs free energy, stabilizing disordered solid solution phases over intermetallic compounds. This entropic stabilization has been exploited in various fields including structural materials, catalysis, and energy storage."},
         "confidence": 0.96},
        {"type": "concept", "concept_id": "doc_xxx_c2", "term": "Articles of Confederation", "normalized": "邦联条例",
         "std_label": None,
         "evidence": {"section": "The 1776 Articles of Confederation",
             "original_text": "Although Dickinson wrote the Articles of Confederation for the nation, he did so with an eye toward the increasing anti-Quaker sentiment in Pennsylvania. The coup of the Pennsylvania government by the radicals and his recognition of the reality that America would probably revolt instigated his attempt to secure the Quakers' constitutional rights. His fear at this point was that the patriotic furor of the radicals, combined with their deep-seated resentment of nonradical Quakers, would overrun any regard for dissenters' rights. The main issue in framing an American constitution was similar to the question of the relation of the colonies to the British constitution – the power of the states in relation to the central government. Dickinson was not alone in his concern for such a power, but he was one of the most consistent advocates of it."},
         "confidence": 0.94},
        {"type": "relation", "relation_id": "doc_xxx_r1", "head": "high-entropy alloy",
         "relation_type": "enhances", "relation_surface": "can efficiently activate",
         "tail": "oxygen evolution reaction",
         "evidence": {"section": "Abstract",
             "original_text": "We reveal that high-entropy alloys (HEAs) can efficiently activate the LOM for enhanced oxygen evolution activity. Density functional theory calculations confirm that the multi-element composition facilitates electron redistribution, lowering the energy barrier for the rate-determining step. The synergistic effect arises from the coexistence of multiple transition metal atoms in the lattice, each contributing distinct electronic states near the Fermi level. This multi-path electron transfer mechanism bypasses the conventional adsorbate evolution mechanism (AEM), which is constrained by the linear scaling relations between intermediate binding energies. As a result, the HEA catalyst achieves an overpotential of only 280 mV at 10 mA/cm², substantially outperforming both IrO2 (320 mV) and RuO2 (310 mV) benchmark catalysts."},
         "confidence": 0.93},
        {"type": "relation", "relation_id": "doc_xxx_r2", "head": "John Dickinson",
         "relation_type": "creates", "relation_surface": "drafted",
         "tail": "Articles of Confederation",
         "evidence": {"section": "The 1776 Articles of Confederation",
             "original_text": "Although Dickinson wrote the Articles of Confederation for the nation, he did so with an eye toward the increasing anti-Quaker sentiment in Pennsylvania. Not wanting independence, but in preparation for it, he took the lead immediately before the Declaration in writing the Articles. The document that was submitted to Congress on July 12 was originally written by Dickinson, and then revised by him according to the critiques of his colleagues. He was one of a committee of thirteen that included, among others, Josiah Bartlett, Edward Rutledge, Samuel Adams, and Thomas McKean."},
         "confidence": 0.95},
    ]
}
G1_OUTPUT_EXAMPLE_JSON = json.dumps(G1_OUTPUT_EXAMPLE, ensure_ascii=False, indent=2)

G1_SYSTEM_PROMPT = """你是一位学术文献知识抽取专家。从学术论文中深度提取概念和概念间关系，覆盖自然科学、社会科学、人文学科等所有学科领域，目标是构建高质量的学科知识图谱。

## 输出格式

```json
{output_example}
```

## 知识类型

1. **concept**: 关键概念、术语、实体
   - term: 原文术语
   - normalized: 规范化中文名
   - std_label: 标准缩写（可选）
2. **relation**: 概念间语义关系
   - head/tail 必须对应已抽取的 concept term
   - relation_type: enhances | inhibits | causes | creates | influences | belongs_to | measures | uses | compares | precedes | derives

## 概念提取要求

1. **全学科覆盖**：论文所属学科的核心概念一律提取，不区分自然科学/社会科学/人文学科：
   - 理论框架与范式（如 "density functional theory"、"Quaker political theology"、"post-structuralism"）
   - 核心研究对象（材料、化合物、蛋白、基因、疾病、算法、制度、历史事件、思想流派、社会群体等）
   - 关键性质与属性（物理量、性能指标、制度特征、思想特征等）
   - 方法论概念（实验技术、数学方法、田野调查、文本分析、口述史等）
   - 学科标准分类与命名（行业标准、领域分类体系、历史分期等）
2. **层次覆盖**：同时抽取高层抽象概念和底层具体概念，覆盖论文的核心贡献点和关键支撑点
3. **数量要求**：每篇论文至少抽取 20-40 个高质量概念，学科相关的概念应占 60% 以上。人文社科论文同样需要提取充足概念（如思想流派、制度框架、历史事件、社会结构等）
4. **避免噪音**：不抽取过于泛化、无学科辨识度的词（如 "result"、"analysis"、"experiment"、"chapter"）

## 关系提取要求（必须严格遵守！）

1. **数量硬指标**：每篇论文必须输出 15-30 条高质量关系，不能少于 15 条。关系数量太少视为不合格
2. **全学科关系映射**：对每对语义相关的概念都要建立关系，不分学科。关系类型包括：
   - 因果关系（causes/inhibits）
   - 创建/制定关系（creates）——论文、法律、制度、作品等的创建
   - 思想影响关系（influences）——思想、政策、事件的塑造与影响
   - 所属/组成关系（belongs_to）
   - 方法/工具使用（uses/measures）
   - 增强/促进关系（enhances）
   - 比较/对比关系（compares）
   - 时间先后（precedes）
   - 派生/推导/继承（derives）——思想渊源、理论衍生、制度传承
3. **类型均衡**：优先使用 creates/influences/derives/enhances/causes 等强语义关系，belongs_to 不超过总数的 30%。人文社科学科多用 creates/influences/derives/precedes
4. **关系多样性**：同一 head 概念可关联多个不同 tail，同一 tail 也可被多个 head 关联。避免一个概念只参与一条关系
5. **evidence 必须充分**：每条 relation 的 evidence 至少 5-8 句，明确体现 head→tail 的语义

## 提取原则

1. **忠实性**：original_text 必须是原文直接摘录，禁止改写
2. **evidence 充足性**：每条 concept 和 relation 的 evidence 必须包含完整、充实的原文段落
   - 至少包含 8-10 个完整句子，充分展示概念出现的上下文和语义背景
   - relation 的 evidence 必须明确体现 head 和 tail 之间的语义关系，不能模糊暗示
3. **关联性**：relation 的 head/tail 必须对应已抽取的 concept term
4. **覆盖度**：原文中出现的学科重要概念全部提取，有明确语义关系的建立 relation。输出 token 上限 12000，concept 和 relation 必须均衡，relation 不得少于 15 条"""

G1_USER_PROMPT = """## 论文内容

{paper_text}

请提取概念和概念间关系。"""


# ─── Group1a: concept only ────────────────────────────────

GC_OUTPUT_EXAMPLE = {
    "entries": [
        {"type": "concept", "concept_id": "doc_xxx_c1", "term": "high-entropy alloy", "normalized": "高熵合金",
         "std_label": "HEA",
         "evidence": {"section": "Abstract",
             "original_text": "We reveal that high-entropy alloys (HEAs) can efficiently activate the LOM through synergistic multi-path electron transfer. Configurational entropy in these multi-principal-element alloys stabilizes a single-phase solid solution, enabling unique catalytic properties not achievable in binary or ternary systems. Unlike conventional alloys that rely on one or two principal elements, HEAs contain five or more principal elements in near-equimolar ratios, creating a vast compositional space. The high configurational entropy lowers the Gibbs free energy, stabilizing disordered solid solution phases over intermetallic compounds."},
         "confidence": 0.96},
        {"type": "concept", "concept_id": "doc_xxx_c2", "term": "Articles of Confederation", "normalized": "邦联条例",
         "std_label": None,
         "evidence": {"section": "The 1776 Articles of Confederation",
             "original_text": "Although Dickinson wrote the Articles of Confederation for the nation, he did so with an eye toward the increasing anti-Quaker sentiment in Pennsylvania. The coup of the Pennsylvania government by the radicals and his recognition of the reality that America would probably revolt instigated his attempt to secure the Quakers' constitutional rights. His fear at this point was that the patriotic furor of the radicals, combined with their deep-seated resentment of nonradical Quakers, would overrun any regard for dissenters' rights that had existed under the now-incapacitated 1701 Charter. The main issue in framing an American constitution was similar to the question of the relation of the colonies to the British constitution – the power of the states in relation to the central government."},
         "confidence": 0.94},
        {"type": "concept", "concept_id": "doc_xxx_c3", "term": "Quaker political theology", "normalized": "贵格会政治神学",
         "std_label": None,
         "evidence": {"section": "Introduction",
             "original_text": "Three Quaker-informed factions existed in Pennsylvania. Influenced by the conflict with Britain, two of them were gradually moving away from traditional Quaker theologico-politics – one toward individualistic, democratic, and armed radicalism; the other toward a withdrawn, passive stance, based on a new, narrower interpretation of the peace testimony. The radical group, in its beginnings hostile to Presbyterians in the campaign for royal government, now united with them, ostensibly to further the American cause. The withdrawing group of Quakers retreated from civic engagement and adopted a neutrality that was historically uncharacteristic of their Society when rights were threatened."},
         "confidence": 0.92},
    ]
}
GC_OUTPUT_EXAMPLE_JSON = json.dumps(GC_OUTPUT_EXAMPLE, ensure_ascii=False, indent=2)

GC_SYSTEM_PROMPT = """你是一位学术文献知识抽取专家。从论文中提取关键概念，覆盖自然科学、社会科学、人文学科等所有学科领域。

## 输出格式

```json
{output_example}
```

## 知识类型

**concept**: 关键概念、术语、实体
- term: 原文术语（优先使用论文原文语言）
- normalized: 规范化中文名
- std_label: 标准缩写（可选）

## 概念提取要求

1. **全学科覆盖**：论文所属学科的核心概念一律提取，不区分自然科学/社会科学/人文学科：
   - 理论框架与范式（如 "density functional theory"、"Quaker political theology"、"post-structuralism"）
   - 核心研究对象（材料、化合物、蛋白、基因、疾病、算法、制度、历史事件、思想流派、社会群体等）
   - 关键性质与属性（物理量、性能指标、制度特征、思想特征等）
   - 方法论概念（实验技术、数学方法、田野调查、文本分析、口述史等）
   - 学科标准分类与命名（行业标准、领域分类体系、历史分期等）
   - 关键人物/组织（仅当论文以之为核心分析对象时）
2. **层次覆盖**：同时抽取高层抽象概念和底层具体概念，覆盖论文的核心贡献点和关键支撑点
3. **数量要求**：每篇论文抽取 20-40 个高质量概念，学科相关概念占 60% 以上。人文社科论文同样需要提取充足概念（如思想流派、制度框架、历史事件、社会结构等）
4. **避免噪音**：不抽取过于泛化、无学科辨识度的词（如 "result"、"analysis"、"experiment"、"chapter"）
5. **术语规范**：选择标准、规范的术语形式，让后续关系抽取能准确匹配

## 提取原则

1. **忠实性**：original_text 必须是原文直接摘录，禁止改写
2. **evidence 充足性**：每条 evidence 至少 8-10 个完整句子，充分展示概念的上下文和语义背景
3. **覆盖度**：输出 token 上限 12000，学科重要概念全部提取
4. **防幻觉**：论文可能包含大量版权声明、下载信息、meta元数据等非学术内容。如果论文正文缺乏实质学术内容，返回空列表 {{\"entries\": []}}，不要从版权信息中编造概念"""

GC_USER_PROMPT = """## 论文内容

{paper_text}

请全面提取论文中的所有学科核心概念。"""

# ─── Group1b: relation only ───────────────────────────────

GR_OUTPUT_EXAMPLE = {
    "entries": [
        {"type": "relation", "relation_id": "doc_xxx_r1", "head": "high-entropy alloy",
         "relation_type": "enhances", "relation_surface": "can efficiently activate",
         "tail": "oxygen evolution reaction",
         "evidence": {"section": "Abstract",
             "original_text": "We reveal that high-entropy alloys (HEAs) can efficiently activate the LOM for enhanced oxygen evolution activity. Density functional theory calculations confirm that the multi-element composition facilitates electron redistribution, lowering the energy barrier for the rate-determining step. The synergistic effect arises from the coexistence of multiple transition metal atoms in the lattice, each contributing distinct electronic states near the Fermi level. This multi-path electron transfer mechanism bypasses the conventional adsorbate evolution mechanism (AEM). As a result, the HEA catalyst achieves an overpotential of only 280 mV at 10 mA/cm², substantially outperforming both IrO2 (320 mV) and RuO2 (310 mV) benchmark catalysts."},
         "confidence": 0.93},
        {"type": "relation", "relation_id": "doc_xxx_r2", "head": "John Dickinson",
         "relation_type": "creates", "relation_surface": "drafted",
         "tail": "Articles of Confederation",
         "evidence": {"section": "The 1776 Articles of Confederation",
             "original_text": "Although Dickinson wrote the Articles of Confederation for the nation, he did so with an eye toward the increasing anti-Quaker sentiment in Pennsylvania. Not wanting independence, but in preparation for it, he took the lead immediately before the Declaration in writing the Articles. The document that was submitted to Congress on July 12 was originally written by Dickinson, and then revised by him according to the critiques of his colleagues. He was one of a committee of thirteen that included, among others, Josiah Bartlett, Edward Rutledge, Samuel Adams, and Thomas McKean. The main issue in framing an American constitution was similar to the question of the relation of the colonies to the British constitution – the power of the states in relation to the central government. Dickinson was not alone in his concern for such a power, but he was one of the most consistent advocates of it."},
         "confidence": 0.95},
        {"type": "relation", "relation_id": "doc_xxx_r3", "head": "Quaker political theology",
         "relation_type": "influences", "relation_surface": "shaped the design of",
         "tail": "Articles of Confederation",
         "evidence": {"section": "The 1776 Articles of Confederation",
             "original_text": "There are several proposals in the Dickinson Plan that scholars consider innovative. Among the most notable of his contributions are the provisions for a powerful central government and religious liberty. These may have been exceptional when compared to the work and thought of other Founders, but most were standard in the context of Quaker political thought and practice. His constant equation of liberty with safety led to his presidency of the Annapolis Convention. The ideals he espoused in his version of the Articles of Confederation and in Pennsylvania government represented Quaker concerns."},
         "confidence": 0.91},
        {"type": "relation", "relation_id": "doc_xxx_r4", "head": "high-entropy alloy",
         "relation_type": "compares", "relation_surface": "outperforms",
         "tail": "IrO2 catalyst",
         "evidence": {"section": "Results",
             "original_text": "The FeCoNiCrMn HEA exhibits a low overpotential of 280 mV at 10 mA/cm², outperforming IrO2 (320 mV) and RuO2 (310 mV). This represents a 12.5% and 9.7% improvement respectively. The Tafel slope of 45 mV/dec indicates favorable reaction kinetics compared to 68 mV/dec for pure Co3O4. The mass activity at 1.53 V was 120 A/g, which is approximately three-fold higher than that of IrO2 (40 A/g)."},
         "confidence": 0.97},
    ]
}
GR_OUTPUT_EXAMPLE_JSON = json.dumps(GR_OUTPUT_EXAMPLE, ensure_ascii=False, indent=2)

GR_SYSTEM_PROMPT = """你是一位学术文献知识抽取专家。从论文原文中独立提取概念之间的语义关系，覆盖自然科学、社会科学、人文学科等所有学科领域。不依赖任何预提取的概念列表，head/tail 直接从原文中识别。

## 输出格式

```json
{output_example}
```

## 知识类型

**relation**: 概念间语义关系
- head/tail: 直接从原文中识别的术语（优先使用论文中的标准术语形式）
- relation_type: enhances | inhibits | causes | creates | influences | belongs_to | measures | uses | compares | precedes | derives
- relation_surface: 原文中表达关系的短语

## 关系提取方法

1. **通读全文**：识别论文中的核心实体、方法、现象、性质、思想、人物、制度、事件
2. **寻找语义连接**：对每一对相关的实体，判断它们之间的语义关系类型
3. **从原文提取证据**：每对关系必须有原文段落支撑

## 关系提取要求

1. **数量硬指标**：每篇论文必须输出 15-30 条高质量关系，不能少于 15 条。人文社科学科论文同样需要提取充足的关系
2. **全学科关系类型**：
   - causes: A 导致/引起 B
   - inhibits: A 抑制/阻碍 B
   - enhances: A 增强/提升/促进 B
   - creates: A 创建/撰写/制定 B（论文、法律、制度、作品等）
   - influences: A 影响/塑造 B（思想、政策、事件等）
   - belongs_to: A 是 B 的一部分/子类/归属于 B
   - measures: A 用于测量/评估 B
   - uses: A 使用/利用 B（方法、工具、数据、文献）
   - compares: A 与 B 进行比较/对比
   - precedes: A 在 B 之前发生（时间先后、历史先后）
   - derives: A 从 B 推导/派生/继承（思想渊源、理论衍生、制度传承）
3. **类型均衡**：enhances/inhibits/causes/creates/influences 等强语义关系占 50%+，belongs_to 不超过 30%。人文社科论文多用 creates/influences/derives/precedes
4. **关系密度**：每个核心实体至少参与 2-3 条关系，不同关系类型交错使用
5. **evidence 必须充分**：每条 relation 至少 5-8 句原文摘录，明确展示 head 和 tail 的语义连接

## 提取原则

1. **忠实性**：original_text 必须是原文直接摘录，禁止改写
2. **完整性**：扫描全文，不遗漏任何明确的语义关系
3. **覆盖度**：输出 token 上限 12000，关系类型全覆盖
4. **防幻觉**：如果论文正文缺乏实质学术内容（如通篇只有版权声明、下载信息），返回空列表 {{\"entries\": []}}，禁止从版权/出版信息中编造关系"""

GR_USER_PROMPT = """## 论文内容

{paper_text}

请从论文原文中独立提取概念间语义关系（15-30条），不依赖预提取的概念列表，直接从文本中识别实体和关系。"""

# ─── Group2: dataset + data_specification ─────────────────

G2_OUTPUT_EXAMPLE = {
    "entries": [
        {"type": "dataset", "dataset_id": "doc_xxx_d1", "name": "OER activity benchmark", "modality": "tabular", "domain": "electrochemistry",
         "evidence": {"section": "Results",
             "original_text": "We benchmark our HEA against state-of-the-art OER catalysts including IrO2 and RuO2. The benchmark dataset comprises overpotential measurements at 10 mA/cm² from 15 independently synthesized electrodes. All measurements were repeated in triplicate to ensure statistical reliability. The dataset also includes Tafel slope values derived from linear sweep voltammetry at a scan rate of 5 mV/s. Electrochemical impedance spectroscopy data were collected at frequencies ranging from 100 kHz to 0.1 Hz with a 10 mV AC amplitude. Chronopotentiometry stability data were recorded at a constant current density of 10 mA/cm² for 24 hours."},
         "confidence": 0.90},
        {"type": "data_specification", "ds_id": "doc_xxx_ds1", "spec_type": "quality_standard",
         "description": "Standardized three-electrode measurement protocol with iR compensation and RHE calibration",
         "evidence": {"section": "Methods",
             "original_text": "All electrochemical measurements were performed at 298 K under ambient pressure using a standard three-electrode configuration with a graphite counter electrode and Ag/AgCl reference electrode. All potentials were iR-corrected and calibrated to the reversible hydrogen electrode (RHE) scale. The working electrode was prepared by drop-casting a catalyst ink composed of 5 mg catalyst, 1 mL isopropanol, and 10 μL Nafion onto a glassy carbon rotating disk electrode. The electrolyte was 1 M KOH solution purged with high-purity O2 for at least 30 minutes prior to each experiment. The uncompensated resistance was determined by electrochemical impedance spectroscopy at open circuit potential and compensated at 85% level."},
         "confidence": 0.95},
    ]
}
G2_OUTPUT_EXAMPLE_JSON = json.dumps(G2_OUTPUT_EXAMPLE, ensure_ascii=False, indent=2)

G2_SYSTEM_PROMPT = """你是一位学术文献知识抽取专家。从学术论文中提取数据集和数据规范，覆盖所有学科领域。

## 输出格式

```json
{output_example}
```

## 知识类型

1. **dataset**: 论文使用/产生的数据集
   - name: 数据集名称
   - modality: 数据模态（可选：text | image | tabular | time_series | multimodal | other）
   - domain: 所属领域（可选）
2. **data_specification**: 数据格式规范、质量标准、环境要求
   - spec_type: format_rule | quality_standard | env_requirement | metadata_standard
   - description: 一句话摘要（可选）

## 提取原则

1. **忠实性**：original_text 必须是原文直接摘录，禁止改写
2. **完整性**：original_text 必须是完整段落，至少包含 8-10 个完整句子，保留充足上下文
3. **覆盖度**：原文中存在的全部提取。输出 token 上限8000，两种类型均衡。切忌编造"""

G2_USER_PROMPT = """## 论文内容

{paper_text}

请提取数据集和数据规范。"""

# ─── Group3: method + experiment ──────────────────────────

G3_OUTPUT_EXAMPLE = {
    "entries": [
        {"type": "method", "method_id": "doc_xxx_m1", "name": "Raman spectroscopy", "method_type": "instrument",
         "evidence": {"section": "Methods",
             "original_text": "We analyzed the catalyst surface using Raman spectroscopy following the i-t tests in both KOH and TMAOH solutions. Spectra were collected using a 532 nm excitation laser with 5 mW power, integrating 10 scans of 30 seconds each to achieve adequate signal-to-noise ratio. The laser spot size was approximately 1 μm in diameter, allowing spatially resolved mapping of the electrode surface. Baseline correction was performed using a cubic spline interpolation, and peak fitting was carried out with Lorentzian functions. The spectrometer was calibrated using a silicon standard (520.7 cm⁻¹) before each measurement session."},
         "confidence": 0.95},
        {"type": "experiment", "experiment_id": "doc_xxx_x1", "task": "electrochemical stability test",
         "setup": "three-electrode cell, 1M KOH, 298 K, glassy carbon RDE at 1600 rpm",
         "evidence": {"section": "Methods",
             "original_text": "All electrochemical measurements were performed at 298 K under ambient pressure using a standard three-electrode configuration. Chronoamperometry was conducted at a constant potential of 1.53 V vs RHE for 24 hours. The electrolyte was continuously purged with O2 to maintain saturation, and the working electrode rotation speed was fixed at 1600 rpm. The catalyst loading on the glassy carbon electrode was precisely controlled at 0.2 mg/cm² to ensure reproducible measurements. Three independent electrodes were prepared for each catalyst composition to assess batch-to-batch reproducibility."},
         "confidence": 0.95},
    ]
}
G3_OUTPUT_EXAMPLE_JSON = json.dumps(G3_OUTPUT_EXAMPLE, ensure_ascii=False, indent=2)

G3_SYSTEM_PROMPT = """你是一位学术文献知识抽取专家。从学术论文中提取方法和实验信息，覆盖所有学科领域（自然科学实验、社会科学调查、人文学科研究方法等）。

## 输出格式

```json
{output_example}
```

## 知识类型

1. **method**: 方法、模型、算法、仪器
   - name: 方法/模型名称
   - method_type: model | algorithm | protocol | software | instrument | preprocessing
2. **experiment**: 实验设置与流程
   - task: 实验任务/名称
   - setup: 实验条件摘要（可选，一句话概括设备、环境、关键参数）

## 提取原则

1. **忠实性**：original_text 必须是原文直接摘录，禁止改写
2. **完整性**：original_text 必须是完整段落，至少包含 8-10 个完整句子，保留充足上下文
3. **覆盖度**：原文中存在的全部提取。输出 token 上限8000，两种类型均衡。切忌编造"""

G3_USER_PROMPT = """## 论文内容

{paper_text}

请提取方法和实验信息。"""

# ─── Group4: quantitative_result + performance_result ─────

G4_OUTPUT_EXAMPLE = {
    "entries": [
        {"type": "quantitative_result", "qr_id": "doc_xxx_qr1", "quantity": "overpotential", "value": 280, "unit": "mV", "context": "FeCoNiCrMn HEA at 10 mA/cm² current density in 1M KOH",
         "result_type": "main_result",
         "evidence": {"section": "Results",
             "original_text": "The FeCoNiCrMn HEA exhibits a low overpotential of 280 mV at 10 mA/cm², outperforming IrO2 (320 mV) and RuO2 (310 mV). Chronoamperometry measurements at a constant potential of 1.53 V vs RHE confirmed stable current density over 24 hours of continuous operation with less than 3% degradation. The polarization curve was recorded at a slow scan rate of 2 mV/s to minimize capacitive contributions. The overpotential values were determined at the geometric current density of 10 mA/cm², which corresponds to approximately 10% efficient solar-to-fuel conversion. Error bars represent the standard deviation from measurements on five independent electrodes. The improvement is attributed to the multi-path electron transfer mechanism facilitated by configurational entropy."},
         "confidence": 0.97},
        {"type": "performance_result", "perf_id": "doc_xxx_p1",
         "metric": "overpotential at 10 mA/cm²", "compared_to": "IrO2, RuO2",
         "evidence": {"section": "Results",
             "original_text": "The FeCoNiCrMn HEA exhibits a low overpotential of 280 mV at 10 mA/cm², outperforming IrO2 (320 mV) and RuO2 (310 mV). This represents a 12.5% and 9.7% improvement respectively. The Tafel slope of 45 mV/dec indicates favorable reaction kinetics compared to 68 mV/dec for pure Co3O4. The mass activity of the HEA catalyst at 1.53 V was 120 A/g, which is approximately 3-fold higher than that of IrO2 (40 A/g). Electrochemical impedance spectroscopy revealed a charge transfer resistance of 12 Ω for the HEA, significantly lower than 38 Ω for IrO2, confirming faster electron transfer kinetics at the electrode-electrolyte interface."},
         "confidence": 0.95},
    ]
}
G4_OUTPUT_EXAMPLE_JSON = json.dumps(G4_OUTPUT_EXAMPLE, ensure_ascii=False, indent=2)

G4_SYSTEM_PROMPT = """你是一位学术文献知识抽取专家。从学术论文中提取量化结果和性能对比，覆盖所有学科领域。

## 输出格式

```json
{output_example}
```

## 知识类型

1. **quantitative_result**: 科学测量、实验指标、性能数据（排除年份、页数、编号等元信息）
   - quantity: 物理量/指标名称
   - value: 数值（可选）
   - unit: 单位（可选）
   - context: 实验条件/上下文（必填）
   - result_type: main_result | baseline | ablation | measurement | threshold
2. **performance_result**: 性能对比/评价
   - metric: 被比较的指标名称（可选）
   - compared_to: 对比对象（可选）

## 提取原则

1. **证据完整性（最重要）**：每条 evidence 必须是 8-10 句完整段落，保留所有实验条件、对比基准、统计细节。字段（quantity/value/unit/context）只是索引标签，不能替代 evidence。即使量化字段已经概括了结果，evidence 仍必须包含完整原文摘录
2. **忠实性**：original_text 必须是原文直接摘录，禁止改写
3. **质量优先于数量**：只提取有科学意义的测量（排除年份、页数、编号等元信息）。宁可少几条，也要保证每条 evidence 足够详细
4. **覆盖度**：输出 token 上限8000，两种类型均衡"""

G4_USER_PROMPT = """## 论文内容

{paper_text}

请提取量化结果和性能对比。"""

# ─── Group5: conclusion + claim + limitation + future_work ─

G5_OUTPUT_EXAMPLE = {
    "entries": [
        {"type": "claim", "claim_id": "doc_xxx_ca1",
         "evidence": {"section": "Abstract",
             "original_text": "Configurational entropy in high-entropy alloys synergistically activates multiple electron transfer pathways, enabling superior OER performance. This finding challenges the conventional wisdom that catalytic activity is solely determined by the electronic structure of individual active sites. The multi-path electron transfer mechanism provides a new paradigm for designing next-generation electrocatalysts. By incorporating multiple transition metal elements into a single-phase solid solution, the catalyst can exploit a continuum of electronic states to facilitate charge transfer at the electrode-electrolyte interface."},
         "confidence": 0.96},
        {"type": "conclusion", "conclusion_id": "doc_xxx_cl1",
         "evidence": {"section": "Conclusion",
             "original_text": "High-entropy alloys enable multi-path electron transfer to synergistically activate the lattice oxygen mechanism, providing a new design strategy for efficient OER catalysts. These results demonstrate that configurational complexity can be harnessed as a design parameter to overcome the activity-stability tradeoff that has long plagued OER catalyst development. The FeCoNiCrMn system serves as a model platform, but the design principles established here are generalizable to other HEA compositions. Our work establishes a direct link between configurational entropy, electronic structure, and catalytic activity."},
         "confidence": 0.96},
        {"type": "limitation", "limitation_id": "doc_xxx_lm1",
         "evidence": {"section": "Discussion",
             "original_text": "The current study is limited to five-component HEAs in alkaline media; generalizability to acid-stable compositions remains to be demonstrated. Furthermore, the long-term stability beyond 24 hours and the performance under practical device conditions have not been evaluated. The DFT calculations were performed on idealized slab models that do not capture surface reconstruction effects under operating potentials. Additionally, the precise contributions of individual elements to the overall activity cannot be deconvoluted from the present experimental data alone."},
         "confidence": 0.92},
        {"type": "future_work", "future_work_id": "doc_xxx_fw1",
         "evidence": {"section": "Discussion",
             "original_text": "Future studies should explore HEAs in other electrocatalytic reactions such as CO2 reduction and nitrogen reduction. Additionally, machine learning-guided composition screening could accelerate the discovery of optimal HEA formulations. In-situ/operando characterization techniques such as X-ray absorption spectroscopy are needed to directly probe the multi-path electron transfer mechanism under working conditions. Long-term durability testing under industrially relevant current densities exceeding 500 mA/cm² is essential to assess practical viability."},
         "confidence": 0.93},
    ]
}
G5_OUTPUT_EXAMPLE_JSON = json.dumps(G5_OUTPUT_EXAMPLE, ensure_ascii=False, indent=2)

G5_SYSTEM_PROMPT = """你是一位学术文献知识抽取专家。从学术论文中提取核心主张、结论、局限性和未来工作，覆盖所有学科领域。

## 输出格式

```json
{output_example}
```

## 知识类型

1. **claim**: 核心主张/发现 — 仅需 evidence
2. **conclusion**: 核心结论 — 仅需 evidence
3. **limitation**: 方法局限、适用约束 — 仅需 evidence
4. **future_work**: 未来研究方向 — 仅需 evidence

## 提取原则

1. **忠实性**：original_text 必须是原文直接摘录，禁止改写
2. **完整性**：original_text 必须是完整段落，至少包含 8-10 个完整句子，保留充足上下文
3. **区分度**：claim 是核心论点/假设，conclusion 是经实验验证后的结论
4. **覆盖度**：原文有的全部提取，没有的不编造。四种类型均衡。输出 token 上限8000"""

G5_USER_PROMPT = """## 论文内容

{paper_text}

请提取核心主张、结论、局限性和未来工作。"""

# ─── 分组 Prompt 构建函数（预设组合）──────────────────────────────

def build_grouped_extraction_prompts(paper_text: str) -> list[tuple[str, str, str]]:
    """构建 5 组预设分类型提取 prompt，返回 [(group_name, sys_prompt, user_prompt), ...]"""
    return [
        ("G1_concept_relation",
         G1_SYSTEM_PROMPT.format(output_example=G1_OUTPUT_EXAMPLE_JSON),
         G1_USER_PROMPT.format(paper_text=paper_text)),
        ("G2_dataset_spec",
         G2_SYSTEM_PROMPT.format(output_example=G2_OUTPUT_EXAMPLE_JSON),
         G2_USER_PROMPT.format(paper_text=paper_text)),
        ("G3_method_experiment",
         G3_SYSTEM_PROMPT.format(output_example=G3_OUTPUT_EXAMPLE_JSON),
         G3_USER_PROMPT.format(paper_text=paper_text)),
        ("G4_quant_perf",
         G4_SYSTEM_PROMPT.format(output_example=G4_OUTPUT_EXAMPLE_JSON),
         G4_USER_PROMPT.format(paper_text=paper_text)),
        ("G5_insight_outlook",
         G5_SYSTEM_PROMPT.format(output_example=G5_OUTPUT_EXAMPLE_JSON),
         G5_USER_PROMPT.format(paper_text=paper_text)),
    ]


def build_concept_prompt(paper_text: str) -> tuple[str, str, str]:
    """构建仅概念提取 prompt"""
    return (
        "GC_concept",
        GC_SYSTEM_PROMPT.format(output_example=GC_OUTPUT_EXAMPLE_JSON),
        GC_USER_PROMPT.format(paper_text=paper_text),
    )


def build_relation_prompt(paper_text: str) -> tuple[str, str, str]:
    """构建仅关系提取 prompt（独立从原文提取，不依赖概念列表）"""
    return (
        "GR_relation",
        GR_SYSTEM_PROMPT.format(output_example=GR_OUTPUT_EXAMPLE_JSON),
        GR_USER_PROMPT.format(paper_text=paper_text),
    )


# ═══════════════════════════════════════════════════════════════
#  独立 Prompt：12 种知识类型各有一个专属 prompt
#  自由分组时，单类型直接用专属 prompt，多类型合并
# ═══════════════════════════════════════════════════════════════

# ─── 1. concept（独立）────────────────────────────────────────

def build_concept_solo_prompt(paper_text: str) -> tuple[str, str, str]:
    return build_concept_prompt(paper_text)


# ─── 2. relation（独立）───────────────────────────────────────

def build_relation_solo_prompt(paper_text: str) -> tuple[str, str, str]:
    return build_relation_prompt(paper_text)


# ─── 3. dataset ──────────────────────────────────────────────

D_DATASET_EXAMPLE = {
    "entries": [
        {"type": "dataset", "dataset_id": "doc_xxx_d1", "name": "OER activity benchmark",
         "modality": "tabular", "domain": "electrochemistry",
         "evidence": {"section": "Results",
             "original_text": "We benchmark our HEA against state-of-the-art OER catalysts including IrO2 and RuO2. The benchmark dataset comprises overpotential measurements at 10 mA/cm² from 15 independently synthesized electrodes. All measurements were repeated in triplicate to ensure statistical reliability. The dataset also includes Tafel slope values derived from linear sweep voltammetry at a scan rate of 5 mV/s. Electrochemical impedance spectroscopy data were collected at frequencies ranging from 100 kHz to 0.1 Hz with a 10 mV AC amplitude. Chronopotentiometry stability data were recorded at a constant current density of 10 mA/cm² for 24 hours."},
         "confidence": 0.90},
    ]
}
D_DATASET_EXAMPLE_JSON = json.dumps(D_DATASET_EXAMPLE, ensure_ascii=False, indent=2)

D_DATASET_SYS = """你是一位学术文献知识抽取专家。从学术论文中提取数据集信息，覆盖所有学科领域。

## 输出格式

```json
{output_example}
```

## 知识类型

**dataset**: 论文使用/产生的数据集
- name: 数据集名称（必填）
- modality: 数据模态 text | image | tabular | time_series | multimodal | other
- domain: 所属领域

## 提取要求

1. 不仅提取命名的公开数据集，也要提取论文中描述的数据集合（如"15个独立合成的电极的过电位测量数据"）
2. 社会科学论文中的调查样本、访谈记录、档案汇编等同样视为 dataset
3. modality 根据数据性质选择：问卷调查→tabular，文本档案→text，地理信息→image 等

## 提取原则

1. **忠实性**：original_text 必须是原文直接摘录，禁止改写
2. **完整性**：每条 evidence 至少 8-10 个完整句子，保留充足上下文
3. **覆盖度**：原文中存在的全部提取。输出 token 上限 4000
4. **防幻觉**：如论文没有使用或产生数据集，返回空列表 {{\"entries\": []}}"""

D_DATASET_USR = """## 论文内容

{paper_text}

请提取论文中使用或产生的数据集。"""


def build_dataset_prompt(paper_text: str) -> tuple[str, str, str]:
    return ("GD_dataset",
            D_DATASET_SYS.format(output_example=D_DATASET_EXAMPLE_JSON),
            D_DATASET_USR.format(paper_text=paper_text))


# ─── 4. data_specification ──────────────────────────────────

D_DS_EXAMPLE = {
    "entries": [
        {"type": "data_specification", "ds_id": "doc_xxx_ds1", "spec_type": "quality_standard",
         "description": "Standardized three-electrode measurement protocol with iR compensation and RHE calibration",
         "evidence": {"section": "Methods",
             "original_text": "All electrochemical measurements were performed at 298 K under ambient pressure using a standard three-electrode configuration with a graphite counter electrode and Ag/AgCl reference electrode. All potentials were iR-corrected and calibrated to the reversible hydrogen electrode (RHE) scale. The working electrode was prepared by drop-casting a catalyst ink onto a glassy carbon rotating disk electrode. The electrolyte was 1 M KOH solution prepared with ultrapure water (18.2 MΩ·cm) and purged with high-purity O2 for 30 minutes prior to each experiment. The uncompensated resistance was determined by electrochemical impedance spectroscopy at open circuit potential and compensated at 85% level."},
         "confidence": 0.95},
    ]
}
D_DS_EXAMPLE_JSON = json.dumps(D_DS_EXAMPLE, ensure_ascii=False, indent=2)

D_DS_SYS = """你是一位学术文献知识抽取专家。从学术论文中提取数据规范和质量标准信息，覆盖所有学科领域。

## 输出格式

```json
{output_example}
```

## 知识类型

**data_specification**: 数据格式规范、质量标准、环境要求
- spec_type: format_rule | quality_standard | env_requirement | metadata_standard
- description: 一句话摘要

## 提取要求

1. 提取论文中明确定义的数据格式要求、实验环境标准、质量门槛、元数据标准
2. 社会科学中的抽样标准、访谈协议、编码规范等同样视为 data_specification
3. spec_type 根据具体内容选择：实验环境条件→env_requirement，数据处理规则→format_rule，质量门槛→quality_standard

## 提取原则

1. **忠实性**：original_text 必须是原文直接摘录，禁止改写
2. **完整性**：每条 evidence 至少 8-10 个完整句子，保留充足上下文
3. **覆盖度**：原文中存在的全部提取。输出 token 上限 4000
4. **防幻觉**：如论文没有明确的数据规范，返回空列表 {{\"entries\": []}}"""

D_DS_USR = """## 论文内容

{paper_text}

请提取论文中的数据规范和质量标准。"""


def build_data_specification_prompt(paper_text: str) -> tuple[str, str, str]:
    return ("GDS_data_specification",
            D_DS_SYS.format(output_example=D_DS_EXAMPLE_JSON),
            D_DS_USR.format(paper_text=paper_text))


# ─── 5. method ──────────────────────────────────────────────

D_METHOD_EXAMPLE = {
    "entries": [
        {"type": "method", "method_id": "doc_xxx_m1", "name": "Raman spectroscopy",
         "method_type": "instrument",
         "evidence": {"section": "Methods",
             "original_text": "We analyzed the catalyst surface using Raman spectroscopy following the i-t tests in both KOH and TMAOH solutions. Spectra were collected using a 532 nm excitation laser with 5 mW power, integrating 10 scans of 30 seconds each to achieve adequate signal-to-noise ratio. The laser spot size was approximately 1 μm in diameter, allowing spatially resolved mapping of the electrode surface. Baseline correction was performed using a cubic spline interpolation, and peak fitting was carried out with Lorentzian functions. The spectrometer was calibrated using a silicon standard (520.7 cm⁻¹) before each measurement session."},
         "confidence": 0.95},
    ]
}
D_METHOD_EXAMPLE_JSON = json.dumps(D_METHOD_EXAMPLE, ensure_ascii=False, indent=2)

D_METHOD_SYS = """你是一位学术文献知识抽取专家。从学术论文中提取方法和研究手段，覆盖所有学科领域（自然科学实验技术、社会科学调查方法、人文学科分析手段等）。

## 输出格式

```json
{output_example}
```

## 知识类型

**method**: 方法、模型、算法、仪器、研究手段
- name: 方法/模型/技术名称
- method_type: model | algorithm | protocol | software | instrument | preprocessing | field_research | textual_analysis | survey | interview

## method_type 选择指南

| 学科 | 常见类型 | 示例 |
|------|---------|------|
| 自然科学 | instrument, protocol, model, algorithm | electron microscopy, PCR protocol, DFT |
| 社会科学 | survey, interview, field_research | questionnaire survey, structured interview, participant observation |
| 人文学科 | textual_analysis | close reading, discourse analysis, archival research |
| 通用 | software, preprocessing | SPSS, Python, data cleaning |

## 提取原则

1. **忠实性**：original_text 必须是原文直接摘录，禁止改写
2. **完整性**：每条 evidence 至少 8-10 个完整句子，保留充足上下文
3. **覆盖度**：原文中使用的所有方法全部提取。输出 token 上限 4000
4. **防幻觉**：如论文缺乏实质方法描述，返回空列表 {{\"entries\": []}}"""

D_METHOD_USR = """## 论文内容

{paper_text}

请提取论文中使用的研究方法和研究手段。"""


def build_method_prompt(paper_text: str) -> tuple[str, str, str]:
    return ("GM_method",
            D_METHOD_SYS.format(output_example=D_METHOD_EXAMPLE_JSON),
            D_METHOD_USR.format(paper_text=paper_text))


# ─── 6. experiment ──────────────────────────────────────────

D_EXPERIMENT_EXAMPLE = {
    "entries": [
        {"type": "experiment", "experiment_id": "doc_xxx_x1", "task": "electrochemical stability test",
         "setup": "three-electrode cell, 1M KOH, 298 K, glassy carbon RDE at 1600 rpm",
         "evidence": {"section": "Methods",
             "original_text": "All electrochemical measurements were performed at 298 K under ambient pressure using a standard three-electrode configuration. Chronoamperometry was conducted at a constant potential of 1.53 V vs RHE for 24 hours. The electrolyte was continuously purged with O2 to maintain saturation, and the working electrode rotation speed was fixed at 1600 rpm. The catalyst loading on the glassy carbon electrode was precisely controlled at 0.2 mg/cm² to ensure reproducible measurements. Three independent electrodes were prepared for each catalyst composition to assess batch-to-batch reproducibility."},
         "confidence": 0.95},
    ]
}
D_EXPERIMENT_EXAMPLE_JSON = json.dumps(D_EXPERIMENT_EXAMPLE, ensure_ascii=False, indent=2)

D_EXPERIMENT_SYS = """你是一位学术文献知识抽取专家。从学术论文中提取实验和研究流程信息，覆盖所有学科领域（自然科学实验、社会科学调查设计、人文学科研究框架等）。

## 输出格式

```json
{output_example}
```

## 知识类型

**experiment**: 研究设计、实验设置、调查流程
- task: 研究任务/实验名称
- setup: 实验条件/研究设置摘要（一句话概括关键参数、环境、样本等）

## 提取要求

1. 自然科学论文：提取每个独立实验的设置（温度、压力、设备、样本量等）
2. 社会科学论文：提取调查设计、问卷结构、访谈流程等
3. 人文学科论文：提取研究框架、分析路径、史料范围等

## 提取原则

1. **忠实性**：original_text 必须是原文直接摘录，禁止改写
2. **完整性**：每条 evidence 至少 8-10 个完整句子，保留充足上下文
3. **覆盖度**：原文中描述的每个研究/实验流程全部提取。输出 token 上限 4000
4. **防幻觉**：如论文缺乏实质研究设计描述，返回空列表 {{\"entries\": []}}"""

D_EXPERIMENT_USR = """## 论文内容

{paper_text}

请提取论文中的研究设计和实验流程。"""


def build_experiment_prompt(paper_text: str) -> tuple[str, str, str]:
    return ("GX_experiment",
            D_EXPERIMENT_SYS.format(output_example=D_EXPERIMENT_EXAMPLE_JSON),
            D_EXPERIMENT_USR.format(paper_text=paper_text))


# ─── 7. quantitative_result ─────────────────────────────────

D_QR_EXAMPLE = {
    "entries": [
        {"type": "quantitative_result", "qr_id": "doc_xxx_qr1",
         "quantity": "overpotential", "value": 280, "unit": "mV",
         "context": "FeCoNiCrMn HEA at 10 mA/cm² current density in 1M KOH",
         "result_type": "main_result",
         "evidence": {"section": "Results",
             "original_text": "The FeCoNiCrMn HEA exhibits a low overpotential of 280 mV at 10 mA/cm², outperforming IrO2 (320 mV) and RuO2 (310 mV). Chronoamperometry measurements at a constant potential of 1.53 V vs RHE confirmed stable current density over 24 hours of continuous operation with less than 3% degradation. The polarization curve was recorded at a slow scan rate of 2 mV/s to minimize capacitive contributions. The overpotential values were determined at the geometric current density of 10 mA/cm². Error bars represent the standard deviation from measurements on five independent electrodes."},
         "confidence": 0.97},
    ]
}
D_QR_EXAMPLE_JSON = json.dumps(D_QR_EXAMPLE, ensure_ascii=False, indent=2)

D_QR_SYS = """你是一位学术文献知识抽取专家。从学术论文中提取量化结果和科学度量，覆盖所有学科领域。

## 输出格式

```json
{output_example}
```

## 知识类型

**quantitative_result**: 科学测量、实验指标、统计数据（排除年份、页数、编号等元信息）
- quantity: 物理量/指标名称（必填）
- value: 数值
- unit: 单位
- context: 实验条件/上下文（必填，说明是在什么条件下得到的该数值）
- result_type: main_result | baseline | ablation | measurement | threshold

## 提取要求

1. 提取论文中所有有科学意义的具体数值结果，包括图表中报告的数据
2. 社会科学论文中的统计数据（样本量、相关系数、p值、效应量等）同样提取
3. 必须包含 context 字段，说明该数值的完整实验/研究背景

## 提取原则

1. **证据完整性（最重要）**：每条 evidence 必须是 8-10 句完整段落，保留所有实验条件、对比基准、统计细节。字段（quantity/value/unit/context）只是索引标签，不能替代 evidence
2. **忠实性**：original_text 必须是原文直接摘录，禁止改写
3. **质量优先于数量**：只提取有科学意义的测量（排除年份、页数、编号等元信息）。宁可少几条，也要保证每条 evidence 足够详细
4. **覆盖度**：输出 token 上限 4000"""

D_QR_USR = """## 论文内容

{paper_text}

请提取论文中的量化结果和科学度量。"""


def build_quantitative_result_prompt(paper_text: str) -> tuple[str, str, str]:
    return ("GQR_quantitative_result",
            D_QR_SYS.format(output_example=D_QR_EXAMPLE_JSON),
            D_QR_USR.format(paper_text=paper_text))


# ─── 8. performance_result ──────────────────────────────────

D_PERF_EXAMPLE = {
    "entries": [
        {"type": "performance_result", "perf_id": "doc_xxx_p1",
         "metric": "overpotential at 10 mA/cm²", "compared_to": "IrO2, RuO2",
         "evidence": {"section": "Results",
             "original_text": "The FeCoNiCrMn HEA exhibits a low overpotential of 280 mV at 10 mA/cm², outperforming IrO2 (320 mV) and RuO2 (310 mV). This represents a 12.5% and 9.7% improvement respectively. The Tafel slope of 45 mV/dec indicates favorable reaction kinetics compared to 68 mV/dec for pure Co3O4. The mass activity of the HEA catalyst at 1.53 V was 120 A/g, which is approximately 3-fold higher than that of IrO2 (40 A/g). Electrochemical impedance spectroscopy revealed a charge transfer resistance of 12 Ω for the HEA, significantly lower than 38 Ω for IrO2."},
         "confidence": 0.95},
    ]
}
D_PERF_EXAMPLE_JSON = json.dumps(D_PERF_EXAMPLE, ensure_ascii=False, indent=2)

D_PERF_SYS = """你是一位学术文献知识抽取专家。从学术论文中提取性能对比和评价信息，覆盖所有学科领域。

## 输出格式

```json
{output_example}
```

## 知识类型

**performance_result**: 性能对比/方法评价/优劣比较
- metric: 被比较的指标名称
- compared_to: 对比对象/基准方法

## 提取要求

1. 不仅提取数值对比，也要提取定性评价（如"A 优于 B"、"方法 X 比 Y 更有效"）
2. 社会科学论文中的方法对比、政策效果对比等同样视为 performance_result

## 提取原则

1. **忠实性**：original_text 必须是原文直接摘录，禁止改写
2. **完整性**：每条 evidence 至少 8-10 个完整句子，保留充足上下文
3. **覆盖度**：原文中存在的全部提取。输出 token 上限 4000
4. **防幻觉**：如论文没有性能对比或方法评价，返回空列表 {{\"entries\": []}}"""

D_PERF_USR = """## 论文内容

{paper_text}

请提取论文中的性能对比和方法评价。"""


def build_performance_result_prompt(paper_text: str) -> tuple[str, str, str]:
    return ("GP_performance_result",
            D_PERF_SYS.format(output_example=D_PERF_EXAMPLE_JSON),
            D_PERF_USR.format(paper_text=paper_text))


# ─── 9. claim ───────────────────────────────────────────────

D_CLAIM_EXAMPLE = {
    "entries": [
        {"type": "claim", "claim_id": "doc_xxx_ca1",
         "evidence": {"section": "Abstract",
             "original_text": "Configurational entropy in high-entropy alloys synergistically activates multiple electron transfer pathways, enabling superior OER performance. This finding challenges the conventional wisdom that catalytic activity is solely determined by the electronic structure of individual active sites. The multi-path electron transfer mechanism provides a new paradigm for designing next-generation electrocatalysts. By incorporating multiple transition metal elements into a single-phase solid solution, the catalyst can exploit a continuum of electronic states to facilitate charge transfer at the electrode-electrolyte interface. This concept extends beyond OER catalysis and may be broadly applicable to other multi-electron reactions such as CO2 reduction and nitrogen fixation."},
         "confidence": 0.96},
    ]
}
D_CLAIM_EXAMPLE_JSON = json.dumps(D_CLAIM_EXAMPLE, ensure_ascii=False, indent=2)

D_CLAIM_SYS = """你是一位学术文献知识抽取专家。从学术论文中提取核心主张和发现，覆盖所有学科领域。

## 输出格式

```json
{output_example}
```

## 知识类型

**claim**: 核心主张/核心发现
- 仅需 evidence，无额外标注字段
- 是论文的核心论点或假设性主张（可能尚未被完全验证）

## 提取要求

1. claim 是论文的"中心论点"——作者试图论证或说服读者的核心命题
2. 区分 claim 和 conclusion：claim 是论据/主张/假设，conclusion 是经过验证后的结论
3. 人文社科论文中，claim 包括作者的理论立场、解释框架、核心论点
4. 一篇论文通常有 1-5 个核心 claim

## 提取原则

1. **忠实性**：original_text 必须是原文直接摘录，禁止改写
2. **完整性**：每条 evidence 至少 8-10 个完整句子，保留充足上下文
3. **覆盖度**：只提取作者明确表达的核心理念主张。输出 token 上限 4000"""

D_CLAIM_USR = """## 论文内容

{paper_text}

请提取论文的核心主张和中心论点。"""


def build_claim_prompt(paper_text: str) -> tuple[str, str, str]:
    return ("GCA_claim",
            D_CLAIM_SYS.format(output_example=D_CLAIM_EXAMPLE_JSON),
            D_CLAIM_USR.format(paper_text=paper_text))


# ─── 10. conclusion ─────────────────────────────────────────

D_CONCLUSION_EXAMPLE = {
    "entries": [
        {"type": "conclusion", "conclusion_id": "doc_xxx_cl1",
         "evidence": {"section": "Conclusion",
             "original_text": "High-entropy alloys enable multi-path electron transfer to synergistically activate the lattice oxygen mechanism, providing a new design strategy for efficient OER catalysts. These results demonstrate that configurational complexity can be harnessed as a design parameter to overcome the activity-stability tradeoff that has long plagued OER catalyst development. The FeCoNiCrMn system serves as a model platform, but the design principles established here are generalizable to other HEA compositions. Our work establishes a direct link between configurational entropy, electronic structure, and catalytic activity, opening new avenues for rational catalyst design."},
         "confidence": 0.96},
    ]
}
D_CONCLUSION_EXAMPLE_JSON = json.dumps(D_CONCLUSION_EXAMPLE, ensure_ascii=False, indent=2)

D_CONCLUSION_SYS = """你是一位学术文献知识抽取专家。从学术论文中提取结论，覆盖所有学科领域。

## 输出格式

```json
{output_example}
```

## 知识类型

**conclusion**: 经研究验证后的结论
- 仅需 evidence，无额外标注字段
- 是论文基于研究结果得出的确定性结论

## 提取要求

1. conclusion 是经过实验/分析/论证验证后的结论，不同于 claim（主张/假设）
2. 关注结论中的"因此"、"结果表明"、"本文发现"等标志性表述
3. 一篇论文通常有 1-5 个核心 conclusion，提取完整的结论段落

## 提取原则

1. **忠实性**：original_text 必须是原文直接摘录，禁止改写
2. **完整性**：每条 evidence 至少 8-10 个完整句子，保留充足上下文
3. **覆盖度**：只提取论文经过验证后的明确结论。输出 token 上限 4000"""

D_CONCLUSION_USR = """## 论文内容

{paper_text}

请提取论文的核心结论。"""


def build_conclusion_prompt(paper_text: str) -> tuple[str, str, str]:
    return ("GCL_conclusion",
            D_CONCLUSION_SYS.format(output_example=D_CONCLUSION_EXAMPLE_JSON),
            D_CONCLUSION_USR.format(paper_text=paper_text))


# ─── 11. limitation ─────────────────────────────────────────

D_LIMITATION_EXAMPLE = {
    "entries": [
        {"type": "limitation", "limitation_id": "doc_xxx_lm1",
         "evidence": {"section": "Discussion",
             "original_text": "The current study is limited to five-component HEAs in alkaline media; generalizability to acid-stable compositions remains to be demonstrated. Furthermore, the long-term stability beyond 24 hours and the performance under practical device conditions (e.g., membrane electrode assemblies) have not been evaluated. The DFT calculations were performed on idealized slab models that do not capture surface reconstruction effects under operating potentials. Additionally, the precise contributions of individual elements to the overall activity cannot be deconvoluted from the present experimental data alone. The catalyst synthesis method yields polycrystalline samples, and the role of specific crystal facets or grain boundaries in the catalytic process warrants further investigation."},
         "confidence": 0.92},
    ]
}
D_LIMITATION_EXAMPLE_JSON = json.dumps(D_LIMITATION_EXAMPLE, ensure_ascii=False, indent=2)

D_LIMITATION_SYS = """你是一位学术文献知识抽取专家。从学术论文中提取研究局限性和适用约束，覆盖所有学科领域。

## 输出格式

```json
{output_example}
```

## 知识类型

**limitation**: 方法局限、适用范围约束、研究不足
- 仅需 evidence，无额外标注字段

## 提取要求

1. 提取论文明确承认的局限性：方法局限、样本局限、适用范围限制、假设约束等
2. 提取作者对研究边界、适用范围的限定表述
3. 人文社科论文中的史料局限、方法论边界、解释范围限定同样提取

## 提取原则

1. **忠实性**：original_text 必须是原文直接摘录，禁止改写
2. **完整性**：每条 evidence 至少 8-10 个完整句子，保留充足上下文
3. **覆盖度**：如果论文明确讨论了局限性，全部提取。输出 token 上限 4000
4. **防幻觉**：如果论文没有明确讨论局限性，返回空列表 {{\"entries\": []}}"""

D_LIMITATION_USR = """## 论文内容

{paper_text}

请提取论文的研究局限性和适用范围约束。"""


def build_limitation_prompt(paper_text: str) -> tuple[str, str, str]:
    return ("GLM_limitation",
            D_LIMITATION_SYS.format(output_example=D_LIMITATION_EXAMPLE_JSON),
            D_LIMITATION_USR.format(paper_text=paper_text))


# ─── 12. future_work ────────────────────────────────────────

D_FW_EXAMPLE = {
    "entries": [
        {"type": "future_work", "future_work_id": "doc_xxx_fw1",
         "evidence": {"section": "Discussion",
             "original_text": "Future studies should explore HEAs in other electrocatalytic reactions such as CO2 reduction and nitrogen reduction. Additionally, machine learning-guided composition screening could accelerate the discovery of optimal HEA formulations. In-situ/operando characterization techniques such as X-ray absorption spectroscopy and surface-enhanced Raman spectroscopy are needed to directly probe the multi-path electron transfer mechanism under working conditions. Systematic investigation of the effect of each constituent element through targeted substitution experiments would help elucidate individual contributions to catalytic activity. Long-term durability testing under industrially relevant current densities exceeding 500 mA/cm² is essential to assess practical viability for commercial electrolyzer applications."},
         "confidence": 0.93},
    ]
}
D_FW_EXAMPLE_JSON = json.dumps(D_FW_EXAMPLE, ensure_ascii=False, indent=2)

D_FW_SYS = """你是一位学术文献知识抽取专家。从学术论文中提取未来研究方向，覆盖所有学科领域。

## 输出格式

```json
{output_example}
```

## 知识类型

**future_work**: 未来研究方向、后续工作建议
- 仅需 evidence，无额外标注字段

## 提取要求

1. 提取论文明确提出的未来研究方向和建议
2. 不仅提取"future work"章节的内容，也包括讨论中提到的后续方向
3. 人文社科论文中提出的进一步研究课题同样提取

## 提取原则

1. **忠实性**：original_text 必须是原文直接摘录，禁止改写
2. **完整性**：每条 evidence 至少 8-10 个完整句子，保留充足上下文
3. **覆盖度**：论文明确提出的未来方向全部提取。输出 token 上限 4000
4. **防幻觉**：如果论文没有明确讨论未来研究方向，返回空列表 {{\"entries\": []}}"""

D_FW_USR = """## 论文内容

{paper_text}

请提取论文的未来研究方向和后续工作建议。"""


def build_future_work_prompt(paper_text: str) -> tuple[str, str, str]:
    return ("GFW_future_work",
            D_FW_SYS.format(output_example=D_FW_EXAMPLE_JSON),
            D_FW_USR.format(paper_text=paper_text))


# ═══════════════════════════════════════════════════════════════
#  类型→prompt 构建函数索引
# ═══════════════════════════════════════════════════════════════

_TYPE_BUILDERS = {
    "concept":             build_concept_solo_prompt,
    "relation":            build_relation_solo_prompt,
    "dataset":             build_dataset_prompt,
    "data_specification":  build_data_specification_prompt,
    "method":              build_method_prompt,
    "experiment":          build_experiment_prompt,
    "quantitative_result": build_quantitative_result_prompt,
    "performance_result":  build_performance_result_prompt,
    "claim":               build_claim_prompt,
    "conclusion":          build_conclusion_prompt,
    "limitation":          build_limitation_prompt,
    "future_work":         build_future_work_prompt,
}

# 每类知识的字段提示（用于自定义分组时说明字段含义）
_TYPE_FIELD_HINTS = {
    "concept":  "term, normalized, std_label",
    "relation": "head, head_term, relation_type(enhances|inhibits|causes|creates|influences|belongs_to|measures|uses|compares|precedes|derives), relation_surface, tail, tail_term",
    "dataset":  "name, modality(text|image|tabular|time_series|multimodal|other), domain",
    "data_specification": "spec_type(format_rule|quality_standard|env_requirement|metadata_standard), description",
    "method":   "name, method_type(model|algorithm|protocol|software|instrument|preprocessing|field_research|textual_analysis|survey|interview)",
    "experiment": "task, setup",
    "quantitative_result": "quantity, value, unit, context, result_type(main_result|baseline|ablation|measurement|threshold)",
    "performance_result": "metric, compared_to",
    "conclusion": "（仅需 evidence）",
    "claim":     "（仅需 evidence）",
    "future_work": "（仅需 evidence）",
    "limitation":  "（仅需 evidence）",
}

# 每类知识的专属提取指导（多类型合并时拼接）
_TYPE_GUIDANCE = {
    "concept": """**concept 提取要求**：
- 学科核心概念优先：理论框架、核心对象、关键性质、方法论概念、学科分类
- 数量：20-40 个，学科相关概念占 60% 以上
- 人文社科论文同样需提取充足概念（思想流派、制度框架、历史事件、社会结构等）
- 避免泛化词汇（如 "result"、"analysis"、"experiment"）""",

    "relation": """**relation 提取要求**：
- 数量硬指标：15-30 条，不能少于 15 条
- 关系类型：creates(创建/制定)、influences(思想影响)、derives(推导/继承)、enhances(增强)、inhibits(抑制)、causes(导致)、belongs_to(归属)、measures(测量)、uses(使用)、compares(比较)、precedes(先于)
- 类型均衡：强语义关系占 50%+，belongs_to 不超过 30%
- 人文社科多用 creates/influences/derives/precedes
- 每个核心实体至少参与 2-3 条关系""",

    "dataset": """**dataset 提取要求**：
- 不仅提取命名的公开数据集，也要提取论文中描述的数据集合
- 社会科学中的调查样本、访谈记录、档案汇编同样视为 dataset
- modality 根据数据性质选择：问卷调查→tabular，文本档案→text，地理信息→image
- name 为必填字段""",

    "data_specification": """**data_specification 提取要求**：
- 提取论文中明确定义的数据格式、质量标准、环境要求、元数据标准
- 社会科学中的抽样标准、访谈协议、编码规范同样视为 data_specification
- spec_type 必填：format_rule/quality_standard/env_requirement/metadata_standard""",

    "method": """**method 提取要求**：
- 提取论文中使用的所有研究方法和技术手段
- 自然科学：instrument, protocol, model, algorithm
- 社会科学：survey, interview, field_research
- 人文学科：textual_analysis
- 通用：software, preprocessing
- method_type 必填""",

    "experiment": """**experiment 提取要求**：
- 自然科学：提取每个独立实验的设置（温度、压力、设备、样本量等）
- 社会科学：提取调查设计、问卷结构、访谈流程等
- 人文学科：提取研究框架、分析路径、史料范围等
- task 必填，setup 一句话概括关键参数""",

    "quantitative_result": """**quantitative_result 提取要求**：
- 提取论文中所有有科学意义的具体数值结果，包括图表中报告的数据
- 社会科学中的统计数据（样本量、相关系数、p值、效应量等）同样提取
- 必须包含 context 字段，说明完整实验/研究背景
- 排除年份、页数、编号等元信息
- quantity、context、result_type 必填""",

    "performance_result": """**performance_result 提取要求**：
- 不仅提取数值对比，也要提取定性评价（如"A 优于 B"）
- 社会科学中的方法对比、政策效果对比同样视为 performance_result
- metric 和 compared_to 为可选字段""",

    "claim": """**claim 提取要求**：
- claim 是论文的"中心论点"——作者试图论证的核心命题
- 区分 claim 和 conclusion：claim 是主张/假设，conclusion 是验证后的结论
- 人文社科论文中，claim 包括作者的理论立场、解释框架
- 一篇论文通常有 1-5 个核心 claim""",

    "conclusion": """**conclusion 提取要求**：
- conclusion 是经过实验/分析/论证验证后的确定性结论
- 关注"因此"、"结果表明"、"本文发现"等标志性表述
- 一篇论文通常有 1-5 个核心 conclusion""",

    "limitation": """**limitation 提取要求**：
- 提取论文明确承认的局限性：方法局限、样本局限、适用范围、假设约束
- 人文社科中的史料局限、方法论边界同样提取
- 没有明确讨论局限性时返回空列表""",

    "future_work": """**future_work 提取要求**：
- 提取论文明确提出的未来研究方向和建议
- 不仅提取"future work"章节，也包括讨论中提到的后续方向
- 没有明确讨论未来方向时返回空列表""",
}


# ─── 动态自定义分组 ──────────────────────────────────────────

def build_custom_prompt(paper_text: str, types: list[str], group_idx: int) -> tuple[str, str, str]:
    """为任意类型组合构建 prompt。
    单类型：直接使用该类型的专属 prompt。
    多类型：合并各类型的字段定义和专属提取指导，生成组合 prompt。
    """
    # 单类型 → 直接用专属 prompt
    if len(types) == 1:
        t = types[0]
        builder = _TYPE_BUILDERS.get(t)
        if builder:
            name, sys_p, usr_p = builder(paper_text)
            return (f"GX_custom_{group_idx}_{t}", sys_p, usr_p)

    # 多类型 → 合并
    group_name = f"GX_custom_{group_idx}"
    type_list = "、".join(types)

    # 字段定义
    field_lines = []
    for t in types:
        hint = _TYPE_FIELD_HINTS.get(t, "")
        field_lines.append(f"### {t}\n字段：{hint}")
    field_desc = "\n\n".join(field_lines)

    # 专属提取指导
    guide_lines = []
    for t in types:
        guide = _TYPE_GUIDANCE.get(t)
        if guide:
            guide_lines.append(guide)
    guidance = "\n\n".join(guide_lines) if guide_lines else ""

    sys_prompt = f"""你是一位学术文献知识抽取专家。从学术论文中提取以下知识类型，覆盖所有学科领域。

## 输出格式

```json
{{{{"entries"{{}}: [{{{{"type": "...", ...}}}}, ...]}}}}
```

每条条目公共字段：
- type: 以下知识类型之一
- evidence: {{{{"section"{{}}: "...", "original_text"{{}}: "原文摘录（8-10句，禁止改写）"}}}}
- confidence: 0-1 置信度
- 各类型的专属字段见下方

## 知识类型字段定义

{field_desc}

## 各类型提取指导

{guidance}

## 提取原则

1. **忠实性**：original_text 必须是原文直接摘录，禁止改写
2. **完整性**：每条 evidence 至少 8-10 个完整句子，保留充足的上下文和细节
3. **覆盖度**：原文中存在的全部提取，没有的不编造。输出 token 上限 12000，{type_list} 均衡分配
4. **防幻觉**：如果论文缺乏实质学术内容，返回空列表 {{{{}}"entries"{{}}: []{{}}}}"""

    user_prompt = f"""## 论文内容

{{{{paper_text}}}}

请提取{type_list}。""".format(paper_text=paper_text)

    return group_name, sys_prompt, user_prompt


def parse_custom_group_spec(spec: str) -> list[list[str]]:
    """解析 EXTRACT_GROUP_SPEC。例: 'concept|relation|dataset+method' → [['concept'], ['relation'], ['dataset','method']]"""
    groups = []
    for part in spec.split("|"):
        types = [t.strip() for t in part.split("+") if t.strip() in _TYPE_FIELD_HINTS]
        if types:
            groups.append(types)
    return groups
