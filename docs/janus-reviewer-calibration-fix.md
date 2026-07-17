# Janus Reviewer 审查校准失准问题 —— 完整修复方案

> 综合代码层根因分析 + 人类管理智慧映射，分 P0/P1/P2 三个优先级。每个修复点标注改什么文件、怎么改、为什么这样做。

---

## 问题总览

| 编号 | 优先级 | 问题 | 根因分类 | 预计见效 |
|------|--------|------|----------|----------|
| F1 | 🔴 P0 | System Prompt 与 User Template 的 severity 映射矛盾 | 代码层矛盾 | **立即** |
| F2 | 🔴 P0 | CRITERIA CLASSIFICATION 放在 User Template 而非 System Prompt | 代码层架构缺陷 | **立即** |
| F3 | 🟡 P1 | `_adjust_verdict_for_criteria` 不存在，全依赖 LLM 自由裁量 | 代码层缺失防御 | **立即** |
| F4 | 🟡 P1 | Artifact 预加载截断（1000/8000）导致部分审查遗漏 | 代码层参数过保守 | **立即** |
| F5 | 🟡 P1 | 缺少"尊重现场判断"default 预设（军事模型） | 人类管理智慧缺位 | **立即** |
| F6 | 🟢 P2 | 缺多 Reviewer 一致性检查机制（学术校准会议模型） | 架构性改进 | 需多轮 |
| F7 | 🟢 P2 | 缺陷分级门控矩阵不够精确（制造业模型） | 架构性改进 | 需多轮 |
| F8 | 🟢 P2 | 缺跨任务 Reviewer 校准面板（企业强制分布模型） | 架构性改进 | 需多轮 |

---

## 🔴 P0 — 立即修复（阻断性矛盾）

### F1：统一 severity 映射规则（消除两套矛盾规则）

**现状（源码证据）**：

`core/reviewer.py` 第 276-279 行（System Prompt）：
```python
- **minor**: A [SOFT] criterion is not met, or a [HARD] criterion has a small
deviation — result is usable but should be fixed.
```

同文件第 292-297 行（User Template）：
```python
- **[HARD]** criteria are must-haves — non-negotiable requirements.
  Failure to meet a HARD criterion → severity must be at least **major**,
  and the overall verdict must be **major_revisions** or **rejected**.
```

**矛盾本质**：System Prompt 说 `[HARD] + small deviation → minor`，User Template 说 `[HARD] failure → must be at least major`。LLM 在 System Prompt（角色定位）和 User Template（操作指令）之间随机偏向，导致同一类问题被 Reviewer 判出不同 severity → 后续 retry 策略不一致。

**根因分析**：这是典型的"双层指令冲突"——System Prompt 定义角色判断准则，User Template 给出操作规则，两处对同一输入给出不同输出指令。DeepSeek 系列模型对 System Prompt 和 User Message 的权重并无绝对高低，取决于 token 位置和注意力分布，因此表现为"随机偏向"。

**修复方案 — 单向规则，System Prompt 做原则、User Template 做精确映射**：

System Prompt 改为只给原则框架，不给具体 severity 映射：

```python
# core/reviewer.py — _REVIEW_SYSTEM_PROMPT 修改

_REVIEW_SYSTEM_PROMPT = """\
You are a Janus Reviewer. Your sole job is to audit deliverables against \
requirements.

Given a task specification with acceptance criteria and a Worker's delivered \
result, evaluate whether the result actually meets every criterion.

Be precise and evidence-based:
- For each acceptance criterion, state whether it is satisfied and cite \
specific evidence from the result.
- If a criterion is partially met, explain what is missing.
- Do NOT assume — if evidence is absent, flag it as an issue.

Acceptance criteria are classified as [HARD] or [SOFT]:
- [HARD] = must-have, non-negotiable requirements.
- [SOFT] = guidelines / nice-to-haves where minor deviations are acceptable.
- Unmarked criteria default to [HARD].

You have four severity levels to choose from (see the user message for the \
exact mapping between criterion type and severity).

When in doubt between two severities, choose the HIGHER one. \
Judgment errors that understate severity are worse than overstatement — \
because understatement masks problems while overstatement triggers a \
verification check."""
```

