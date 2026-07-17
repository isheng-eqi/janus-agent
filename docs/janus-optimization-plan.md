# Janus 优化计划：人类管理模式 → Agent 架构映射

> **基于**：`janus-full-summary.md`（Janus Phase 4 现状）× `human-management-patterns.md`（六领域管理模式）
> **方法论**：逐条比对 25 个人类管理模式 → 判断 Janus 实现状态 → 按价值/复杂度排序
> **日期**：2026-07-16

---

## 总览：25 个模式的实现状态分布

| 状态 | 数量 | 占比 |
|------|------|------|
| ✅ 已对齐 | 11 | 44% |
| 🟡 可优化（小改） | 8 | 32% |
| 🔵 待实现（新功能） | 10 | 40% |
| ⚫ 不建议（过度设计） | 6 | —（与上面有重叠） |

> 注：部分模式既属"可优化"又属"不建议全量做"——表中分开统计，实际独立模式共 25 个。

---

## 已对齐（不需要改）

以下 11 个人类管理模式已在 Janus 中实现良好，无需修改：

| # | 人类模式 | 来源 | Janus 落地 | 证据 |
|---|---------|------|-----------|------|
| 1 | **任务式指挥**（Mission Command） | 军事 | TaskSpec 含 `intent` + `constraints`，Worker 知道"为什么" | 9 条已应用规则 #1 |
| 2 | **参谋/一线分离**（Staff/Line Split） | 军事 | Gatekeeper（零工具）→Planner（零工具）→Worker（有工具） | 9 条已应用规则 #2 |
| 3 | **三权分立**（Separation of Powers） | 政府 | Gatekeeper（立法/定规则）、Worker（行政/执行）、Reviewer（司法/裁决）三角 | 9 条已应用规则 #3 |
| 4 | **绩效改进计划**（PIP） | 企业 | `_make_retry_spec()` 注入具体问题 + 严重度标签，不是"重做" | 9 条已应用规则 #5 |
| 5 | **缺陷等级分类**（Defect Classification） | 制造 | `Severity` 四级：CRITICAL/MAJOR/MINOR/SUGGESTION | 9 条已应用规则 #7 |
| 6 | **监察长独立审计**（Inspector General） | 政府 | Reviewer 独立于 Worker，零工具，直接向 Gatekeeper 报告 | 9 条已应用规则 #3 |
| 7 | **质量检查点**（Quality Checkpoints） | 制造 | Planner 级审查 + Worker 子审查 + Reviewer 终审 | 9 条已应用规则 #4 |
| 8 | **司法审查标准**（Standards of Review） | 司法 | `ReviewVerdict` 五级裁决替代二元 pass/fail | 9 条已应用规则 #6 |
| 9 | **指挥链**（Chain of Command） | 军事 | Gatekeeper→Planner→Worker，每层看不同粒度信息 | 9 条已应用规则 #8 |
| 10 | **管理幅度**（Span of Control） | 企业 | `context_discipline_prompt`——"summarize to one line" | 9 条已应用规则 #9 |
| 11 | **编辑-评审者-作者三角** | 学术 | Gatekeeper（编辑）、Reviewer（评审者）、Worker（作者），三方独立 | 架构映射明确 |

**结论**：Janus 的核心架构基因已经覆盖了人类管理的精华。剩余优化空间主要在"细化执行"和"补缺漏"，而非结构性重构。

---

## 可优化（小改）

以下 8 个模式在 Janus 中已有雏形，但需要小幅度调整即可发挥更大价值。

---

### 🟡 OPT-1：验收标准 [HARD]/[SOFT] 标签

**人类模式**：司法"三层审查标准"（§4.1）——不同标准适用不同推翻门槛。`[HARD]` 标准零容忍（de novo），`[SOFT]` 标准只在明显错误时打回（clearly erroneous）。

**当前状态**：部分实现。Janus 有四级 Severity 和五级 ReviewVerdict，但 `acceptance_criteria` 是纯文本，没有结构性 `[HARD]`/`[SOFT]` 标签。Reviewer 靠 LLM 自行判断哪些标准更关键——不稳定。

