# Multi-source Canvas DDL Assistant

实时 Canvas 结构化记录与**预先 ingestion、经验证的官方课程文档**共同提供 DDL
证据。document-only deadline 可以独立进入查询；与同一个 Canvas deadline 冲突时，
默认选择实时 Canvas，并保留文档的原日期、文档名、来源位置和原文。

Python 引擎负责事实、验证、来源优先级、分类、去重与计数；Codex 只负责理解问题
和展示结果。需要文档时先检查文件库并更新证据，再查询；不调用 LLM API，不修改 Canvas。

默认所有 DDL 查询都会检查选中课程的全部可访问受支持文档以及 Canvas Syllabus/Pages，
即使 Canvas 或本地证据已经返回完整正结果，也会先核对文件清单和版本。这样宽泛 DDL 查询
不会漏掉 PDF-only 考试，已有文档日期也不会因 positive fast path 而跳过更新。认证 Canvas API 返回的课程文件会按课程 ID、文件 ID 和内容哈希自动登记；
同一文件的后续版本自动更新并重新 ingestion，无需人工批准。外部链接和手工本地文件仍需维护者建立信任记录。
相同版本复用本地证据，旧文件消失或已知变更无法纳入时，旧日期会被停用。

显式查询上传文档/最新文件时加 `--document-mode refresh`；需要仅使用当前证据时
加 `--document-mode existing`。结果的 file_library_check 显示检查/跳过、更新、
待审核及失败信息。文件库访问失败会明确返回 partial，不声称已检查最新课程文档。

## 跨平台安装

从 GitHub 获取项目：

```text
git clone https://github.com/zhangleqi04-dev/canvas-ddl.git
cd canvas-ddl
```

需要 Python 3.11 或更高版本。推荐运行只依赖 Python 标准库的跨平台安装器；如果系统
没有 `python` 命令，macOS/Linux 通常使用 `python3`：

```text
python tools/setup_project.py
```

安装器会创建 `.venv`、安装引擎、在终端中隐藏输入 Canvas token，并把 Skill 安装到
`$CODEX_HOME/skills/canvas-ddl`（未设置 `CODEX_HOME` 时使用 `~/.codex`）。已有且属于
其他项目的同名 Skill 不会被覆盖。扫描版 PDF 的 OCR 依赖体积较大，按需安装：

```text
python tools/setup_project.py --ocr
```

开发者可加 `--dev`；仅安装 Python 引擎可加 `--no-skill`；无人值守环境可加
`--non-interactive`，然后自行编辑 `.env`。安装后重启 Codex，再使用：

```text
$canvas-ddl 我下周有什么作业和考试？
```

### 手动安装

Windows PowerShell：

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e .
Copy-Item .env.example .env
.\.venv\Scripts\Activate.ps1
```

macOS/Linux：

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -e .
cp .env.example .env
source .venv/bin/activate
```

在 `.env` 中设置 `CANVAS_BASE_URL`、`CANVAS_TOKEN` 和 `CANVAS_TIMEZONE`。保留的
`CANVAS_API_TOKEN` 名称仍兼容；token 不会打印或复制进技能。可选
`CANVAS_COURSE_IDS`（数字 ID）限定课程。OCR 使用 `pip install -e ".[ocr]"` 单独安装，
默认 CPU 配置适用于普通电脑。

激活虚拟环境后，以下命令在三个平台相同：

```text
canvas-ddl courses
canvas-ddl deadlines --time-intent-file time-intent.json --type assignment
canvas-ddl deadlines --time-intent-file time-intent.json --type exam
canvas-ddl upcoming --days 14 --type quiz
canvas-ddl deadlines --time-intent-file time-intent.json --course "COURSE101" --limit 1
```

Skill 由 Codex 理解时间，输出 TimeIntent，再由 Python 验证并计算实际范围。
自然语言不限于上述示例；整周、滚动天数、星期区间、月初 N 天和月底之前均可表达。
“最近”等有实质歧义的说法先澄清；周按周一至周日计算，范围在答案中展示。
引擎不解析自然语言，也没有时间短语白名单或正则回退。结构化调用使用
--time-intent JSON 或 --time-intent-file <UTF-8 JSON 文件路径>，不能同时使用
--start/--end；后两者只接受用户或已验证锚点给出的明确 ISO 时间戳。
详见 [时间意图接口](skills/canvas-ddl/references/time-intent.md)。
返回 UTF-8 JSON。技能从项目或 CANVAS_DDL_HOME 找到同一个引擎，读取已 ingestion 的证据，
不自行读源文档、决定来源、处理冲突或把语义结果直接当成事实。

