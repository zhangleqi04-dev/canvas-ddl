# canvas-ddl 查询流程图

这张图说明问题如何经过文件库检查、多来源证据处理和结构化计数，最后形成回答。

```mermaid
flowchart TD
    A["你的问题<br/>例如：下下周有什么事？"] --> B["Codex Skill<br/>理解问题并输出 TimeIntent"]
    B --> T["Python 验证时间意图<br/>按当前时钟计算范围"]
    T --> C["DeadlineService<br/>引擎统一处理查询"]
    C --> D{"需要检查文件库？"}

    D -->|"默认 auto／强制 refresh<br/>所有 DDL 查询都检查"| E["检查所选课程全部受支持文档<br/>＋Canvas Syllabus／Pages<br/>Files 不可用时尝试 Modules"]
    D -->|"用户明确选择 existing"| H["查询或复用现有证据"]

    E --> F{"文件版本或解析规则有变化？"}
    F -->|"没有变化"| G["复用已保存的内容"]
    F -->|"新文件／变化／尚未解析"| O["按格式解析来源单元<br/>PDF／图片必要时本地 PP-OCRv6 Small"]
    O --> P["结构切块 → 宽时间线索粗筛<br/>→ Codex 语义审核"]
    P --> V["Codex 输出原文日期＋ISO 标准日期<br/>Python 验证映射、课程、哈希与来源身份"]
    G --> H
    V --> H

    H --> I["实时 Canvas ＋ 已验证文档事实<br/>语义审核未完成时整份文档暂不计入"]
    I --> J["统一格式、分类、验证"]
    J --> K["协调多来源一致与冲突"]
    K --> L["去重 → 筛选 → 排序 → 计数"]
    L --> M["结构化结果<br/>已确认事项＋文档参考＋风险"]
    M --> N["Codex Skill 整理答案<br/>附来源、位置和不确定性"]
```

所有 DDL 查询都会检查选中课程的可访问受支持文档和 Canvas 课程内容；相同版本且解析审核有效时复用内容。
认证 Canvas 课程文件自动登记，不等待人工批准；外部／手工未受信来源和未通过验证的候选
只作为参考，不参与已确认事项计数。
Files 权限失败后的 Modules 检索属于部分覆盖；失败与不确定性随结果返回。

详细契约见 [ARCHITECTURE.md](ARCHITECTURE.md) 和 [运行 Skill](../skills/canvas-ddl/SKILL.md)。

时间意图由 Codex 理解；实际范围只由 Python 计算，文件更新期间保持同一范围。