**改什么**：
- `TaskSpec.acceptance_criteria` 规范化为分层格式（向后兼容——纯文本仍可工作）
- Reviewer prompt 增加指引：`[HARD]` → 零容忍 → REJECTED/MAJOR_REVISIONS；`[SOFT]` → 明显错误才打回 → MINOR_REVISIONS

**改哪里**：
1. `core/protocol.py`：`TaskSpec` 文档注释增加 `[HARD]`/`[SOFT]` 格式说明
2. `core/planner.py`：`_PLAN_SYSTEM_PROMPT` 增加"验收标准标注指引"——要求 LLM 为每条标准加 `[HARD]` 或 `[SOFT]` 前缀
3. `core/reviewer.py`：`_REVIEW_SYSTEM_PROMPT` 增加分级审查逻辑——`[HARD]` 不满足 → CRITICAL/MAJOR；`[SOFT]` 不满足 → MINOR/SUGGESTION

**预估影响**：⭐⭐⭐⭐ 高。直接减少 Reviewer 因"变量命名"之类问题反复打回 Worker 的 Token 浪费。改动量约 50 行，风险低。

---

### 🟡 OPT-2：重审范围限制——只审上次标记的问题

**人类模式**：司法"上诉流程"（§4.2）——上诉法院只审下级法院审过的问题，不审新问题。防止无限重审。

**当前状态**：未实现。当 Worker 重试后，Reviewer 全量重新审计——如果 Worker 在第 1 轮被指出"缺 X"，第 2 轮补上了 X，Reviewer 可能又挑出 Y 和 Z 的新问题，导致 Worker 陷入无尽的重试循环。

**改什么**：
- `_make_retry_spec()` 在 retry TaskSpec 中注入 `previous_issues` 字段（上一轮 Reviewer 标记的问题清单）
- Reviewer 在 retry 模式的 prompt 中被告知：**只验证上一轮标记的问题是否已修复**，不审计新问题（除非新问题属于 CRITICAL 级别）

**改哪里**：
1. `core/planner.py`：`_make_retry_spec()` 从 ReviewResult 提取 issue 清单，注入 retry TaskSpec
2. `core/reviewer.py`：retry 模式 prompt 增加"scope: only verify fixes for: [issue list]"

**预估影响**：⭐⭐⭐⭐ 高。解决 Janus 重试循环中最常见的痛点——"修了 A 被打回 B，修了 B 被打回 C"。改动量约 80 行，需测试重试协议不受影响。

---

### 🟡 OPT-3：Gatekeeper 推翻 Reviewer 裁决的显式权力

**人类模式**：学术"编辑-评审者-作者三角"（§6.1）——编辑（Gatekeeper）读评审意见但做最终决定，可以推翻评审者的建议（"评审者太严了，录用"）。

**当前状态**：部分实现。Gatekeeper 读取 `ExecutionReport` 做最终汇报，但 `ExecutionReport` 中的 `passed/failed` 是由 Reviewer 的 ReviewVerdict 决定的。Gatekeeper 没有显式的"override"机制。

**改什么**：
- `ExecutionReport` 增加 `gatekeeper_overrides: list[dict]` 字段——Gatekeeper 可以覆写个别 Reviewer 裁决
- 在 `_report_to_user()` 中，Gatekeeper 的 prompt 增加"你可以推翻 Reviewer 的裁决，但必须给出理由"
- **不强制 Gatekeeper 逐条审查**——仅当 Gatekeeper 在汇总时发现明显不合理时才覆写

**改哪里**：
1. `core/protocol.py`：`ExecutionReport` 增加 `gatekeeper_overrides` 字段
2. `core/gatekeeper.py`：`_report_to_user()` 的 prompt 增加覆写指引

**预估影响**：⭐⭐⭐ 中等。减少 Reviewer 过度严格导致的误杀。代价是增加一次 Gatekeeper LLM 调用的推理复杂度。改动量约 60 行。

---

### 🟡 OPT-4：Desk Reject——送 Reviewer 前的轻量级预筛选

**人类模式**：学术"Desk Reject"（§6.2）——编辑在送审前做快速判断，明显不合格的稿件直接拒稿，不浪费评审者时间。Nature 统计约 30-50% 投稿在此阶段被过滤。