## 课程文档 ingestion

Canvas Files API、Canvas Syllabus 和已发布 Pages 是认证、课程范围内的来源。引擎验证
Canvas origin、课程路径、资源 ID 和内容哈希后，以 `source_authority=canvas_api` 自动登记，
不要求人工填写 `approved_by` 或教学期间，也不会进入来源批准队列。未变更的版本直接复用
SQLite 证据；新版本重新解析并保留版本历史。资源消失、当前版本下载失败或哈希异常时，
旧 deadline 会被扣留并将查询标为 partial。

显式准备命令仍可使用已有 Canvas `.env`：

```text
canvas-ddl prepare-documents --course COURSE101 --course COURSE102
```

该命令通过 Canvas Files API 查找文件名带 syllabus、outline、handout 等提示的受支持文档，
最多处理 20 份并自动登记。自动 DDL 查询使用更完整的文件库刷新：检查全部可访问受支持
文件，不按文件名筛选，也没有 20 份默认上限。Canvas 文件显示名可以没有扩展名，只要 API
MIME 类型明确受支持。本地支持 PDF、DOCX、PPTX、XLSX、CSV、TXT/Markdown、RTF、HTML
和 PNG/JPEG/WebP/TIFF/BMP；DOC/PPT/XLS、ZIP 和音视频转录暂不处理。Canvas token 只发给
Canvas origin；签名下载地址不写入登记表。`CANVAS_DOCUMENT_HOSTS` 只控制下载传输域名，
不会给任意外部页面授予课程来源身份。

`approve-document` 仅保留给外部 URL 或手工放入的本地官方课程文档。维护者必须核对课程、
文档类型、适用教学期间和内容哈希，并提供真实身份；Codex/LLM 不能代填。这个外部来源
流程不会应用于认证 Canvas Files/Syllabus/Pages。

```text
# 仅适用于 external/manual pending source
canvas-ddl approve-document --document external-course-outline --approved-by "你的姓名" --valid-from "YYYY-MM-DD" --valid-until "YYYY-MM-DD" --confirm-official

# 查看登记、ingestion 和语义审核状态
canvas-ddl documents
```

文档 pipeline：

```text
Canvas API scoped identity 或 operator trust
→ DocumentParser → StructuralChunker → LightSemanticPrefilter
→ Codex semantic extractor (codex-semantic-v2)
→ SemanticDeadlineCandidate → Python DeadlineValidator
→ SQLite 文档证据（source units、semantic reviews、proposals、validation audit）
```

Codex 为每个逻辑事件同时返回：原文中的精确 `date_expression`、标准化
`normalized_date=YYYY-MM-DD` 和可选 `normalized_time=HH:MM`。因此 `02/10/26`、
`24th November`、`2:00pm` 等不必先由正则转换成 ISO。Python 仍会独立检查标准日期是否
是原文表达的可能读法、原文 span 是否存在、完整文档是否只有一个可用年份、Canvas/
operator 来源边界、课程、哈希、位置、OCR 置信度和语义角色。Codex 不能批准来源、决定
冲突、去重或计数。含糊的数值日期可由 Codex 根据课程上下文选择日/月顺序，但映射不在
原文字面可能范围内就会被拒绝；缺失年份且全文没有唯一年份时保持 unresolved。

当 `semantic_review_required=true` 时，`$canvas-ddl` 分批获取 engine-issued chunk，提交严格
`codex-semantic-v2` JSON，再重跑原查询。审核完成前，该文档的 deadline 被扣留，结果为
partial。最终查询只读持久化证据，不在每次查询时重新打开 PDF。扫描页仅在新文件或变更
文件 ingestion 时调用本地 PP-OCRv6 Small；普通文字页不启动 OCR。Week 7 Friday 等相对教学
周仍由 Canvas Calendar Events/课程 ICS 的确定性 resolver 处理，不由 Codex 猜日期。

## 查询结果与冲突

```text
实时 Canvas + 已 ingestion 官方证据
→ normalize → classify → validate → reconcile → deduplicate
→ filter → sort → limit → count
```

- 同课程、明确同一 logical item：同日期合并；日期不同时 Canvas 优先，conflicts
  保存文档替代值与完整来源。仅日期的 PDF 可与同一天精确 Canvas 时间一致。
- 仅官方课程文档有该记录：经验证后成为 canonical deadline，提交状态为 unknown。
- 课程/名称/多次考试身份不明确：不擅自合并；待处理项出现在 unresolved_deadlines，
  不计入总数。没有 Canvas 的冲突 PDF 也不会随意选“最新的一份”。
