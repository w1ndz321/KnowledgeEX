# KnowledgeEX

KnowledgeEX 将原始论文 PDF 转换为可追溯的结构化知识 JSON，用于下游 AI4S 检索、证据核验和二次知识加工。项目重点不是穷举全文信息，而是在控制 LLM 调用成本的前提下，保留论文重要知识及可返回原文的 evidence。

最终 JSON 字段及知识类型说明见 [schemas/README.md](schemas/README.md)。

## Pipeline

| 阶段 | 输入 | 主要操作 | 输出 | LLM 调用 |
|---|---|---|---|---:|
| `convert` | PDF | PDF 指纹去重；多进程解析；保留页码、图题和表格；正文指纹去重；过滤文本过短文件 | `markdown/<source_key>.md`、数据库状态 | 0 |
| `preprocess` | Markdown | 正则预提取；用前 5000 字符及去重后的摘要/引言校验标题、年份、DOI；生成学科和关键词 | 初始 `json_output/<source_key>.json` | 1 次/篇 |
| `extract` | Markdown + metadata JSON | 默认分两组提取知识；程序回填页码和长 evidence；去重并生成 entry ID | 最终 JSON | 通常 2 次/篇 |

正常完成一篇论文通常需要 3 次 LLM 调用：`preprocess` 1 次，`extract` 2 次。若结果组首次返回空条目，程序会为该组自动补抽一次。

## 目录结构

```text
KnowledgeEX/
  papers/                 # PDF_DIR，原始 PDF，可按批次建子目录
    batch_0001/
      paper_a.pdf
  markdown/               # convert 输出，保持 papers 下的相对目录
    batch_0001/
      paper_a.md
  json_output/            # preprocess/extract 输出，保持相对目录
    batch_0001/
      paper_a.json
    debug/                # 使用 --debug 时的 LLM raw 与 anchor audit
  db/
    papers.db             # 全语料三阶段状态、指纹、跳过原因、token 统计
  schemas/
    README.md             # 最终 JSON 数据字典
  run.py                  # 主程序
```

`source_key` 是每个输入文件的任务身份，固定为相对于 `PDF_DIR` 的 PDF 路径，例如 `batch_0001/paper_a.pdf`。因此不同批次中的同名 PDF 不会互相覆盖；全量运行与 `--source` 子目录运行也会引用同一条数据库记录。

`doc_id` 是文档内容身份，由原 PDF 字节的 SHA-256 指纹生成。两个路径不同但字节完全相同的 PDF 拥有相同内容身份，其中后发现的文件会被标记为重复并跳过。

## 安装与配置

当前代码使用 Python，核心依赖包括 `pymupdf4llm`、`pymupdf`、`openai`、`python-dotenv` 和 `pydantic`。已配置的 `myagent` 环境可直接运行：

```bash
conda activate myagent
```

在项目根目录创建本地 `.env`。该文件被 Git 忽略，不应提交 API Key：

```bash
PDF_DIR=papers
MARKDOWN_DIR=markdown
JSON_OUTPUT_DIR=json_output
DB_DIR=db

OPENAI_API_KEY="your-api-key"
OPENAI_BASE_URL="your-compatible-api-url"
LLM_MODEL="your-model"
LLM_TEMPERATURE=0.0
MAX_OUTPUT_TOKENS=8100
MAX_RETRIES=3

METADATA_HEAD_CHARS=5000
MAX_INPUT_CHARS=50000
MIN_MD_CHARS=10000

CONVERT_BACKEND=pymupdf4llm
CONVERT_WORKERS=8
WORKERS=5
PROCESS_LIMIT=0

UNZIP_FIRST=false
DELETE_ZIP_AFTER_UNZIP=false
EXTRACT_GROUPS=
EXTRACT_GROUP_SPEC=
EXTRACT_GROUP_PARALLEL=1
```