**当前状态**：未实现。所有 Worker 产出都送 Reviewer，包括明显不合格的（空结果、跑题、格式错误）。每次都消耗一次 Reviewer LLM 调用。

**改什么**：
- `_dispatch_with_review()` 在调用 Reviewer 前增加规则式预筛选（非 LLM，零 Token 成本）
- 触发 Desk Reject 的条件：
  - Worker 返回 `FAILURE` → 已知 Reviewer 已有快速通道，但这里改为**规则层面跳过**，不发起 Reviewer 调用
  - Worker 的 `TaskResult.result` 是空字符串或 < 20 字符
  - Worker 产出的文件不存在于磁盘（`artifacts` 声明的文件实际不存在）
  - Worker 的 `TaskResult.summary` 与 `TaskSpec.description` 无关键词重叠（明显跑题）
- Desk Reject → 直接返回 `MINOR_REVISIONS`，附带具体问题描述，不消耗 Reviewer Token

**改哪里**：
1. `core/planner.py`：`_dispatch_with_review()` 开头新增 `_desk_reject_check()` 规则函数

**预估影响**：⭐⭐⭐⭐⭐ 极高。每条 Desk Reject 节省一次 Reviewer LLM 调用（~500-2000 token）。在多任务场景中，可能节省 30%+ Reviewer 总成本。改动量约 60 行纯逻辑，无新增 LLM 调用。

---

### 🟡 OPT-5：TaskSpec.validate() 调用——IQC 来料检验

**人类模式**：制造"三道质量防线"（§5.1）——IQC（来料检验）在原材料进入产线前检查。缺陷越早发现，修复成本越低。

**当前状态**：已知缺口。`TaskSpec.validate()` 方法已定义，但从未被调用。TaskSpec 可能带着空 `task_id`、缺失 `description` 等缺陷进入 Worker 执行——到终审才发现，白白浪费 Worker Token。

**改什么**：
- Planner 在 `_plan()` 返回后、分派 Worker 前，对每个 TaskSpec 调用 `validate()`
- 验证失败 → 尝试 LLM 自动修复 TaskSpec（一次轻量调用），仍失败 → 标记为 FAILED（不浪费 Worker Token）

**改哪里**：
1. `core/planner.py`：`execute()` 中 `_plan()` 之后，增加 `_validate_task_specs()` 步骤
2. `core/protocol.py`：确保 `TaskSpec.validate()` 覆盖空 task_id、空 description、空 acceptance_criteria

**预估影响**：⭐⭐⭐ 中等。直接修复已知缺口 GAP-2/NEW-1 中提到的"task_id 可为空"问题。改动量约 50 行。

---

### 🟡 OPT-6：控制跨度软守卫

**人类模式**：企业"控制跨度"（§3.1）——一个管理者有效管理的直接下属为 5-7 人，超过后管理质量急剧下降。

**当前状态**：未显式限制。Planner 可以分解出任意数量的子任务。如果一次分解出 20 个 TaskSpec，Gatekeeper 的审核负担过重，Worker 并行执行的协调也会混乱。

**改什么**：
- Planner prompt 增加"建议分解 3-7 个子任务，最多不超过 10 个"
- 如果 LLM 分解出 > 10 个 → Console 输出警告 + 自动选取前 10 个或要求重新分解
- 不要硬截断——优先保留高优先级任务

**改哪里**：
1. `core/planner.py`：`_PLAN_SYSTEM_PROMPT` 增加跨度指引；`execute()` 增加 `_enforce_span_limit()` 检查

**预估影响**：⭐⭐⭐ 中等。防止 Planner 过度分解导致审核崩盘。Already somewhat implicit in prompts——making it explicit prevents edge cases. 改动量约 40 行。

---

### 🟡 OPT-7：同一 Worker 重试用同一 Reviewer

**人类模式**：学术"修改-重新提交循环"（§6.4）——修改稿通常送回原评审者，以保证评审一致性。

**当前状态**：未实现。每次 `_dispatch_with_review()` 调用 Reviewer 时，不保证是同一 Reviewer 实例（虽然当前只有单实例，但架构上允许不同实例）。