User Template 改为精确的"输入→输出"映射表（唯一权威来源）：

```python
# core/reviewer.py — _REVIEW_USER_TEMPLATE 中的 CRITERIA CLASSIFICATION 修改

CRITERIA CLASSIFICATION — EXACT SEVERITY MAPPING (this overrides any general \
guidance; follow this table precisely):

For each acceptance criterion, first identify whether it is [HARD] or [SOFT], \
then apply the corresponding rule:

| Criterion Class | Failure Mode                  | Severity   | Verdict Floor     |
|-----------------|-------------------------------|------------|-------------------|
| [HARD]          | completely unmet / absent     | critical   | rejected          |
| [HARD]          | significantly wrong           | major      | major_revisions   |
| [HARD]          | small / partial deviation     | major      | major_revisions   |
| [SOFT]          | completely unmet / absent     | minor      | minor_revisions   |
| [SOFT]          | partially met / suboptimal    | suggestion | approved_with_notes |
| ANY             | optimization / improvement    | suggestion | approved_with_notes |

KEY RULES (these are NOT negotiable):
1. If at least one [HARD] criterion is NOT fully met → severity CANNOT be \
   "minor" or "suggestion" for that issue.
2. If at least one issue has severity "critical" → verdict MUST be "rejected".
3. If at least one issue has severity "major" → verdict MUST be at least \
   "major_revisions".
4. SOFT-only failures (no HARD criterion violated) → verdict is at most \
   "minor_revisions".
5. When a [HARD] criterion is "mostly met but has minor issue" — the failure \
   is still on a [HARD] requirement. Use severity "major" and verdict \
   "major_revisions". DO NOT downgrade to "minor" just because the gap is small.
```

**为什么这样改**：
- **类比司法审查标准**（de novo vs clearly erroneous）：不同问题类型用不同审查强度。`[HARD]` 用 de novo（严格重新审查），`[SOFT]` 用 clearly erroneous（除非明显错误否则通过）。映射表把这个原则可操作化了。
- 唯一的映射表消除了 LLM 在双套规则间摇摆的空间。
- System Prompt 加"when in doubt, choose HIGHER"类似制造业的"安全侧偏向"原则（宁可误拒不可误放）。
- 表格格式比自然语言更符合 LLM 的 pattern-matching 偏好。

**影响范围**：`core/reviewer.py` 第 253-279 行（System Prompt）和第 291-297 行（User Template CRITERIA CLASSIFICATION 段）。

**验证方法**：构造 3 个测试用例：
1. `[HARD]` 小偏差 → 期望 `severity=major`（不是 minor）
2. `[SOFT]` 完全缺失 → 期望 `severity=minor`（不是 major）
3. 混合 `[HARD]` 完全失败 + `[SOFT]` 小问题 → 期望 `verdict=rejected`

---

### F2：CRITERIA CLASSIFICATION 从 User Template 移入 System Prompt（或合并为单一 source of truth）

**现状**：CRITERIA CLASSIFICATION（包括 severity 映射和 verdict 推导规则）放在 User Template（第 291-330 行），System Prompt 只有模糊的原则性描述。

**问题**：
- User Template 的内容是任务级变量（description/result/artifacts 等混在一起），LLM 对"静态规则"和"动态数据"的注意力分配可能不均。
- System Prompt 是角色定位层，应该是规则权威来源。当前 System Prompt 甚至给出了与 User Template 矛盾的规则。
- 类比制造业缺陷分级门控：规则应该写在"标准操作程序"（System Prompt），而不是写在工作单（User Template）里。

**修复方案**：

