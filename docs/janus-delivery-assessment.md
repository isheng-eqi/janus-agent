# Janus 交付质量评判报告

> **评判对象**：Janus 多 Agent 系统生成的 `AgentCanary分析报告.md`
> **对比基准**：`agentcanary_ground_truth.md`（对 AgentCanary 全部源码的逐行审查）
> **评判日期**：2026-07-16

---

## 一、总体裁决：❌ REJECTED — 交付物无效

Janus 交付了一份标题为「AgentCanary 项目分析报告」的文档，但内容描述的 **根本不是 AgentCanary**。Janus 分析了它自己（Janus 框架），把分析结果的标题写上 "AgentCanary"，当作任务交付。

**这不是细节错误，是交付物完全张冠李戴。用户拿到的是一份分析 Janus 的报告，却被告知这是 AgentCanary 的分析。** 就像你请人评测一款手机，他交回来一篇评测他自己评测工具的报告——数据可能是真的，但与你要的东西毫无关系。

---

## 二、逐维度对比

### 2.1 事实准确性：0/10

| 地面真相（AgentCanary 实际） | Janus 报告声称 | 判定 |
|---|---|---|
| CLI 驱动的自主 AI 渗透测试工具 | "分层递归任务分解 Agent 框架" | ❌ 完全错误 |
| 核心入口 `chat.py` 438 行 | 花 492 行篇幅分析 Gatekeeper (496行), Planner (812行) 等 | ❌ 张冠李戴 |
| 5 层攻击面：L1 注入→L5 多轮越狱 | Gatekeeper→Planner→Worker→Reviewer 四层架构 | ❌ 完全错误 |
| 约束驱动 Memory（双态模型、Self-consolidation） | 零提及 | ❌ 完全遗漏 |
| tools 目录 7 个攻击工具文件 | "9 个工具"（read_file/write_file/terminal…） | ❌ 这是 Janus 的 Worker 工具 |
| 约 1,869 行代码 | 声称分析 "12 个模块"，列出的是 Janus 核心文件 | ❌ 行数、文件名全部对不上 |
| `security.py` 13 个注入模式 | 零提及 | ❌ 完全遗漏 |
| `tools/discovery.py` 自动发现管线 | 零提及 | ❌ 完全遗漏 |
| `tools/binary.py` PE/ELF/Mach-O 逆向分析 404 行 | 零提及 | ❌ 完全遗漏 |
| DeepSeek 驱动，通过 OpenAI SDK | "deepseek-v4-pro"（这点碰巧蒙对了） | ⚠️ 仅此一点正确 |

**总结：报告中关于 AgentCanary 的事实正确率接近 0%。报告中描述的是一个叫 Janus 的分层 Agent 框架——碰巧 Janus 是执行分析的那个多 Agent 系统。**

### 2.2 信息完整性：2/10

对照地面真相列出的 10 项「必须覆盖的内容」：

| # | 必备内容 | Janus 报告是否覆盖 |
|---|---|---|
| 1 | 项目定位：自主 Agent（非固定扫描器） | ❌ 描述成了"分层 Agent 框架" |
| 2 | 5 层攻击面：L1-L5 工具覆盖 | ❌ 完全遗漏 |
| 3 | 架构核心：ChatLoop 编排 + LLM 工具调用循环 | ❌ 描述了 Janus 自己的四层架构 |
| 4 | Memory 机制：约束驱动、双态模型、Self-consolidation | ❌ 完全遗漏 |
| 5 | 工具清单：20+ 工具及分层归属 | ❌ 列出的是 Janus 的 9 个 Worker 工具 |
| 6 | 安全层：注入扫描 + 执行边界 | ❌ 完全遗漏 |
| 7 | 安全问题：shell 注入风险、明文 API Key | ❌ 完全遗漏 |
| 8 | 依赖：openai/httpx/rich + pyyaml/pydantic 冗余 | ❌ 完全遗漏 |
| 9 | 代码行数：约 1,869 行，14 个功能文件 | ❌ 声称覆盖 12 个模块(Janus 的模块) |
| 10 | 与 Hermes 的关系 | ❌ 完全遗漏 |