**改什么**：
- Reviewer 实例增加 `session_id` 概念——第一次审查时记录，重试时复用同一实例
- 如果同一任务的重试用了不同 Reviewer，至少注入上一轮的审查上下文

**改哪里**：
1. `core/planner.py`：`_dispatch_with_review()` 中维护 `task_id → reviewer_session` 映射
2. `core/reviewer.py`：支持接收 `previous_review` 上下文

**预估影响**：⭐⭐ 低。当前单 Reviewer 实例下实际影响不大，但为未来多 Reviewer 场景打基础。改动量约 50 行。

---

### 🟡 OPT-8：信息向上传递时显式增加判断

**人类模式**：跨领域"信息向上流"（§7.1）——每层向上传递信息时必须**增加解释（judgment）**，不只是删减细节。

**当前状态**：部分实现。Worker → Reviewer → Gatekeeper 链条已有信息压缩，但压缩方式偏"删减"而非"增值"。例如 `_summarize()` 是纯逻辑聚合，没有 LLM 级别的态势判断。

**改什么**：
- `_summarize()` 从纯逻辑聚合改为 LLM 辅助的态势判断——不只是"3 passed, 1 failed"，而是"3 个任务正常完成，1 个失败的原因可能是 X"
- 给 Gatekeeper 的 `ExecutionReport.summary` 增加"风险判断"和"建议"字段

**改哪里**：
1. `core/planner.py`：`_summarize()` 可选地使用轻量 LLM 调用做态势判断（仅在 verbose 模式或任务数 > 3 时）
2. `core/protocol.py`：`ExecutionReport` 增加 `risks` 和 `recommendations` 字段

**预估影响**：⭐⭐ 低。增值判断提升 Gatekeeper 决策质量，但增加了 Planner 的 LLM 调用成本。建议作为可选项（verbose 模式开启）。改动量约 80 行。

---

## 待实现（新功能）

以下 10 个模式在 Janus 中尚未实现，但按价值/复杂度比值得建设。

---

### 🔵 NEW-1：根因分析——Reviewer FAIL 反哺 Gatekeeper 分解质量 ⭐⭐⭐⭐⭐

**人类模式**：制造"根因分析/5 Whys"（§5.3）+ 跨领域"缺陷溯源"（§7.5）——终检缺陷信息反馈到前端工艺改进。丰田"5 Whys"：反复追问直到找到系统根源。

**当前状态**：完全未实现。当同一模式的失败反复出现（如 3 个 Worker 都在"错误处理"上 FAIL），Janus 只做重试——没有聚合分析，没有反哺前端。

**改什么**：
- `Planner.execute()` 结束后，如果出现"同一类 FAIL 模式 ≥ 2 次"，触发生成 `DecompositionFeedback`
- `DecompositionFeedback` 包含：失败模式描述、建议的 Gatekeeper prompt 改进（如"当任务是实现函数/API 时，[HARD] 标准必须包含异常处理"）
- 反馈存入 Session 上下文，供后续 `_formulate_directive()` 和 `_plan()` 使用
- 不需要持久存储（避免引入 Memory 系统），会话级即可

**改哪里**：
1. `core/planner.py`：`execute()` 末尾增加 `_analyze_failure_patterns()` —— 聚合所有 FAIL 的 Reviewer 反馈，提取共性
2. `core/protocol.py`：新增 `DecompositionFeedback` 数据类
3. `core/session.py`：Session 上下文增加 `decomposition_learnings` 缓存

**预估影响**：⭐⭐⭐⭐⭐ 极高。这是 Janus"自进化"的最小可行实现——不需要完整的 Memory/Skill/Reflection 三层架构，只需在会话内让失败反哺分解。改动量约 120 行。

---

### 🔵 NEW-2：Catchball 可行性预检——执行前的轻量协商 ⭐⭐⭐⭐

**人类模式**：企业"Hoshin Kanri / Catchball"（§3.3）——战略目标不是单向级联，而是上下反复协商直到对齐。在投入大量资源前验证可行性。

**当前状态**：未实现。Gatekeeper → Planner → Worker 纯单向。Worker 执行完才知道不可行。