将完整的 severity 映射表移入 System Prompt，User Template 只保留操作指令（不包含规则定义）：

```python
# core/reviewer.py — 新的 _REVIEW_SYSTEM_PROMPT（合并 F1 修复）

_REVIEW_SYSTEM_PROMPT = """\
You are a Janus Reviewer — an independent audit agent. Your sole job is to \
audit Worker deliverables against acceptance criteria.

## Your Operating Rules (these are your "standard operating procedure")

### Criteria Classification
Acceptance criteria are prefixed with [HARD] or [SOFT]:
- **[HARD]** = must-have, non-negotiable. Failure on any HARD criterion is \
  blocking.
- **[SOFT]** = guideline / nice-to-have. Minor deviations are acceptable.
- **Unmarked** = default to [HARD].

### Severity Assignment Table
For every issue you find, assign severity by this table:

| Criterion | Failure Mode              | Severity   |
|-----------|---------------------------|------------|
| [HARD]    | completely unmet / absent | critical   |
| [HARD]    | significantly wrong       | major      |
| [HARD]    | small/partial deviation   | major      |
| [SOFT]    | completely unmet / absent | minor      |
| [SOFT]    | partially met             | suggestion |
| ANY       | pure optimization idea    | suggestion |

### Verdict Derivation Rules
- Any CRITICAL issue → verdict = rejected
- Any MAJOR issue (and no CRITICAL) → verdict = major_revisions
- Only MINOR issues (and no MAJOR/CRITICAL) → verdict = minor_revisions
- Only SUGGESTION issues → verdict = approved_with_notes
- No issues at all → verdict = approved
- [SOFT]-only failures can never produce "rejected" or "major_revisions"

### Tie-Breaking Rule
When uncertain between two severities, choose the HIGHER one. \
A false-pass is worse than a false-fail — the system has retry mechanisms \
that catch overstatement, but understatement silently masks problems.

### Evidence Rule
For each criterion satisfied, cite specific evidence from the result or \
artifact contents. For each criterion failed, explain exactly what is missing.

### 用户意图对齐规则
产出的审查必须对照用户原始输入（user_goal）而不仅是验收标准。如果产出虽然在技术层面满足验收标准，但与用户原始需求明显不符（例如分析了错误项目），必须在 evidence 中明确指出这一偏差。"""

# User Template 简化为纯操作指令 + 数据占位
_REVIEW_USER_TEMPLATE = """\
TASK: {description}
ACCEPTANCE CRITERIA: {acceptance_criteria}
EXPECTED ARTIFACTS: {context}
GOAL: {goal}
用户原始输入：{user_goal}
CONSTRAINTS: {constraints}
INTENT: {intent}

DELIVERED RESULT:
Status: {status}
Summary: {summary}
Full Result: {result}
Artifact Paths: {artifacts}

ARTIFACT CONTENTS (pre-loaded for your inspection):
{artifact_contents}

INSTRUCTIONS:
1. For each acceptance criterion, identify [HARD] or [SOFT], then check \
   against the result and artifact contents.
2. Assign severity according to the table in your system prompt.
3. Derive the overall verdict from the rules in your system prompt.
4. Write evidence that cites specifics — what was present, what was missing.

Output ONLY a JSON object:
{{
  "verdict": "approved|approved_with_notes|minor_revisions|major_revisions|rejected",
  "summary": "one-line verdict",
  "issues": [
    {{"severity": "critical|major|minor|suggestion", "description": "..."}}
  ],
  "evidence": "what proved success or what was missing"
}}"""
```

**为什么这样改**：
- **单一权威来源（Single Source of Truth）**：所有规则只在 System Prompt 出现一次，消除双写同步风险。
- LLM 对 System Prompt 中结构性表格的处理比自然语言段落更稳定（表格格式触发更强的 pattern-following）。
- 类比企业 SOP 体系：操作规程（System Prompt）定义"怎么做"，工单（User Template）提供"做什么"——规则和数据分离。