- 当前学生的 Canvas due=null 不会被旧 PDF due 复活；unlock/lock 不是 due。
- reconciliation 在范围筛选前进行；必要时查询选中课程跨日期的 calendar，防止
  延期后的 Canvas 记录消失而旧文档日期仍然被计数。
- count 等于返回列表长度（经过 limit）；matched_count/course_counts 为 limit 前
  的引擎计数，所有 unresolved 候选不参与计算。
- reference_count/reference_deadlines 是 Canvas 日历解析出的高召回参考安排，必须
  标明未确认、日期精度、置信度和来源，不并入确认数量。
- 只要存在与问题相关但尚未确认的安排，无论已确认结果是 0 条还是多条，Skill 都会在
  **参考安排（未确认）** 中单独显示原文、课程、来源位置和未确认原因。没有经引擎解析的
  时间范围时，会明确说明无法判断它是否落在查询区间；这类证据不改变确认数量。
- ok/complete=true 仅覆盖实时支持来源和**登记的文档 inventory**，不是所有课程文件。
  未登记文档会有 scope warning；partial 零条不能说明没有考试。
- freshness 区分 live 与已 ingestion 文档，文档 validation/ingestion time 不是实时
  PDF 读取时间。Canvas 失败不会悄悄返回旧 Canvas cache。

SQLite 默认在 data/documents.sqlite3，只存 ingestion artifacts；可通过
CANVAS_DOCUMENT_STORE 和 CANVAS_DOCUMENT_REGISTRY 改位置，路径相对于 `.env`。
不处理公告、邮件、聊天、非官方笔记，不加入 RAG/vector DB 或教学内容问答。

## 验证与规范

```text
python -m pytest -q
```

默认测试为离线模拟数据与生成的 PDF，不需要真实 token。
[PRD](docs/PRD_DDL_ONLY.md)、[Architecture](docs/ARCHITECTURE.md)、
[开发规则](docs/AGENTS.md)、[运行技能](skills/canvas-ddl/SKILL.md) 同步定义 v0.12.2。
本地验证记录可能包含私有课程信息，因此不提交到公开仓库。

PDF parser 依据 [pypdf 官方文档](https://pypdf.readthedocs.io/en/stable/user/extract-text.html)；
OCR 依据 [PaddleOCR 官方安装说明](https://www.paddleocr.ai/main/en/version3.x/installation.html)
和 [PP-OCRv6 OCR pipeline](https://www.paddleocr.ai/main/en/version3.x/pipeline_usage/OCR.html)。

第一次遇到扫描页时会自动下载 `PP-OCRv6_small_det` 与
`PP-OCRv6_small_rec` 到用户的 PaddleX 模型缓存。模型随后离线复用；OCR 只在
新文件或变更文件 ingestion 时运行，最终 deadline 查询读取 SQLite 缓存，不占用
常驻显存。Windows CPU 运行时固定使用 PaddlePaddle 3.2.x；3.3.1 已知在该组合上
可能触发 oneDNN 不支持的算子。

## 全课程文档考试检查（v0.9）

“下周有什么考试/几门考试”自动检查所选课程的所有受支持文档，不按文件名筛选，也没有
默认20份上限。上述 prepare-documents 是单独的显式维护命令，其文件名提示与20份
默认限制不适用于自动考试查询。Files 禁止访问时尝试 Modules 文件链接，并标为部分
覆盖；访问、下载、解析及空白页失败均保留，不声称已经成功读完所有文件。

认证 Canvas API 返回的新 PDF、DOCX、PPTX、XLSX、CSV、文本、RTF、HTML 和图片会
自动登记并进入主 ingestion pipeline。只有外部/手工来源在 operator trust 前进入
`data/documents.pending.sqlite3`，只能作为未确认参考，不能进入 canonical deadline 或考试计数。
相同 metadata/hash 复用已解析内容，新版本按授权更新，最终查询只读取持久化证据。

结果中的 document_content_matches 来自引擎对全部已保存文档单元的考试关键词及上下文检索，
覆盖跨行安排。每份文档最多返回10段、每段1500字符并显示截断信息；关键词匹配
不是考试数量，统计学的 test、举例中的 exam 也不是考试安排。Skill 只呈现相关
引擎证据及风险，不自行读取源文档、决定来源或将原文变成最终日期事实。
相关的未确认证据不会因为已有 confirmed 考试而被隐藏；练习卷、示例、统计检验、
教学内容、仅有评分权重及取消/否定语境仍不会作为可能的考试安排展示。
PDF 解析支持能以空用户密码正常打开的 AES 文件；需要实际密码的文件仍安全失败。
