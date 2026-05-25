# KnowledgeEX

将论文 PDF 转换为带原文证据的结构化知识 JSON。字段定义见 [schemas/README.md](schemas/README.md)。

## 流程

| 阶段 | 命令 | 输入 → 输出 | LLM |
|------|------|------------|:---:|
| prepare | `run.py prepare` | `archives/*.zip` → `papers/<批次>/*.pdf` | - |
| convert | `run.py convert` | PDF → `markdown/<批次>/*.md` | - |
| preprocess | `run.py preprocess` | MD → 元数据（标题/DOI/学科/关键词） | 1次 |
| extract | `run.py extract` | 全文 → 知识条目 + 原文证据 | 可配 |

## 快速开始

### 1. 安装

```bash
pip install pymupdf4llm pymupdf openai python-dotenv pydantic
```

### 2. 配置

在项目根目录创建 `.env`：

```bash
# 必要
OPENAI_API_KEY="sk-xxx"
OPENAI_BASE_URL="https://api.example.com/v1"
LLM_MODEL="gpt-4o"

# 路径（相对于项目根目录）
PDF_DIR=papers
MARKDOWN_DIR=markdown
JSON_OUTPUT_DIR=json_output
DB_DIR=db

# 可选
WORKERS=5                 # API 并发数
CONVERT_WORKERS=8         # PDF 转 Markdown 进程数
MAX_INPUT_CHARS=50000     # 送 LLM 的最大字符数
METADATA_HEAD_CHARS=5000  # 无摘要时取论文前 N 字符做学科分类
MIN_MD_CHARS=10000        # 低于此值的 MD 跳过
MAX_OUTPUT_TOKENS=16384
MAX_RETRIES=3
```

### 3. 运行

```bash
# 完整流程
python run.py all

# 或分步执行
python run.py prepare            # ZIP 解压
python run.py convert            # PDF → Markdown
python run.py preprocess         # 元数据提取
python run.py extract            # 知识抽取

# 从中间阶段继续
python run.py all --from-stage preprocess

# 查看结果
python run.py inspect
```

## 使用 ZIP 包

将 ZIP 放入 `archives/`，然后：

```bash
python run.py prepare                    # 全部解压
python run.py prepare --archive batch_001.zip  # 只解压指定包
```

解压得到 `papers/batch_001/*.pdf`。

已有 PDF 可以直接放到 `papers/`，跳过 `prepare`。

## 处理指定论文

```bash
python run.py all --paper path/to/paper    # 只处理单篇
python run.py all --source papers/batch_001  # 只处理某批次
python run.py preprocess --reset-failed       # 重试失败的 preprocess
python run.py extract --debug                 # 保存 LLM 原始响应
```

## 输出结构

```
json_output/
  batch_001/
    paper_doi.json          # metadata + entries + evidence
    debug/                  # --debug 时保存 LLM 原始响应
db/
  papers.db                 # 流水线状态（断点续跑）
```

每个 JSON 结构：

```json
{
  "metadata": {
    "doc_id": "doc_xxx",
    "title": "...",
    "year": 2024,
    "doi": "10.xxx/xxx",
    "primary_discipline": {"level1": "...", "level2": "...", "level3": "..."},
    "keywords": ["..."]
  },
  "entries": [
    {
      "type": "concept",
      "concept_id": "doc_xxx_c1",
      "term": "...",
      "evidence": {"section": "Abstract", "original_text": "原文摘录 8-10 句"}
    }
  ]
}
```

## 抽取分组配置

`.env` 中 `EXTRACT_GROUPS` 控制抽取哪些知识类型：

```bash
# 推荐：concept 和 relation 独立，其余全合并
EXTRACT_GROUPS=concept,relation,dataset_spec,method_experiment,quant_perf,insight_outlook

# 完全自定义：每种类型一个独立 prompt
EXTRACT_GROUP_SPEC=concept|relation|dataset|method|experiment
```

## Schema

12 种知识类型的字段定义、分组配置详见 [schemas/README.md](schemas/README.md)。