**影响范围**：
- `core/reviewer.py` `_REVIEW_SYSTEM_PROMPT`（完全重写）
- `core/reviewer.py` `_REVIEW_USER_TEMPLATE`（删除 CRITERIA CLASSIFICATION 段）
- 现有的 `{artifact_contents}` 等占位符不受影响

---

## 🟡 P1 — 结构加固（显著提升一致性）

### F3：增加硬编码校准层 `_adjust_verdict_for_criteria`（规则引擎兜底）

**现状**：当前全流程依赖 LLM 一次调用完成 criteria→issue classification → severity assignment → verdict derivation。没有程序化约束。如果 LLM 输出 `[HARD]` failure 但 severity = `minor`，没有规则引擎纠正。

**问题**：`_parse_review_result()`（第 612-643 行）只做 JSON 解析，不做语义校验。`from_dict()`（第 180-218 行）只做类型转换，不做业务规则验证。

**修复方案**：在 `review()` 方法中，LLM 返回解析之后，增加一个纯规则引擎校验步骤。这一步**零 LLM 调用**。

```python
# core/reviewer.py — 新增方法

@staticmethod
def _calibrate_verdict(
    result: ReviewResult,
    acceptance_criteria: str,
) -> ReviewResult:
    """Post-hoc calibration: enforce hard rules that the LLM might violate.

    This is a zero-token, deterministic rule engine — it does NOT call the
    LLM.  It catches contradictions like:
    - [HARD] criterion failure assigned severity=minor or suggestion
    - CRITICAL issues present but verdict is not rejected
    - MAJOR issues present but verdict is less than major_revisions

    Inspired by manufacturing "stop-line" gating: certain combinations
    are non-negotiable regardless of human/LLM judgment.

    Args:
        result: The parsed ReviewResult from the LLM.
        acceptance_criteria: The raw acceptance_criteria string from the
            TaskSpec, used to count [HARD] vs [SOFT] criteria.

    Returns:
        A (possibly adjusted) ReviewResult with a calibration note appended
        to evidence if any adjustments were made.
    """
    import re

    adjustments: list[str] = []
    adjusted_issues = list(result.issues)

    # Step 1: Count criteria by class from the acceptance_criteria text.
    hard_criteria = re.findall(
        r'\[HARD\]\s*(.+?)(?=\[HARD\]|\[SOFT\]|$)', acceptance_criteria
    )
    if not hard_criteria:
        # Fallback: count lines that don't start with [SOFT]
        hard_criteria = [
            line for line in acceptance_criteria.split('\n')
            if line.strip() and '[SOFT]' not in line
        ]
    soft_criteria = re.findall(
        r'\[SOFT\]\s*(.+?)(?=\[HARD\]|\[SOFT\]|$)', acceptance_criteria
    )

    # Step 2: For each issue, check if severity is consistent with the
    # criterion it references.  If an issue description mentions a [HARD]
    # criterion keyword, its severity must be >= MAJOR.
    hard_keywords: set[str] = set()
    for hc in hard_criteria:
        # Extract key nouns/verbs as matching keywords (first 3 words)
        words = hc.strip().split()[:4]
        for w in words:
            if len(w) > 3:
                hard_keywords.add(w.lower())

    soft_keywords: set[str] = set()
    for sc in soft_criteria:
        words = sc.strip().split()[:4]
        for w in words:
            if len(w) > 3:
                soft_keywords.add(w.lower())

    for i, issue in enumerate(adjusted_issues):
        desc_lower = issue.description.lower()

        # Check if this issue references a HARD criterion keyword
        hits_hard = any(kw in desc_lower for kw in hard_keywords)
        hits_soft = any(kw in desc_lower for kw in soft_keywords)

        if hits_hard and not hits_soft:
            if issue.severity in (Severity.MINOR, Severity.SUGGESTION):
                old_sev = issue.severity.value
                adjusted_issues[i] = ReviewIssue(
                    severity=Severity.MAJOR,
                    description=(
                        f"[AUTO-CALIBRATED: was {old_sev}, upgraded to major "
                        f"because this references a [HARD] criterion] "
                        f"{issue.description}"
                    ),
                )
                adjustments.append(
                    f"Upgraded issue #{i+1} from {old_sev} → major "
                    f"(references [HARD] criterion)"
                )

    # Step 3: Derive the correct verdict floor from issue severities.
    has_critical = any(i.severity == Severity.CRITICAL for i in adjusted_issues)
    has_major = any(i.severity == Severity.MAJOR for i in adjusted_issues)
    has_minor = any(i.severity == Severity.MINOR for i in adjusted_issues)
    has_suggestion = any(i.severity == Severity.SUGGESTION for i in adjusted_issues)
    no_issues = len(adjusted_issues) == 0

    if no_issues:
        floor_verdict = ReviewVerdict.APPROVED
    elif has_critical:
        floor_verdict = ReviewVerdict.REJECTED
    elif has_major:
        floor_verdict = ReviewVerdict.MAJOR_REVISIONS
    elif has_minor:
        floor_verdict = ReviewVerdict.MINOR_REVISIONS
    elif has_suggestion:
        floor_verdict = ReviewVerdict.APPROVED_WITH_NOTES
    else:
        floor_verdict = ReviewVerdict.APPROVED

    # Verdict rank for comparison
    verdict_rank = {
        ReviewVerdict.APPROVED: 0,
        ReviewVerdict.APPROVED_WITH_NOTES: 1,
        ReviewVerdict.MINOR_REVISIONS: 2,
        ReviewVerdict.MAJOR_REVISIONS: 3,
        ReviewVerdict.REJECTED: 4,
    }

    if verdict_rank.get(result.verdict, 0) < verdict_rank.get(floor_verdict, 0):
        old_verdict = result.verdict.value
        adjustments.append(
            f"Upgraded verdict from {old_verdict} → {floor_verdict.value} "
            f"(issues present require at least {floor_verdict.value})"
        )
        result.verdict = floor_verdict

    # Step 4: Append calibration note to evidence.
    if adjustments:
        calib_note = (
            "\n\n[CALIBRATION NOTE — rule engine corrections applied:]\n"
            + "\n".join(f"  - {a}" for a in adjustments)
        )
        result.evidence = (result.evidence or "") + calib_note

    # Update the issues list with any severity adjustments
    result.issues = adjusted_issues
    return result
```

