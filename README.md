# KnowledgeEX

KnowledgeEX 将论文 PDF 转换为带原文证据的结构化知识 JSON，供下游 AI4S 任务检索、核验和二次处理。最终 JSON 的字段和知识类型定义见 [schemas/README.md](schemas/README.md)。

## Pipeline

| 阶段 | 做什么 | 主要输出 | 是否调用 LLM |
|---|---|---|---|
| `prepare` | 将 ZIP 中的 PDF 解压到按批次组织的输入目录 | `papers/<批次>/**/*.pdf` | 否 |
| `convert` | PDF 去重、解析 Markdown、保留页码/表格/图题、过滤低质量文本 | `markdown/<批次>/**/*.md`、`db/papers.db` | 否 |
| `preprocess` | 修正标题、DOI、年份；生成学科和关键词 | JSON 中的 `metadata` | 是，每篇 1 次 |
| `extract` | 提取重要知识并将 evidence anchor 定位回原文 | `json_output/<批次>/**/*.json` | 通常每篇 2 次 |

一篇有效论文通常需要 3 次 LLM 调用：`preprocess` 1 次，`extract` 2 次。

## Quickstart

### 1. 安装依赖

使用 Python 环境安装所需包：

```bash
conda activate myagent
pip install pymupdf4llm pymupdf openai python-dotenv pydantic
```

### 2. 配置模型和目录

在项目根目录创建 `.env`：

```bash
PDF_DIR=papers
MARKDOWN_DIR=markdown
JSON_OUTPUT_DIR=json_output
DB_DIR=db

OPENAI_API_KEY="your-api-key"
OPENAI_BASE_URL="your-compatible-api-url"
LLM_MODEL="your-model"

CONVERT_BACKEND=pymupdf4llm
CONVERT_WORKERS=8
WORKERS=5
METADATA_HEAD_CHARS=5000
MAX_INPUT_CHARS=50000
MIN_MD_CHARS=10000
```

### 3. 运行流程

在项目根目录执行：

```bash
conda run --no-capture-output -n myagent python run.py prepare
conda run --no-capture-output -n myagent python run.py convert
conda run --no-capture-output -n myagent python run.py all --from-stage preprocess
conda run --no-capture-output -n myagent python run.py inspect
```

| 命令 | 结果 |
|---|---|
| `run.py prepare` | 将 `archives/` 里的 ZIP 解压为 `papers/` 中的 PDF 批次 |
| `run.py convert` | 生成 Markdown，并在数据库中记录重复或解析失败的 PDF |
| `run.py all --from-stage preprocess` | 对可用论文执行 metadata 处理和知识抽取，生成最终 JSON |
| `run.py inspect` | 查看条目数量、已有页码定位的 evidence 数量和输出位置 |

## 两个 ZIP 示例

假设你有两个压缩包：

```text
batch_0001.zip
batch_0002.zip
```

### 1. 放到 `archives/`

```text
KnowledgeEX/
  archives/
    batch_0001.zip
    batch_0002.zip
```

`archives/` 已被 Git 忽略，不会把原始数据包推送到仓库。

### 2. 解压 PDF

```bash
cd /Users/jupiter/Desktop/KnowledgeEX
conda run --no-capture-output -n myagent python run.py prepare
```

得到：

```text
papers/
  batch_0001/
    ...pdf
  batch_0002/
    ...pdf
```

`prepare` 只提取 PDF。相同 ZIP 重复执行会自动跳过；不要用新版 ZIP 覆盖已经抽取过的批次，新版请改名为新批次，例如 `batch_0001_v2.zip`。

### 3. 转换 PDF

```bash
conda run --no-capture-output -n myagent python run.py convert
```

得到：

```text
markdown/
  batch_0001/
    ...md
  batch_0002/
    ...md
db/
  papers.db
```

数据库会记录被跳过的文件原因：`duplicate_pdf`、`duplicate_markdown` 或 `insufficient_text`。

### 4. 抽取知识

```bash
conda run --no-capture-output -n myagent python run.py all --from-stage preprocess
```

得到：

```text
json_output/
  batch_0001/
    ...json
  batch_0002/
    ...json
```

每个 JSON 包含论文元数据、知识条目和可定位回原文的 evidence。

### 5. 查看结果

```bash
conda run --no-capture-output -n myagent python run.py inspect
```

该命令会显示每篇论文的知识条目数量、类型统计和已定位到页码的 evidence 数量。