**10 项必覆盖内容，0 项覆盖。交上来的是一份关于错误项目的优质报告。**

### 2.3 用户体验：1/10

- **标题对**：报告号称分析 AgentCanary ✓
- **内容错**：实际分析的是 Janus ✗
- **信任伤害**：用户拿到报告后可能短时间不会发现错误（报告本身写得很专业、有模有样），直到实际使用才发现完全不对——这种「看起来专业但完全是错的」比「看起来粗劣」更危险
- **唯一得分**：报告格式美观，有目录、有表格、有代码块、有 emoji——如果它真的是分析 AgentCanary 的，会是一份好报告

### 2.4 分析深度：4/10

- 对「被错误地分析了的 Janus 系统」而言，分析深度不错：四层架构说清楚了，数据流跟踪了，工具系统分解了
- 对「应该分析的 AgentCanary」而言，深度为零
- 报告引用的数据来源（`check/validation_result.json`、`docs/janus-full-summary.md`、`analysis/failure-root-cause-analysis.md`）全部是 Janus 自己的文档——说明 Worker 确实在认真「分析」，只是分析了错的对象

---

## 三、根因分析：错在哪个环节？

### 3.1 直接原因：Worker 目标混淆

Janus 自己的根因分析报告（`analysis/failure-root-cause-analysis.md`）部分识别了这个问题：

> "问题二：Worker 目标混淆——读了错误项目的文件"

但这描述得太轻了。真正发生的是：