**集成点** — 在 `review()` 方法中，`_parse_review_result` 返回后调用：

```python
# core/reviewer.py — review() 方法中，第 607-608 行之间插入

        # ── Parse JSON ───────────────────────────────────────────────
        parsed = self._parse_review_result(content)

        # ── Rule-engine calibration (P1: enforce severity-verdict consistency) ──
        parsed = self._calibrate_verdict(parsed, spec.acceptance_criteria)

        return parsed
```

**为什么这样改**：
- **类比制造业"停线"门控**：某些缺陷组合（HARD criterion + minor severity）本身就不合法，生产线应该自动停止，不需要人工/LLM 决定。
- **类比司法"法律审"vs"事实审"**：LLM 做事实审（判断是否满足标准），规则引擎做法律审（判断 severity/verdict 是否符合规定）。
- **零 token 成本**的防御层，只在 LLM 输出违规时触发纠正。

**局限性**：关键词匹配不如 LLM 语义理解精准（这是 F1 和 F2 用 LLM prompt 的原因），但作为兜底层，宁可误升级（触发一次额外 retry）也不漏判。

---

### F4：提升 Artifact 预加载上限（缓解截断导致的审查盲区）

**现状**（第 408 行）：
```python
def _build_artifact_contents(artifacts, max_per_file=1000, max_total=8000)
```

