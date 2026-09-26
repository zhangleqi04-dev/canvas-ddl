# canvas-ddl 架构图

这张图展示对话层、本地引擎、实时 Canvas 数据及多格式课程文档证据库之间的职责和数据流。

```mermaid
flowchart TB
    subgraph UI["对话层"]
        U["你"] --> S["Codex ＋ canvas-ddl Skill<br/>输出 TimeIntent、调用引擎、呈现答案"]
        S --> CLI["本地查询脚本"]
    end

    CLI --> DS["DeadlineService<br/>统一查询入口与流程调度"]
    DS --> R["验证 TimeIntent 并计算实际范围<br/>当前时钟、日历边界、时区与课程匹配"]
    DS --> CC["Canvas 数据采集"]
    DS --> FR["课程文档与 Syllabus／Pages 检查及版本更新"]

    CS["Canvas 结构化数据<br/>Assignments／Quizzes／Calendar 等"] --> CC
    CF["课程文档<br/>PDF／DOCX／PPTX／XLSX／CSV／文本／图片"] --> FR
    CH["Canvas Syllabus／已发布 Pages"] --> FR

    subgraph ING["文档 ingestion：查询前完成"]
        FR --> DP["DocumentParser 格式分派<br/>页／幻灯片／段落／表格行／图片 OCR"]
        DP --> CHUNK["StructuralChunker<br/>生成有位置与哈希的文本块"]
        CHUNK --> PF["LightSemanticPrefilter<br/>只按宽泛时间线索粗筛"]
        PF --> LLM["Codex Skill 语义审核<br/>区分真实安排、示例与学习内容<br/>一个逻辑日期对应一个候选"]
        LLM --> VA["DeadlineValidator<br/>独立验证原文锚点、日期、课程与批准期间"]
        REG["人工维护的来源审批配置<br/>课程、期间、版本及更新授权"] -.-> VA
    end

    VA --> DB["已批准文档证据库<br/>保存原文、语义审核、候选与验证记录"]
    VA --> PB["未批准文档参考库<br/>保持未确认，不产生事实"]

    CC --> FACT["统一 Deadline 处理"]
    DB -->|"仅通过验证的事实"| FACT
    FACT --> REC["DeadlineReconciler<br/>处理一致、冲突和最终日期选择"]
    REC --> DED["DeadlineDeduplicator<br/>避免重复计数"]
    DED --> COUNT["筛选、排序与计数"]

    DB --> SEARCH["已保存来源单元的关键词与上下文检索"]
    PB --> SEARCH

    COUNT --> OUT["查询结果<br/>事项、来源、冲突、完整性与风险"]
    SEARCH -->|"原文参考，不能直接计数"| OUT
    OUT -.-> S
```

Codex 负责理解问题，也只通过引擎发出的有界文本块提出语义候选；
DeadlineService 负责调度，引擎负责验证、最终日期和计数。
图中的两类文档证据库按已有来源审批状态区分，验证器和 Codex 不自行批准来源。
仅文档记载的事项也可在来源获批准、日期通过独立验证后成为已确认事项。
OCR 只在新文件或变更文件 ingestion 时本地运行；置信度不足的 OCR 候选保持未确认，
最终查询只读取已持久化的来源单元证据，并重新验证语义审核能否精确重建候选。
同一事项与实时 Canvas 日期冲突时默认 Canvas 优先，并保留文档替代值与来源。

详细契约见 [ARCHITECTURE.md](ARCHITECTURE.md) 和 [运行 Skill](../skills/canvas-ddl/SKILL.md)。

Codex 把自然语言时间解释为 TimeIntent；TimeRangeResolver 使用实际时钟验证并计算边界，结果附原始意图和时间范围。
