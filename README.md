# Multi-source Canvas DDL Assistant

实时 Canvas 结构化记录与**预先 ingestion、经验证的官方课程文档**共同提供 DDL
证据。document-only deadline 可以独立进入查询；与同一个 Canvas deadline 冲突时，
默认选择实时 Canvas，并保留文档的原日期、文档名、来源位置和原文。

Python 引擎负责事实、验证、来源优先级、分类、去重与计数；Codex 只负责理解问题
和展示结果。需要文档时先检查文件库并更新证据，再查询；不调用 LLM API，不修改 Canvas。

默认考试查询会检查选中课程的全部可访问受支持文档以及 Canvas Syllabus/Pages，即使 Canvas 已有考试记录，
也会检查文档中是否有其他安排。其他查询在已有完整结果时直接返回；空或不完整
结果检查相关课程文件库。已批准文件的 auto_refresh=true 允许同一个 Canvas
文件 ID 的后续版本自动更新并重新 ingestion；新来源仍需确认官方性。
相同版本复用本地证据，旧文件消失或已知变更无法纳入时，旧日期会被停用。

显式查询上传文档/最新文件时加 `--document-mode refresh`；需要仅使用当前证据时
加 `--document-mode existing`。结果的 file_library_check 显示检查/跳过、更新、
待审核及失败信息。文件库访问失败会明确返回 partial，不声称已检查最新课程文档。

## 安装和查询

在项目根目录执行，无须激活环境或运行 PowerShell 脚本：

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[dev,ocr]"
```

本地 `.env` 设置 CANVAS_BASE_URL、CANVAS_TOKEN、CANVAS_TIMEZONE（默认
Asia/Singapore）。保留的 CANVAS_API_TOKEN 名称仍兼容；token 不会打印或复制进技能。
参考 `.env.example`。可选 CANVAS_COURSE_IDS（数字 ID）限定课程。OCR 默认使用
CPU；`CANVAS_OCR_DEVICE=cpu`、`CANVAS_OCR_DPI=150` 和
`CANVAS_OCR_MIN_CONFIDENCE=0.90` 适合当前电脑。

```powershell
.\.venv\Scripts\python.exe -m canvas_ddl.cli.main courses
.\.venv\Scripts\python.exe -m canvas_ddl.cli.main deadlines --time-intent-file .\time-intent.json --type assignment
.\.venv\Scripts\python.exe -m canvas_ddl.cli.main deadlines --time-intent-file .\time-intent.json --type exam
.\.venv\Scripts\python.exe -m canvas_ddl.cli.main upcoming --days 14 --type quiz
.\.venv\Scripts\python.exe -m canvas_ddl.cli.main deadlines --time-intent-file .\time-intent.json --course "COURSE101" --limit 1
```

Skill 由 Codex 理解时间，输出 TimeIntent，再由 Python 验证并计算实际范围。
自然语言不限于上述示例；整周、滚动天数、星期区间、月初 N 天和月底之前均可表达。
“最近”等有实质歧义的说法先澄清；周按周一至周日计算，范围在答案中展示。
引擎不解析自然语言，也没有时间短语白名单或正则回退。结构化调用使用
--time-intent JSON 或 --time-intent-file <UTF-8 JSON 文件路径>，不能同时使用
--start/--end；后两者只接受用户或已验证锚点给出的明确 ISO 时间戳。
详见 [时间意图接口](skills/canvas-ddl/references/time-intent.md)。
返回 UTF-8 JSON。保留四个技能脚本，已安装 `canvas-ddl` 个人技能；重启/新会话后：

```text
$canvas-ddl 我下周有什么作业和考试？
```

技能从项目或 CANVAS_DDL_HOME 找到同一个引擎，读取已 ingestion 的证据，
不自行读源文档、抽取日期、处理冲突或批准官方来源。

## 官方文档 ingestion

首次官方来源批准是显式维护；后续文档依赖查询可通过引擎自动检查及更新已授权版本。

### 使用现有 Canvas .env 自动准备

无需手动下载和填写课程/链接/hash，运行以下课程范围内的准备命令：

```powershell
.\.venv\Scripts\python.exe -m canvas_ddl.cli.main prepare-documents --course COURSE101 --course COURSE102
```

引擎通过 Canvas Files API 查找文件名含 syllabus、outline、handout 等提示的可访问
受支持文档，最多下载20份，保存在 documents/downloaded，并生成 documents/pending-review.json。
提示词只用于发现候选，不证明官方性；权限失败、没有匹配和下载失败均明确返回。
Canvas 公布的文件存储域名可用于下载，其他域名需 CANVAS_DOCUMENT_HOSTS 明确配置；
Canvas token 只发送到 Canvas 本身，签名下载 URL 不写入登记表或输出。
下载域名允许列表与文档官方来源批准是两个独立规则。

当前解析器支持 PDF、DOCX、PPTX、XLSX、CSV、TXT/Markdown、RTF、HTML 和
PNG/JPEG/WebP/TIFF/BMP。Canvas Syllabus 和已发布 Pages 也会在刷新阶段生成 HTML
候选。DOC/PPT/XLS、ZIP 和音视频转录暂不处理。所有新来源先进入 pending review；
成功解析不等于官方性。来源位置分别记录为页码、幻灯片、段落/表格行、工作表行、
文本行、HTML block 或图片。

维护者查看实际 PDF 和官方课程页面，确认官方性、文档类型与适用教学期间后，
执行显式批准命令；请将下面的姓名和日期替换为实际值：

```powershell
.\.venv\Scripts\python.exe -m canvas_ddl.cli.main approve-document --document canvas-12345-67890 --approved-by "你的姓名" --valid-from "YYYY-MM-DD" --valid-until "YYYY-MM-DD" --confirm-official
```

此命令批准待审核记录、保留其他已登记文件，然后自动运行 ingestion；重复准备
不会覆盖已有批准。auto_refresh=false 的变更版本需审核；true 只授权原文件 ID 的
后续版本，不会批准新的来源。pending 文件中的 active=false、
空 approved_by 和未知教学期间会阻止它直接参与查询。不要让 Codex代替你填写批准。
没有匹配名称的课程 PDF 仍可按下面的手动流程登记；当前不扫描公告、邮件或所有附件。

接口依据 [Canvas Files API](https://developerdocs.instructure.com/services/canvas/resources/files)，
下载域名依据 [Canvas 官方域名说明](https://community.instructure.com/en/kb/articles/485223-canvas-domain-email-and-server-management)。

### 手动登记

1. **维护者确认来源**：核对 PDF 来自课程官方 syllabus、course outline、handout
   或明确官方课程文件，确认课程及教学期间。文件名或自称“官方”不构成批准。
2. 下载并保存 PDF，在 `documents/registry.json` 登记已审核记录；字段见
   `documents/registry.example.json`。填写实际 approved_by，不得让 Codex/LLM
   虚构人为批准。路径相对于 registry 文件所在目录。
3. 记录其 SHA-256，并运行 ingestion。默认允许 Canvas 本身的域名；其他学校域名
   必须先在 CANVAS_DOCUMENT_HOSTS 中明确批准（逗号分隔，精确 hostname）。

```powershell
# 获取本地 PDF 内容哈希，填写 registry 的 sha256 字段
Get-FileHash -LiteralPath '.\documents\Course Outline 2026.pdf' -Algorithm SHA256