**问题**：1000 chars/file 对代码文件严重不足（一个函数就可能 500+ 字符），8000 total 对多文件任务更是瓶颈。Reviewer 看到截断内容后可能：
- 误判"缺失"（实际内容在截断部分）
- 降低 confidence → 倾向保守 → 更多 REJECTED → 更多 retry → 恶性循环

**修复方案**：提升上限并改为可配置：

```python
# core/reviewer.py — _build_artifact_contents 签名和默认值修改

@staticmethod
def _build_artifact_contents(
    artifacts: list[str],
    max_per_file: int = 3000,   # was 1000 — tripled for code file coverage
    max_total: int = 16000,      # was 8000 — doubled for multi-file tasks
) -> str:
```

同步更新 Reviewer 构造函数，允许外部配置：

```python
# core/reviewer.py — __init__ 新增参数

def __init__(
    self,
    model: str,
    api_key: str,
    timeout: int = 120,
    artifact_max_per_file: int = 3000,
    artifact_max_total: int = 16000,
) -> None:
    self._model = model
    self._timeout = timeout
    self._artifact_max_per_file = artifact_max_per_file
    self._artifact_max_total = artifact_max_total
    ...
```

`review()` 方法中调用改为：

```python
artifact_contents = self._build_artifact_contents(
    result.artifacts,
    max_per_file=self._artifact_max_per_file,
    max_total=self._artifact_max_total,
)
```

**为什么这样改**：
- 当前 DeepSeek 上下文窗口普遍 ≥ 64K tokens，16000 chars ≈ 4000 tokens，仅占 ~6%，不会导致 prompt 爆炸。
- 1000 chars 对应约 15 行代码（含注释），trivial 函数都可能超限。
- 类比**军事"尊重现场判断"预设**中的"充分信息原则"：决策者（Reviewer）必须有足够的现场信息才能做出正确判断。截断信息等于让 Reviewer 在信息不全的情况下做决策。

**额外优化**：在 truncation 处增加"关键段落标记"——如果截断发生在函数/类定义的中间，提示 Reviewer 内容不完整可能影响判断：

```python
if truncated:
    header += (
        f" (truncated at {max_per_file} chars — "
        f"remaining content not shown; if this truncation point "
        f"falls inside a function/class/JSON block, the review "
        f"may be incomplete)"
    )
```

---

### F5：增加"尊重现场判断"默认预设（军事模型 → 审查哲学）

**动机**：人类管理智慧中的军事指挥原则——"现场指挥官比后方参谋更了解实际情况，参谋的建议尊重现场判断，需证据才能推翻"——映射到 Janus 中为：

> Reviewer 默认信任 Worker 的判断，但要求 Worker 对每个声明提供可验证证据。审查不是"挑刺"而是"验证证据链"。

**修复方案**：在 System Prompt 开头增加审查哲学声明：

```python
# 追加到 _REVIEW_SYSTEM_PROMPT 的开头（在 "You are a Janus Reviewer" 之后）

## Review Philosophy: Verify, Don't Presume

The Worker who produced this deliverable had direct access to tools, files, \
and execution context that you do not have.  Your job is NOT to second-guess \
every decision — it is to verify that the Worker's claims are backed by \
evidence in the artifact contents and result summary.

- If the Worker claims something and the artifact contents support it → \
  criterion is SATISFIED.  Do not invent hypothetical failure modes.
- If the Worker claims something but evidence is absent or contradictory → \
  flag as an issue with evidence.
- If the Worker says "X is done" and the artifact shows X is done → ACCEPT.
  Do not create extra work by demanding the Worker redo things that are \
  already correct.
- If the Worker says "X is done" but artifacts are empty or show no X → \
  REJECT with specific evidence of absence.

The burden of proof is on the ABSENCE of evidence, not the presence of \
perfection.  A deliverable that meets all stated criteria is APPROVED \
regardless of whether YOU would have done it differently.
```