**改什么**：
- 在 `Planner._plan()` 生成 TaskSpec[] 后，对每个 TaskSpec 做轻量级可行性预检（非 Worker 执行，仅 Planner LLM 快速判断）
- 预检维度：依赖是否就绪（文件路径存在？）、验收标准是否可验证、Token 预估是否合理
- 如果 ≥ 30% TaskSpec 被判定不可行 → Planner 向 Gatekeeper 返回"需要调整"，Gatekeeper 重做 `_formulate_directive()`
- **不是让 Worker 讨价还价**——是在投入 Worker Token 之前验证方向

**改哪里**：
1. `core/planner.py`：`_plan()` 之后增加 `_feasibility_check()` LLM 调用（light: 仅 system prompt + TaskSpec 列表）
2. `core/protocol.py`：`Directive` 增加 `revision_history` 字段（Gatekeeper 重做时记录原因）

**预估影响**：⭐⭐⭐⭐ 高。避免 Worker 花 5 万 Token 才发现 TaskSpec 本身有问题（对应制造"原材料不合格到成品才发现"）。代价：每次多 1 次轻量 Planner LLM 调用。改动量约 100 行。

---

### 🔵 NEW-3：独立监察 Tracer Agent ⭐⭐⭐

**人类模式**：政府"监察机制"（§2.4）——独立于行政体系的监察机构，拥有调查权但无执行权，直接向最高层报告。绕过正常汇报链，提供未经过滤的真实信息。

**当前状态**：未实现。Janus 只有正常链（Worker→Reviewer→Gatekeeper→用户），没有旁路监察。

**改什么**：
- 新增轻量级 `Tracer` 组件（非 LLM Agent——基于规则的统计收集器）
- 监控：Token 消耗是否异常（单 Worker 超 10k 未完成）、Worker 是否连续 3 轮输出相同内容（"敷衍"模式）、重试次数是否异常
- 报告直接注入到 Gatekeeper 的汇报 prompt 中（不经过 Planner）
- **不做实时阻断**——Tracer 只有观察权和建议权

**改哪里**：
1. `core/tracer.py`（新文件，~150 行）：钩子式统计收集
2. `core/gatekeeper.py`：`_report_to_user()` 接收 Tracer 摘要
3. `core/worker.py`：Worker 循环中增加 tracer 钩子（每次 LLM 调用后通知）

**预估影响**：⭐⭐⭐ 中等。解决"Worker 卡死但没人发现"的问题。纯规则实现（无额外 LLM 调用），零 Token 成本。改动量约 200 行。

---

### 🔵 NEW-4：证据标准分级——不同任务类型用不同审查严格度 ⭐⭐⭐

**人类模式**：司法"证据门槛"（§4.3）——刑事案件需"排除合理怀疑"（~95%），民事只需"优势证据"（>50%）。错误的代价决定证据标准。

**当前状态**：未实现。Reviewer 对所有任务使用相同严格度。但安全关键代码和文档注释应有不同的审查标准。

**改什么**：
- `TaskSpec` 增加 `review_rigor` 字段：`strict`（安全/正确性——必须运行测试）、`normal`（功能代码——静态分析+逻辑审查）、`relaxed`（文档/格式——模式匹配）
- Reviewer prompt 根据 `review_rigor` 调整：`strict` → 要求确定性证据（测试通过）；`relaxed` → 抽样检查即可
- 默认 `normal`，`strict` 由 Gatekeeper 在 `_formulate_directive()` 中根据任务类型判断

**改哪里**：
1. `core/protocol.py`：`TaskSpec` 增加 `review_rigor` 字段（`Literal["strict", "normal", "relaxed"]`）
2. `core/gatekeeper.py`：`_FORMULATE_SYSTEM_PROMPT` 增加 rigor 判断指引
3. `core/reviewer.py`：`_REVIEW_SYSTEM_PROMPT` 根据 rigor 调整审查指引

**预估影响**：⭐⭐⭐ 中等。减少文档/格式类任务的过度审查 Token 浪费。改动量约 80 行。

---

### 🔵 NEW-5：关键任务多 Reviewer 并行审计 ⭐⭐⭐

**人类模式**：学术"多评审者并行"（§6.3）+ 跨领域"冗余验证"（§7.4）——单一评审有偏见，多个独立评审减少偏见。分歧本身是信号。