| 配置项 | 作用 | 使用建议 |
|---|---|---|
| `CONVERT_BACKEND` | PDF 解析后端 | 使用 `pymupdf4llm` 以保留可解析的表格和图题结构 |
| `CONVERT_WORKERS` | PDF 转换进程数 | 按本机 CPU/内存调整 |
| `WORKERS` | preprocess/extract 的论文间 API 并发 | 从 `5` 或 `10` 开始，避免 API 限流 |
| `METADATA_HEAD_CHARS` | preprocess 默认发送的论文前部字符数 | 当前设计使用 `5000` |
| `MAX_INPUT_CHARS` | 每个 extract 分组的正文字符预算 | 当前设计使用 `50000` |
| `MIN_MD_CHARS` | 解析正文最小字符数 | 过短通常表示扫描件或转换失败 |
| `EXTRACT_GROUPS` / `EXTRACT_GROUP_SPEC` | 覆盖默认知识抽取分组 | 修改知识类型策略时再使用 |

## 完整运行

对 `papers/` 下当前范围运行完整三阶段流程：

```bash
conda run --no-capture-output -n myagent python run.py all --debug
```

查看累计状态与结果审计：

```bash
conda run --no-capture-output -n myagent python run.py status
conda run --no-capture-output -n myagent python run.py inspect
```

开发和调试期间，重跑已有结果可使用：

```bash
conda run --no-capture-output -n myagent python run.py all --force --debug
```

`--force` 会覆盖本次选中范围内的产物，并将覆盖前的结果归档到 `runs/`。对被 convert 排除的论文，单独强制运行下游阶段不会绕过跳过结论；需要重新判定时必须从 `convert --force` 开始。

## 分阶段运行

建议先执行 `convert`，因为这一阶段不调用 LLM，能够先发现重复 PDF、重复正文和解析失败文件：

```bash
conda run --no-capture-output -n myagent python run.py convert --source papers/batch_0001
conda run --no-capture-output -n myagent python run.py status
```

确认解析质量后继续执行 LLM 阶段：

```bash
conda run --no-capture-output -n myagent python run.py all \
  --from-stage preprocess \
  --source papers/batch_0001 \
  --debug

conda run --no-capture-output -n myagent python run.py inspect \
  --source papers/batch_0001
```

只查看或重跑单篇时，`--paper` 接受相对于 `PDF_DIR` 的路径，可省略 `.pdf`：

```bash
conda run --no-capture-output -n myagent python run.py inspect \
  --paper batch_0001/paper_a
```

失败任务重试：

```bash
conda run --no-capture-output -n myagent python run.py preprocess \
  --source papers/batch_0001 --reset-failed

conda run --no-capture-output -n myagent python run.py extract \
  --source papers/batch_0001 --reset-failed
```

## ZIP 批次数据

对于多个 ZIP 的语料，推荐将 ZIP 存放在 `PDF_DIR` 之外，分别解压到 `papers/` 的独立批次目录。这样每次可以控制一个批次的转换、LLM 成本和质量抽查范围。

```text
archives/
  batch_0001.zip
  batch_0002.zip
papers/
  batch_0001/
  batch_0002/
```

```bash
mkdir -p archives papers

for z in archives/*.zip; do
  batch=$(basename "$z" .zip)
  mkdir -p "papers/$batch"
  unzip -oq "$z" -d "papers/$batch"
done
```

先转换所有批次：

```bash
for d in papers/*; do
  [ -d "$d" ] || continue
  conda run --no-capture-output -n myagent python run.py convert --source "$d"
done
```

先用一个批次检查抽取质量，再运行剩余批次：

```bash
conda run --no-capture-output -n myagent python run.py all \
  --from-stage preprocess --source papers/batch_0001 --debug

for d in papers/*; do
  [ -d "$d" ] || continue
  conda run --no-capture-output -n myagent python run.py all \
    --from-stage preprocess --source "$d"
done
```

已完成的任务会根据数据库状态自动跳过。若将 ZIP 直接放入 `papers/` 并启用 `UNZIP_FIRST=true`，`convert` 会自动解压当前处理范围内的 ZIP，但不适合需要逐批控制成本的大规模运行。

## 各阶段细节

### Convert