**为什么这样改**：
- 直接可操作化军事指挥的"尊重现场判断"原则。
- 减少 Reviewer 的"过度审查"倾向（LLM 天然倾向找问题而非确认通过）→ 减少不必要的 retry。
- 明确"证据缺失时才驳回"的标准，类比法庭的"证据裁判原则"。

---

## 🟢 P2 — 架构性改进（长期演进）

### F6：多 Reviewer 一致性检查（学术校准会议模型）

**动机**：学术会议审稿中，一篇论文由 3-5 位审稿人独立打分，领域主席（Area Chair）对比评分后做最终裁决。如果审稿人间分歧过大（一个 accept 一个 reject），进入讨论阶段。

**修复方案**：在 Planner 层增加可选的"双 Reviewer 校准"模式：

```python
# core/planner.py — _dispatch_with_review 的增强（伪代码）

def _dispatch_with_review(
    self, spec: TaskSpec, dual_review: bool = False
) -> ReviewResult:
    """Dispatch a task and review it.  If dual_review=True, run two
    independent reviews and reconcile disagreements.
    """
    worker_result = self._dispatch(spec)

    review1 = self._reviewer.review(spec, worker_result)

    if not dual_review:
        return review1

    # Second reviewer — potentially different model or temperature
    review2 = self._reviewer2.review(spec, worker_result)

    # Reconcile
    return self._reconcile_reviews(review1, review2)
```

`_reconcile_reviews` 逻辑：

```python
@staticmethod
def _reconcile_reviews(r1: ReviewResult, r2: ReviewResult) -> ReviewResult:
    """Reconcile two independent reviews.

    Rules (inspired by academic peer review area chair process):
    - Both agree → return r1 (consensus)
    - One APPROVED, one REJECTED → escalate to "meta-review" LLM call
    - One MAJOR, one MINOR → take the stricter one (safety bias)
    - Severity differences on same issue → take the HIGHER severity
    """
    if r1.verdict == r2.verdict:
        return r1

    # If one is passing and the other blocking, escalate
    if r1.passed != r2.passed:
        # Merge issues from both, use stricter verdict
        merged = r1 if not r1.passed else r2
        merged.issues = list({
            i.description: i for i in (r1.issues + r2.issues)
        }.values())
        merged.evidence = (
            f"[DUAL-REVIEW: r1={r1.verdict.value}, r2={r2.verdict.value}] "
            f"Resolved to: {merged.verdict.value}\n"
            f"r1: {r1.evidence}\nr2: {r2.evidence}"
        )
        return merged

    # Both blocking but different severity → take stricter
    return r1 if not r1.passed else r2
```

**实现优先级**：P2（需显著增加 token 成本，当前非必需）。但作为可选的 `dual_review=True` 模式，对高风险任务（如安全审计）有价值。

---

### F7：缺陷分级门控矩阵（制造业模型 → 可配置的 severity 规则）

**动机**：制造业的缺陷分级不是一刀切的——同一个缺陷在"安全件"上是 CRITICAL（停线），在"内饰件"上可能是 MINOR（让步接收）。映射到代码审查：同一个问题在"安全代码"和"文档生成"中严重性不同。

**修复方案**：在 TaskSpec 中增加 `criticality` 字段，Reviewer 据此调整 severity 计算：

```python
# core/protocol.py — TaskSpec 新增字段

@dataclass
class TaskSpec:
    ...
    criticality: str = "normal"  # "safety" | "normal" | "cosmetic"
```

Reviewer prompt 中动态注入：

```python
CRITICALITY LEVEL: {criticality}

Severity adjustment by criticality:
- safety: All issues are one level HIGHER than the standard table
  (minor → major, major → critical, suggestion → minor)
- normal: Use the standard table as-is
- cosmetic: [HARD] criteria can be downgraded one level
  (major → minor for small deviations, critical → major)
```

---

### F8：跨任务 Reviewer 校准面板（企业强制分布模型）