**当前状态**：未实现。所有任务单 Reviewer。

**改什么**：
- 仅对"高优先级"任务（`priority == "quality"`）启用多 Reviewer
- 2 个 Reviewer 并行：一个用默认 prompt，一个用不同视角（如"只审架构"或不同模型）
- 共识规则：全票 PASS → 通过；全票 FAIL → 打回；分歧 → Gatekeeper 介入（读取分歧点，自行判断）
- **不默认开启**——仅 quality 优先级时触发，控制成本

**改哪里**：
1. `core/planner.py`：`_dispatch_with_review()` 检测 `priority == "quality"` 时并行调用 2 个 Reviewer
2. `core/reviewer.py`：支持 `review_perspective` 参数（如 `"correctness"` / `"architecture"`）
3. `core/gatekeeper.py`：`_report_to_user()` 处理分歧情况

**预估影响**：⭐⭐⭐ 中等。quality 优先级任务本就是高 Token 预算场景，增加 1 次并行 Reviewer 调用成本可接受。改动量约 120 行。

---

### 🔵 NEW-6：IPQC 过程检查——Worker 执行中的异常检测 ⭐⭐

**人类模式**：制造"三道质量防线"（§5.1）——IPQC（过程检验）在生产过程中检测，发现缺陷时停机调整。

**当前状态**：未实现。Janus 有 OQC（Reviewer 终审），但无 IPQC。Worker 可能花 5 万 Token 在错误方向上狂奔。

**改什么**：
- Worker 循环中每 N 次工具调用（默认 10）触发一次轻量检查
- 检查维度：最近 3 次工具调用的输出是否相关？是否有循环模式？Token 消耗是否异常？
- 异常 → Console 警告（不中断），严重异常（如连续 5 次相同错误的工具调用）→ 提前终止 Worker
- 不做 LLM 级别 IPQC（太贵）——纯启发式规则

**改哪里**：
1. `core/worker.py`：`_execute_loop()` 中增加 `_mid_execution_check()` 钩子
2. `core/console.py`：增加 IPQC 警告输出

**预估影响**：⭐⭐ 低-中。纯规则检查（零额外 LLM Token），价值在于提前发现明显跑偏。改动量约 60 行。

---

### 🔵 NEW-7：OKR 可追溯链——显式的目标对齐记录 ⭐⭐

**人类模式**：企业"OKR 级联"（§3.2）——每个 KR 对齐上一层的 O，形成可追溯的目标链。

**当前状态**：部分实现。每个 TaskSpec 有 `intent`，但 `intent` 是自由文本——没有结构性追溯。

**改什么**：
- `TaskSpec` 增加 `parent_goal` 字段（指向上层 TaskSpec 或 Directive 的 goal）
- Console 在 verbose 模式下可渲染任务树（`task_id` → `parent_goal` 关系）
- 不强制——`parent_goal` 可为空

**改哪里**：
1. `core/protocol.py`：`TaskSpec` 增加 `parent_goal: Optional[str]`
2. `core/planner.py`：`_plan()` 系统提示词增加 parent_goal 注入指引

**预估影响**：⭐⭐ 低。主要用于调试和追溯，不改变执行逻辑。改动量约 30 行。

---

### 🔵 NEW-8：授权矩阵——显式的决策权限边界 ⭐

**人类模式**：企业"授权矩阵"（§3.5）——每个层级的决策权限边界明确。技术方案 Worker 自主，架构变更 Gatekeeper 决策。

**当前状态**：隐式存在。Worker 自主选择工具和实现方式，Gatekeeper 定方向，但无显式文档或提示词约束。

**改什么**：
- 在 Worker/Planner/Gatekeeper 的 system prompt 中增加"决策权限"段落
- Worker："你可以自主选择：工具、库、实现方式。不可以：改变验收标准、修改任务范围"
- Planner："你可以自主选择：任务拆分方式、Worker 分派策略。不可以：改变战略意图、推翻 Gatekeeper 约束"
- 此改动已在设计文档中定义，只需注入 prompt

**改哪里**：
1. `core/prompts.py`：增加 `decision_authority_prompt(role)` 函数
2. 各层 system prompt 调用此函数