1. **Gatekeeper** 将用户指令"分析 agentcanary 项目"翻译成 Directive，但 `directive.context` 为空
2. **Planner** 分解任务时，LLM 生成的 TaskSpec 中 `context` 也是空——没有告诉 Worker **AgentCanary 在哪**
3. **Worker** 收到模糊任务"分析项目中的 Python 文件"，在没有任何路径指引下，**默认分析了当前工作目录**，而当前工作目录恰好是 Janus 自己的目录（`C:\Users\HI\Desktop\janus\`）
4. Worker 认真分析了 Janus 源码，产出了一份结构完整、数据翔实的分析——只是分析了错的项目
5. **Reviewer** 审查的是"分析报告有没有达到验收标准"，它检查的是文档结构、引用格式、模块覆盖数量，**没有检查报告分析的是否是用户要求的项目**

### 3.2 系统性根因（按责任排序）

#### 根因 1：Gatekeeper 没有把用户的目标项目路径注入 Directive

`agentcanary_ground_truth.md` 明确存在于 `C:\Users\HI\Desktop\agentcanary\`，Gatekeeper 的 `_formulate_directive()` 本应在解析"分析 agentcanary 项目"时将路径信息写入 `Directive.constraints` 或 `Directive.intent`。但它没有这样做。

**责任归属**：Gatekeeper（战略层失误）

#### 根因 2：Planner 没有追问或补全缺失的上下文

Planner 拿到 `context=""` 的 Directive 后，直接分解出了模糊的 TaskSpec，没有检测到关键信息缺失并追问用户或 Gatekeeper。

**责任归属**：Planner（战术层失职）

#### 根因 3：Worker 在路径模糊时没有询问，直接假设当前目录是目标

Worker 收到的任务 "分析项目中的 Python 文件" 加上空的 context，居然**没有反问**"分析哪个项目？"就动手执行了。它在执行工具调用时发现了 `core/` 目录，自然认为这就是目标。

**责任归属**：Worker（执行层缺乏验证意识）

#### 根因 4：Reviewer 只审查形式，不审查实质正确性

Reviewer 的审查标准围绕"模块覆盖数"、"引用格式"、"字段完整性"展开——它验证了报告的结构质量，但**从未核对报告内容是否与用户原始需求一致**。五级裁决体系完全未能检测到"分析了错的项目"这种致命缺陷。

这是最严重的设计缺陷：**Reviewer 审查的是"Worker 有没有按要求完成任务说明"，而不是"Worker 有没有完成用户的真实需求"。**

**责任归属**：Reviewer（审计层设计缺陷）

#### 根因 5：Gatekeeper 的 Recovery Loop 在错误方向上迭代

Recovery Loop 在发现失败后重新诊断、重新制定策略、重新执行——但它诊断的是"审查为什么没通过"（截断冲突、验收标准），而不是"Worker 有没有分析对项目"。它在错误的轨道上越走越远。

**责任归属**：Gatekeeper（恢复策略的上下文局限）

### 3.3 责任排序

| 优先级 | 根因 | 环节 | 严重程度 |
|:---:|---|---|:---:|
| 1 | 用户目标信息在传递链中丢失（路径不在 Directive 中） | Gatekeeper | 🔴 致命 |
| 2 | Reviewer 不核对产出与用户原始需求的对应关系 | Reviewer | 🔴 致命 |
| 3 | Planner 在上下文缺失时不追问 | Planner | 🟡 严重 |
| 4 | Worker 在模糊指令下不确认就执行 | Worker | 🟡 严重 |
| 5 | Recovery Loop 在错误方向上迭代，未察觉根本问题 | Gatekeeper | 🟡 严重 |

---

## 四、改进建议

### 4.1 短期（不改变架构，只改 prompt）

1. **Gatekeeper 的 `_formulate_directive` prompt** 中增加：
   > "If the user mentions a project name or file path, you MUST include the full absolute path in the directive.constraints field."

2. **Planner 的 `_plan` prompt** 中增加：
   > "If critical information is missing (e.g. file paths, project directories), flag the task as UNPLANNABLE instead of guessing."

3. **Worker 的 system prompt** 中增加：
   > "If a task asks you to analyze 'a project' without specifying the path, ask for clarification before proceeding."

4. **Reviewer 的审查 prompt** 中增加：
   > "CROSS-CHECK: Does the report content match the user's original goal? If the user asked to analyze project X, does this report actually describe project X?"

### 4.2 中期（结构调整）

5. `Directive` 增加 `target_path` 字段，Gatekeeper 强制填充
6. `TaskSpec` 增加 `project_root` 字段，Worker 在执行前 `os.chdir(spec.project_root)`
7. Reviewer 增加「目标匹配」审查维度：检查产出是否与用户原始 goal 对应

### 4.3 长期（架构级）

8. 恢复循环中增加「产出与实际需求的语义匹配度」检查
9. 引入独立的「事实核查」机制——不依赖 LLM 判断，而是用代码验证关键事实（如：报告分析的模块是否真的存在于目标项目中）

---

## 五、最终评判

| 维度 | 评分 | 说明 |
|---|---|---|
| 事实准确性 | 0/10 | 分析了错的项目，AgentCanary 的真实信息几乎为零 |
| 信息完整性 | 2/10 | 10 项必备内容覆盖 0 项（唯一得分来自格式完整性） |
| 用户体验 | 1/10 | 交付物标题正确但内容张冠李戴，发现前会误导用户 |
| 分析深度 | 4/10 | 对「被误分析了的 Janus」分析得不错，但对目标项目分析深度为零 |
| **综合** | **1.5/10** | **不及格。交付物无效，无法使用。** |

### 一句话总结

**Janus 交了一份格式漂亮、分析深入、引用规范、结构完整的报告——只是分析了它自己而不是 AgentCanary。问题出在 Gatekeeper→Planner→Worker→Reviewer 全链条的目标信息丢失，最致命的是 Reviewer 的审查体系完全未能检测到「产出与需求不匹配」这一最根本的质量问题。**