**动机**：企业绩效管理中，不同经理对同一员工的评分差异通过"校准面板"（Calibration Panel）消除——所有经理一起对比评分标准后重新打分。

**修复方案**：在大任务（多个 Worker 并行执行）完成后，增加一个跨任务校准步骤：

```python
# core/planner.py — _execute 完成后

def _cross_task_calibration(self, reviews: list[tuple[TaskSpec, ReviewResult]]):
    """Check for inconsistency across multiple reviews of the same batch.

    If one Worker got APPROVED for work of similar quality to another Worker
    who got MAJOR_REVISIONS, flag the inconsistency.
    """
    # Sample: check verdict distribution
    from collections import Counter
    verdict_dist = Counter(r.verdict for _, r in reviews)

    # If all reviews are APPROVED or all REJECTED, likely consistent
    # If mixed, check for potential miscalibration
    if len(verdict_dist) > 2:  # mixed verdicts across tasks
        # LLM call: "Here are 5 reviews of similar tasks. Are any
        # inconsistent with the others? If so, which one and why?"
        ...
```

**实现优先级**：P2（需要额外 LLM 调用，仅在批量任务且怀疑校准时启用）。

---

## 修复执行顺序

```
第 1 步 ─ F1 + F2 合并修复（改 reviewer.py System Prompt + User Template）
          ↓ 立即生效：单一 severity 映射规则
第 2 步 ─ F3 增加 _calibrate_verdict 规则引擎
          ↓ 立即生效：LLM 输出违规时自动纠正
第 3 步 ─ F5 增加审查哲学声明
          ↓ 立即生效：减少过度审查
第 4 步 ─ F4 提升 artifact 上限
          ↓ 立即生效：减少截断导致的审查盲区
第 5 步 ─ 跑测试套件验证 + 构造回归用例
          ↓
第 6 步 ─ F6 / F7 / F8（P2，按需实现）
```

---

## 测试验证计划

1. **单元测试**（`tests/test_reviewer.py`）：
   - `test_hard_criterion_small_deviation_must_be_major`：构造 `[HARD]` 小偏差输入，断言 `severity=major`
   - `test_soft_only_failure_not_rejected`：构造纯 `[SOFT]` 失败，断言 `verdict ∈ {minor_revisions, approved_with_notes}`
   - `test_calibrate_upgrades_minor_on_hard`：构造 LLM 返回 `[HARD]` failure + minor 的 mock，断言 `_calibrate_verdict` 升级为 major
   - `test_calibrate_upgrades_verdict_floor`：构造 LLM 返回 major issue + approved_with_notes，断言 verdict 升级为 major_revisions

2. **集成测试**（`tests/test_integration.py`）：
   - 完整 Gatekeeper → Planner → Worker → Reviewer 链路，验证 retry 次数在合理范围（≤2）
   - 验证同一输入在多次运行间的一致性（跑 5 次，verdict 标准差 ≤ 1 级）

3. **回归测试**：
   - 现有所有 Reviewer 相关测试不能退化

---

## 人类管理智慧映射总结

| 管理智慧 | 映射到 Janus | 修复编号 |
|----------|-------------|----------|
| 司法审查标准矩阵（de novo / clearly erroneous） | [HARD] 用严格审查，[SOFT] 用宽松审查 | F1, F2 |
| 学术校准会议（多审稿人 + 领域主席） | 双 Reviewer 模式 + reconcile | F6 |
| 制造业缺陷分级门控（停线 vs 让步接收） | `_calibrate_verdict` 规则引擎 | F3 |
| 军事"尊重现场判断"预设 | Review Philosophy 声明 | F5 |
| 企业强制分布 + 校准面板 | 跨任务校准 | F8 |
| SOP 体系（操作规程 vs 工单） | System Prompt = SOP，User Template = 工单 | F2 |
| 安全侧偏向（宁可误拒不可误放） | Tie-breaking 规则 + 规则引擎升级 | F1, F3 |