| 行为 | 设计 |
|---|---|
| 页码保留 | 每页 Markdown 前添加 `<!-- PAGE N -->`，供 evidence 回填页码 |
| 图表处理 | 保留可解析到的图题与 Markdown 表格，不单独输出图像 JSON |
| 参考文献/补充内容 | 清洗阶段截断参考文献等尾部内容，减少 LLM 输入成本 |
| PDF 去重 | 解析前计算 PDF SHA-256/96；重复文件完全跳过转换和下游 |
| Markdown 去重 | 转换后计算正文 SHA-256/96；正文重复文件跳过下游 |
| 质量过滤 | 清洗后字符数低于 `MIN_MD_CHARS` 的文件不进入 LLM 阶段 |

| `skip_code` | 发生时机 | 状态含义 |
|---|---|---|
| `duplicate_pdf` | PDF 解析前 | `convert/preprocess/extract=skipped` |
| `duplicate_markdown` | Markdown 生成后 | `convert=done`, `preprocess/extract=skipped` |
| `insufficient_text` | Markdown 生成后 | `convert=done`, `preprocess/extract=skipped` |

### Preprocess

每篇论文调用 LLM 一次。输入始终包括 Markdown 前 `METADATA_HEAD_CHARS` 个字符；若已解析出的 `abstract` 或 `introduction` 不在该前部文本中，则去重后追加。输出包括：

| 内容 | 处理方式 |
|---|---|
| `title`, `year`, `doi` | LLM 依据原文核验正则候选，避免误匹配引用或页眉信息 |
| `primary_discipline` | 必填；一级学科必须来自代码定义表单 |
| `secondary_disciplines` | 仅在实质交叉学科贡献存在时填写 |
| `keywords` | 与研究对象、方法和任务相关的代表术语 |

### Extract

默认每篇论文调用两组 prompt：

| 组名 | 知识类型 | 输入策略 |
|---|---|---|
| `GA_study_design` | `concept`, `relation`, `dataset`, `data_specification`, `method`, `experiment` | 去掉 Markdown 表格行后的正文前 `MAX_INPUT_CHARS` 字符 |
| `GB_results_claims` | `quantitative_result`, `performance_result`, `claim`, `conclusion`, `limitation`, `future_work` | 保留表格；当前 50000 字符预算下使用前 10000 + 后 40000 字符 |

LLM 只返回短的原文 `anchor_text` 和类型 `payload`。程序随后将 anchor 定位回 Markdown：

| Evidence 规则 | 行为 |
|---|---|
| 正文 | 保存命中句前后各最多 5 句，总长度最多 5000 字符 |
| 表格 | 只保存 caption、表头、命中行及邻近解释，避免整表数值污染 |
| 匹配状态 | `exact`、`unique_fragment`、`unmatched` 写入最终 JSON |

## 注意事项

| 问题 | 处理建议 |
|---|---|
| API 成本 | 先完成 convert，再用一个批次评估知识数量和 evidence 质量后扩大运行范围 |
| LLM 并发 | 不要直接使用过高 `WORKERS`；限流会产生大量失败重试与额外成本 |
| `--debug` 文件量 | 调试样本使用；大批量正式抽取时可关闭 |
| 单次处理数量 | 当前代码会将本次范围的任务键写入 SQLite `IN (...)` 查询；建议每批 1 万到 5 万篇，不要单次接近约 25 万篇参数限制 |
| 旧 JSON | 旧字段结构不能与当前 `payload + evidence` 契约混合；切换契约时应从 convert 开始重跑 |
| 修改知识类型 | 同步修改 [schema.py](schema.py)、[prompts.py](prompts.py) 和 [schemas/README.md](schemas/README.md) |

## 主要代码文件

| 文件 | 作用 |
|---|---|
| `run.py` | 主入口：按阶段运行、检查状态与审计产物 |
| `convert.py` | PDF 转 Markdown、页码归档、两级去重和质量过滤 |
| `preprocess.py` | 元数据校验、学科分类和关键词生成 |
| `extract.py` | 知识抽取、evidence anchor 匹配、关系链接和最终输出 |
| `prompts.py` | 元数据与知识抽取 prompt 设计 |
| `schema.py` | 最终 JSON 与 LLM 返回结构定义 |
| `state.py` | SQLite 状态、跳过原因和 token 统计管理 |
| `utils.py` | 配置、路径身份、哈希、元数据解析及 LLM 调用工具 |