**预估影响**：⭐ 低。主要是文档化现有行为，减少 LLM 越权。改动量约 30 行。

---

### 🔵 NEW-9：Planner 感知多轮对话历史 ⭐⭐⭐⭐

**人类模式**：军事"情报逐级过滤"（§1.5）——每一级接收上一级过滤后的情报，并加入本级判断。

**当前状态**：已知缺口 GAP-2。`Directive` 不携带 `history_context`，Planner 无法感知多轮对话历史，导致复杂多轮任务分解精度下降。

**改什么**：
- `Directive` 增加 `history_context: str` 字段
- `Session._format_history_context()` 的输出注入到 `_formulate_directive()` 中
- Planner prompt 增加"考虑对话历史中的上下文"

**改哪里**：
1. `core/protocol.py`：`Directive` 增加 `history_context` 字段
2. `core/gatekeeper.py`：`_formulate_directive()` 注入历史上下文
3. `core/planner.py`：`_PLAN_SYSTEM_PROMPT` 增加历史感知指引

**预估影响**：⭐⭐⭐⭐ 高。直接修复已知缺口，显著提升多轮对话下的任务分解质量。改动量约 60 行。

---

### 🔵 NEW-10：持续改进的轻量 Memory——会话级失败模式学习 ⭐⭐⭐

**人类模式**：制造"Kaizen/持续改进"（§5.4）——持续小改进累积，不是一次性大改造。

**当前状态**：未实现。每次执行是独立的，不跨任务学习。

**改什么**：
- Session 上下文增加 `learnings: dict[str, str]`——键是失败模式描述，值是已验证的修复策略
- 当 Worker 成功修复某个问题后，Planner 提取"修复模式"存入 learnings
- 后续相同模式的 TaskSpec 自动附带 learnings 中的修复策略作为 context
- **会话级，不跨会话持久化**——避免引入 Memory 系统

**改哪里**：
1. `core/session.py`：增加 `learnings` 缓存和存取方法
2. `core/planner.py`：`_plan()` 和 `_make_retry_spec()` 注入相关 learnings
3. `core/worker.py`：成功修复后通过 TaskResult 回传 learnings

**预估影响**：⭐⭐⭐ 中等。在单会话内让 Janus "越用越聪明"。与 NEW-1（根因分析）互补——NEW-1 是聚合分析，NEW-10 是即时学习。改动量约 80 行。

---

## 不建议（过度设计）

以下模式在人类管理中很重要，但映射到 Janus 当前阶段会过度设计。记录在此以防未来需要时参考。

| # | 模式 | 为什么不建议现在做 |
|---|------|-------------------|
| 1 | **多 Reviewer 默认开启** | 每个任务 2-3 个 Reviewer 并行，Token 成本 2-3x。仅 quality 优先级时开启（NEW-5）是合理折中 |
| 2 | **完整 Hoshin Kanri 双向协商** | 多轮 Gatekeeper↔Planner 反复协商 → 每次协商消耗 LLM 调用。NEW-2 的轻量可行性预检是合理替代 |
| 3 | **Kaizen 完整三层进化**（Memory→Skill→Reflection） | 这是 Hermes 的架构层，不是 Janus 应承担的。NEW-1（会话级根因分析）和 NEW-10（会话级 learnings）是 Janus 范围内的合理实现 |
| 4 | **完整授权矩阵** | 需求本身合理（NEW-8），但做成完整矩阵（含决策类型×角色表格）对 Agent 上下文的边际收益低。prompt 层面的权限约束已足够 |
| 5 | **完整上诉流程**（Worker 可对 Reviewer 裁决上诉到 Gatekeeper） | OPT-3 已给 Gatekeeper 覆写权。Worker 主动上诉 → 增加 Token 且易被滥用 |
| 6 | **实时 IPQC 全量监控** | 需要流式监控和 watchdog 基础设施。NEW-6 的启发式间歇检查是合理的轻量替代 |

---

## 优先级矩阵

按**价值 × 实现成本**排序的推荐实施顺序：

### 🔴 P0：立即做（高价值、低复杂度）