# 以下 ID 必须已存在于人工审核后的 registry
.\.venv\Scripts\python.exe -m canvas_ddl.cli.main ingest --document cs3244-outline-2026

# 查看 ingestion 状态、confirmed/unresolved/rejected 候选信息
.\.venv\Scripts\python.exe -m canvas_ddl.cli.main documents
```

sha256 字段要求 64 位小写十六进制。registry 还要求 course_id/code/name、文档类型、
名称、官方 source_url、有效课程日期范围、approved_by。原文档变更后须更新批准的
hash 并重新 ingestion，或由明确授权的 auto_refresh 流程完成；active=false 可撤销登记。
未批准、哈希不符或课程不符不能导入。自动更新保留 version_history 和真实批准者；
refresh_blocked=true 会将已知过期的版本排除查询。允许同源更新的文档需在本地 registry 中明确设置 auto_refresh。

项目初始 registry 为空；当前登记/导入状态可用 `documents` 命令查看。
这份登记是受信任的本地管理配置；填写 URL 本身不能证明本地文件真的来自该地址。

```text
DocumentParser → DeadlineExtractor → DeadlineCandidate → DeadlineValidator
→ SQLite 文档证据（pages、proposals、validation audit）
```

默认 extractor 为规则式；可注入 LLM proposal extractor，但其候选不能直接成为事实。
validator 独立核对整行原文、页码、标题、明确日期/年份/时刻、截止或安排语义、
课程和已批准内容版本。最终查询读取持久化证据并重新验证；此前更新阶段仅在需要
入库新内容时解析 PDF，未变更的文档不会每次重新解析。

当前支持 ISO yyyy-mm-dd、中文完整年月日、英文完整/缩写月份日期及单个 24 小时
HH:MM。无年份、暂定/否定、多个日期或时刻、复杂表格布局、AM/PM/外部时区等需要
review；文档所在页有 tentative/draft 等标记时同样不会自动确认。
Week 7 Friday 等在 ingestion 时保留为 unresolved；查询时若 Canvas Calendar Events
或课程官方 ICS 日历提供明确 Week 标签，或唯一首次上课锚点及 Recess Week 记录，引擎会生成单独的
reference_deadlines 参考日期范围。该范围不进入确认 count，也不依赖外部 academic
calendar。ICS 仅接受同源 Canvas 课程 feed、请求不携带 API token、URL 不写入结果。
扫描版或
文字层不足的页面在 ingestion 时使用本地 PP-OCRv6 Small；普通文字 PDF 不启动 OCR。
OCR 只提供带页码与置信度的文字，仍须经过同一个规则 extractor 和独立 validator。
低于阈值的日期保留为 unresolved；依赖、模型或推理失败会标记覆盖不完整。

## 查询结果与冲突

```text
实时 Canvas + 已 ingestion 官方证据
→ normalize → classify → validate → reconcile → deduplicate
→ filter → sort → limit → count
```

- 同课程、明确同一 logical item：同日期合并；日期不同时 Canvas 优先，conflicts
  保存文档替代值与完整来源。仅日期的 PDF 可与同一天精确 Canvas 时间一致。
- 仅官方 PDF 有该记录：经验证后成为 canonical deadline，提交状态为 unknown。
- 课程/名称/多次考试身份不明确：不擅自合并；待处理项出现在 unresolved_deadlines，
  不计入总数。没有 Canvas 的冲突 PDF 也不会随意选“最新的一份”。
- 当前学生的 Canvas due=null 不会被旧 PDF due 复活；unlock/lock 不是 due。
- reconciliation 在范围筛选前进行；必要时查询选中课程跨日期的 calendar，防止
  延期后的 Canvas 记录消失而旧文档日期仍然被计数。
- count 等于返回列表长度（经过 limit）；matched_count/course_counts 为 limit 前
  的引擎计数，所有 unresolved 候选不参与计算。
- reference_count/reference_deadlines 是 Canvas 日历解析出的高召回参考安排，必须
  标明未确认、日期精度、置信度和来源，不并入确认数量。
- ok/complete=true 仅覆盖实时支持来源和**登记的文档 inventory**，不是所有课程文件。
  未登记文档会有 scope warning；partial 零条不能说明没有考试。
- freshness 区分 live 与已 ingestion 文档，文档 validation/ingestion time 不是实时
  PDF 读取时间。Canvas 失败不会悄悄返回旧 Canvas cache。

SQLite 默认在 data/documents.sqlite3，只存 ingestion artifacts；可通过
CANVAS_DOCUMENT_STORE 和 CANVAS_DOCUMENT_REGISTRY 改位置，路径相对于 `.env`。
不处理公告、邮件、聊天、非官方笔记，不加入 RAG/vector DB 或教学内容问答。

## 验证与规范

```powershell
.\.venv\Scripts\python.exe -m pytest -q
```

默认测试为离线模拟数据与生成的 PDF，不需要真实 token。
[PRD](docs/PRD_DDL_ONLY.md)、[Architecture](docs/ARCHITECTURE.md)、
[开发规则](docs/AGENTS.md)、[运行技能](skills/canvas-ddl/SKILL.md) 同步定义 v0.9。
[验证记录](docs/VERIFICATION.md) 记录此次执行结果和边界。

PDF parser 依据 [pypdf 官方文档](https://pypdf.readthedocs.io/en/stable/user/extract-text.html)；
OCR 依据 [PaddleOCR 官方安装说明](https://www.paddleocr.ai/main/en/version3.x/installation.html)
和 [PP-OCRv6 OCR pipeline](https://www.paddleocr.ai/main/en/version3.x/pipeline_usage/OCR.html)。

第一次遇到扫描页时会自动下载 `PP-OCRv6_small_det` 与
`PP-OCRv6_small_rec` 到用户的 PaddleX 模型缓存。模型随后离线复用；OCR 只在
新文件或变更文件 ingestion 时运行，最终 deadline 查询读取 SQLite 缓存，不占用
常驻显存。Windows CPU 运行时固定使用 PaddlePaddle 3.2.x；3.3.1 已知在该组合上
可能触发 oneDNN 不支持的算子。

## 全课程 PDF 考试检查（v0.5）

“下周有什么考试/几门考试”自动检查所选课程的所有 PDF，不按文件名筛选，也没有
默认20份上限。上述 prepare-documents 是单独的显式维护命令，其文件名提示与20份
默认限制不适用于自动考试查询。Files 禁止访问时尝试 Modules 文件链接，并标为部分
覆盖；访问、下载、解析及空白页失败均保留，不声称已经成功读完所有文件。

新 PDF 可以先解析并保存原文、页码、候选及验证审核到独立的
`data/documents.pending.sqlite3`；官方性和课程期间未批准前仍是未确认参考，
不能进入 canonical deadline 或考试计数。已批准来源继续使用原事实库和冲突规则。
相同 metadata/hash 复用已解析内容，新版本按授权更新，最终查询只读取持久化证据。

结果新增 document_content_matches：引擎检索全部已保存页面的考试关键词及上下文，
覆盖跨行安排。每份文档最多返回10段、每段1500字符并显示截断信息；关键词匹配
不是考试数量，统计学的 test、举例中的 exam 也不是考试安排。Skill 只呈现相关
引擎证据及风险，不自行读取 PDF、批准来源或将原文变成最终日期事实。
PDF 解析支持能以空用户密码正常打开的 AES 文件；需要实际密码的文件仍安全失败。