| 优先级 | ID | 改动 | 预估工时 | 核心价值 |
|--------|----|------|---------|---------|
| P0-1 | OPT-4 | Desk Reject 预筛选 | 60 行 / 2h | 节省 30%+ Reviewer Token |
| P0-2 | OPT-1 | [HARD]/[SOFT] 标签 | 50 行 / 2h | 减少过度打回 |
| P0-3 | OPT-5 | TaskSpec.validate() 调用 | 50 行 / 1.5h | 修复已知缺口，防止空 Spec 执行 |
| P0-4 | NEW-9 | Planner 感知多轮历史 | 60 行 / 2h | 修复已知缺口 GAP-2 |

### 🟡 P1：本周做（高价值、中等复杂度）

| 优先级 | ID | 改动 | 预估工时 | 核心价值 |
|--------|----|------|---------|---------|
| P1-1 | OPT-2 | 重审范围限制 | 80 行 / 3h | 消除无尽重试循环 |
| P1-2 | NEW-1 | 根因分析反馈 | 120 行 / 4h | 会话级自进化 |
| P1-3 | NEW-2 | Catchball 可行性预检 | 100 行 / 3h | 避免方向性浪费 |

### 🟢 P2：下个迭代（中等价值）

| 优先级 | ID | 改动 | 预估工时 | 核心价值 |
|--------|----|------|---------|---------|
| P2-1 | OPT-3 | Gatekeeper 覆写权 | 60 行 / 2h | 减少误杀 |
| P2-2 | NEW-3 | Tracer Agent | 200 行 / 5h | 旁路监察，零 Token 成本 |
| P2-3 | NEW-5 | quality 优先级多 Reviewer | 120 行 / 4h | 关键任务冗余验证 |
| P2-4 | NEW-4 | 证据标准分级 | 80 行 / 3h | 按任务类型调整审查严格度 |

### 🔵 P3：有需要时做（较低优先级）

| 优先级 | ID | 改动 | 预估工时 | 核心价值 |
|--------|----|------|---------|---------|
| P3-1 | OPT-6 | 控制跨度软守卫 | 40 行 / 1h | 防过度分解 |
| P3-2 | NEW-10 | 会话级 learnings | 80 行 / 3h | 即时修复模式复用 |
| P3-3 | NEW-6 | IPQC 过程检查 | 60 行 / 2h | 提前发现跑偏 |
| P3-4 | OPT-7 | 同一 Reviewer 重试 | 50 行 / 2h | 审查一致性（当前影响小） |
| P3-5 | OPT-8 | 信息增值传递 | 80 行 / 3h | 提升 Gatekeeper 决策质量 |
| P3-6 | NEW-7 | OKR 可追溯链 | 30 行 / 1h | 调试追溯 |
| P3-7 | NEW-8 | 授权矩阵 prompt | 30 行 / 1h | 文档化现有行为 |

---

## 总结：这次优化的核心叙事

Janus 已经抓住了人类管理模式的**骨架**——四层分离、意图驱动、独立审查、分级裁决。这 11 个已对齐的模式是 Janus 的架构基因，不需要改变。

这次优化的核心叙事是：**从"做了"到"做好"**。

1. **减少浪费**：Desk Reject（避免无效 Reviewer 调用）、[HARD]/[SOFT] 标签（避免过度打回）、重审范围限制（避免无尽重试）——这三项直接砍掉 Janus 最大的 Token 浪费源
2. **引入闭环**：根因分析（FAIL → Gatekeeper 改进）、可行性预检（方向验证 → 避免执行浪费）——让 Janus 从"执行者"变成"学习者"
3. **修复缺口**：Planner 多轮历史、TaskSpec.validate()——把已知但未修复的痛点清零
4. **保持克制**：明确标记的 6 个"不建议"模式——防止"因为人类有所以 Agent 也要有"的过度设计陷阱

**预期总体效果**：P0+P1 共 7 项改动可在约 17 工时内完成，预计减少 30-50% 的 Token 浪费，消除最核心的 3 个已知痛点。

---

> *本文档是 Janus Phase 4 → Phase 5 的优化路线图。每条建议都有明确的人类模式来源、当前状态分析、具体改动位置和预估影响。实施时应按 P0→P1→P2→P3 顺序推进，每完成一级进行集成测试。*
