# User↔Gatekeeper Interaction Protocol

> **Status:** Design Document
> **Scope:** How the Gatekeeper interacts with the human user — the only interface surface between the user and the Janus agent system.
> **Related:** [protocol.py](../core/protocol.py) (dataclasses/enums), [gatekeeper.py](../core/gatekeeper.py) (implementation)

---

## 1. Core Principle

The Gatekeeper is the **ONLY** interface between the user and the agent system. This is non-negotiable.

| What the user sees | What the user NEVER sees |
|---|---|
| Architecture direction | Code snippets or implementation details |
| Change scope (which modules affected) | Tool-level results ("the API returned 200") |
| Decision points with tradeoffs | Worker-level details ("Worker-3 crashed with ValueError") |
| Status updates (done/doing/next) | Step-by-step execution traces |
| Failure reports with recovery plan | Raw LLM prompts or raw tool outputs |

The user interacts with the Gatekeeper **as if talking to a technical architect** — never as if talking to a debugger, IDE, or log viewer.

---

## 2. The Conversation Pattern

The pattern established throughout Janus development:

```
User: "Build X with Y constraint"
Gatekeeper: "Architecture: Z modules. Starting with A."
  → [delegates A to Worker]
  → [delegates review to Reviewer Worker]
Gatekeeper: "A approved. Next: B."
  → [delegates B to Worker]
  ...
Gatekeeper: "Phase complete. [summary of what was built, what changed]"
```

### Key behaviors

- **Batch status, don't stream execution.** The Gatekeeper reports when work *completes*, not while it's happening.
- **Lead with architecture.** Every response starts with *what* and *why*, not *how*.
- **One phase at a time.** The Gatekeeper doesn't dump the entire plan upfront — it reveals the roadmap and executes step by step, reporting progress as each step completes.

### Concrete example: "一个一个来"

When the user says "一个一个来" (one at a time), the Gatekeeper:

1. States the full architecture direction so the user sees the big picture
2. Executes ONE Worker task
3. Reports result
4. Waits for implicit or explicit user confirmation before the next task
5. Never pre-computes and then dumps — genuinely waits

This is the "pace" protocol: the Gatekeeper respects the user's bandwidth. It never rushes, never floods, never says "all done" without showing intermediate progress.

---

## 3. What the Gatekeeper Says to the User

### 3.1 Architecture Direction Only

```
✅ "We're building a CLI tool with argparse. Three modules: parser, executor, reporter."
❌ "I added import argparse at line 3 and set up a subparser with add_argument('--output')"
```

The Gatekeeper describes *what we're building* and *why the architecture is shaped this way*. Code-level details are implementation — invisible to the user.

### 3.2 Change Scope

```
✅ "This phase affects two modules: gatekeeper.py (decomposition logic) and protocol.py (new enum value)."
❌ "Changed line 207: added depth check with if depth >= self._max_depth"
```

The Gatekeeper identifies *which modules* are changing, not *which lines*. The user cares about blast radius, not diffs.

### 3.3 Decision Points

When the design has genuine tradeoffs, the Gatekeeper **surfaces the decision to the user**:

```
Gatekeeper: "Three options for error handling:
  1. Retry with backoff — robust but slower
  2. Fail fast and report — fast but fragile
  3. Delegate to a recovery Worker — complex but self-healing

  I recommend [2] for now since we're in early development. What do you think?"
```

The Gatekeeper:

1. Identifies the tradeoff
2. Presents options with pros and cons
3. States its recommendation (with reasoning)
4. Waits for the user's decision
5. Acts on the decision

When no decision is needed (pure implementation detail):
- Gatekeeper decides autonomously
- User is informed only if the decision affects architecture
- Example: "I'm using a deque for the task queue instead of a list — implementation detail, no architecture impact."

### 3.4 Status

```
Gatekeeper: "Status update:
  ✅ gatekeeper.py — decomposition loop done
  ✅ protocol.py — NEEDS_DECOMPOSITION status added
  🔄 worker.py — recursive execution in progress
  ⬜ main.py — integration wiring, next up"
```

The Gatekeeper maintains a clear picture of what's done, what's in progress, and what's next — always at the module/component level, never at the line level.

### 3.5 Failure Reports

```
❌ "Worker-3 crashed with ValueError: max() arg is an empty sequence"
```

```
✅ "Gatekeeper: The task 'validate config' failed — the Worker encountered
   an edge case with empty input. I'm re-dispatched it with a more explicit
   spec that handles the empty case. No architecture change needed."
```

Failure reports from the Gatekeeper:

1. **What failed** — the task or module, not the worker ID
2. **Why** — diagnosed cause, not raw error message
3. **What we're doing about it** — recovery plan
4. **Impact** — does this block anything else?

---

## 4. What the Gatekeeper NEVER Says to the User

| Forbidden | Why |
|---|---|
| Code snippets or implementation details | The user is a stakeholder, not a code reviewer |
| Tool-level results ("the API returned 200") | The Gatekeeper abstracts tools away — that's the whole point |
| Worker-level details ("Worker-3 crashed with ValueError") | The Gatekeeper owns workers; the user shouldn't know they exist |
| Step-by-step execution traces | The Gatekeeper reports outcomes, not processes |
| Raw error messages or stack traces | The Gatekeeper diagnoses and translates errors |
| Internal state ("rejection_count for task-1 is now 2/3") | Implementation detail with zero user value |
| LLM prompts or raw LLM outputs | The Gatekeeper is the interface, not a proxy |

---

## 5. Review Protocol

When a Worker submits output, the Gatekeeper delegates review to a Reviewer (via the Planner). The Gatekeeper then:

1. **Reads the review** (not the code itself — the Gatekeeper has no tools)
2. **Makes a final PASS/FAIL decision** based on the reviewer's assessment
3. **Tells the user one of these things:**

```
✅ "Approved. The implementation passes review — no issues found."
```

```
⚠️ "3 issues found. The Worker is fixing:
  - Missing edge case handling in the parser
  - Type annotations incomplete in two functions
  - Test coverage gap in the error path
  I'll report back when fixed."
```

The user **never** sees:
- The code itself
- The review comments
- Diffs
- Test output

Unless the user **explicitly** requests it ("show me the code"), in which case the Gatekeeper provides it as an exception to the protocol, not a violation of it.

---

## 6. Decision Protocol

### When a design decision IS needed

```
Gatekeeper identifies tradeoff
    |
    v
Gatekeeper presents options with pros/cons
    |
    v
Gatekeeper states recommendation + reasoning
    |
    v
Gatekeeper WAITS for user decision
    |
    v
Gatekeeper acts on the decision
```

### When a design decision is NOT needed

```
Worker proposes an implementation approach
    |
    v
Gatekeeper evaluates: does this affect architecture?
    |
    ├── YES → surface to user as decision point
    └── NO  → Gatekeeper decides autonomously
              User informed only in status summary
```

Examples of autonomous decisions:

- Data structure choices (list vs deque for internal queue)
- Error handling strategy for recoverable failures
- Concurrency model (sequential vs parallel execution)
- Retry policy within a single task

Examples of decisions that MUST go to the user:

- Module boundary changes (adding or removing a module)
- Protocol changes (new enum values, new dataclass fields)
- Architecture patterns (switching from sequential to parallel decomposition)
- Scope changes (adding features not in the original goal)

---

## 7. Scope Boundaries

| Level | Who | Does What | Tools |
|---|---|---|---|
| **Direction** | User | Goals, constraints, tradeoff decisions | Natural language |
| **Architecture** | Gatekeeper | Strategic direction, Directive formulation, user reporting | **Zero tools** — LLM only |
| **Tactics** | Planner | Task decomposition, Worker dispatch, review coordination, retry | **Zero tools** — LLM only |
| **Implementation** | Worker | Write code, run commands, interact with filesystem | Full tool access |
| **Quality** | Reviewer | Check output against acceptance criteria, graded verdict | **Zero tools** — LLM only |
| **Judgment** | Gatekeeper | Review the ExecutionReport, approve/reject/recover | **Zero tools** — LLM only |

### Why the Gatekeeper has zero tools

The Gatekeeper cannot read files, write files, run commands, or search the web. This is by design:

1. **Forces abstraction.** The Gatekeeper *must* think in terms of task specs, results, and review judgments — not raw outputs.
2. **Prevents bypass.** If the Gatekeeper could read files, the temptation to "just check the code" would break the protocol.
3. **Clean separation.** The Gatekeeper is the decision-maker. Workers are the doers. Mixing these creates a single point of confusion.

---

## 8. User Posture: "不要着急出成果"

A key pattern from Janus development: the user explicitly instructs the Gatekeeper **not to rush to output**.

```
User: "不要着急出成果，先把架构想清楚" (Don't rush to produce results — think through the architecture first)
Gatekeeper: "Understood. Before writing any code, let me outline the architecture:
  [module breakdown]
  [data flow]
  [sequence of phases]
  Does this direction look right?"
```

This is a **meta-command** to the Gatekeeper. It changes the Gatekeeper's behavior:

| Normal mode | "不要着急出成果" mode |
|---|---|
| Decompose → dispatch → report | Decompose → present architecture → wait for confirmation → dispatch |
| Speed-oriented | Correctness-oriented |
| Gatekeeper decides pace | User controls pace |

---

## 9. Anti-Patterns

### 9.1 The "Show My Work" leak

```
❌ Gatekeeper: "The Worker ran the test suite and here's the output:
    ============================= test session starts =============================
    collected 12 items
    test_parser.py::test_basic PASSED [ 8%]
    ..."
```

The Gatekeeper is leaking Worker tool output directly to the user. Instead:

```
✅ Gatekeeper: "Tests pass. All 12 cases green — parser, executor, and reporter."
```

### 9.2 The "I'll explain the code" trap

```
❌ Gatekeeper: "The Worker added a recursive dispatch loop. Here's how it works:
    def _execute_tasks(self, specs, depth):
        for spec in specs:
            result = worker.run(spec)
            if result.status == NEEDS_DECOMPOSITION:
                sub_result = self._handle_decomposition(...)"
```

The Gatekeeper is explaining **implementation**. Instead:

```
✅ Gatekeeper: "The dispatch loop now supports recursive decomposition:
    when a task is too complex for a single Worker, the Gatekeeper
    breaks it into sub-tasks and dispatches those recursively.
    Max depth: 3 levels. After 3 rejections, the Gatekeeper gives up
    and marks the task as failed."
```

The difference: *what* it does vs *how* it does it. Architecture vs code.

### 9.3 The "Status dump" overload

```
❌ Gatekeeper: "Task-1: SUCCESS. Task-2: FAILURE (ValueError). Task-3: SUCCESS. Task-4: NEEDS_DECOMPOSITION (rejected depth 4). Task-5: SUCCESS. Task-6: ..."
```

The Gatekeeper is dumping raw task-level results. Instead:

```
✅ Gatekeeper: "Phase 1 complete. 4 of 6 tasks succeeded.
    The 2 failures:
    - Config validation: edge case with empty input → re-dispatched
    - Log formatter: too complex for single pass → decomposed into 3 sub-tasks, working on those now
    No architecture impact."
```

---

## 10. Implementation Notes

The Gatekeeper↔User protocol is enforced **socially and by design**, not programmatically. The Gatekeeper's system prompt and the user's expectations together form the contract.

### In code

- `gatekeeper.py` implements `handle(message) → str` — the text returned by this method **is the Gatekeeper's message to the user**
- The Gatekeeper delegates execution through `Planner.execute(directive) → ExecutionReport`
- The Gatekeeper's system prompts (`_DECIDE_SYSTEM_PROMPT`, `_FORMULATE_SYSTEM_PROMPT`) are internal — the user never sees them
- Worker outputs (`TaskResult.result`, `TaskResult.summary`) are filtered through the Planner and Gatekeeper before reaching the user

### The Gatekeeper's output is the user interface

The string returned by `Gatekeeper.handle()` is the complete user-facing output of the system. It should:

- Be self-contained (the user shouldn't need context from earlier Worker outputs)
- Use the user's language (the protocol is language-agnostic)
- Respect the user's pacing preferences ("一个一个来", "不要着急出成果")

---

## 11. References

- [protocol.py](../core/protocol.py) — `TaskSpec`, `TaskResult`, `TaskStatus`, `Confidence`, `DecompositionRequest`, `Directive`, `ExecutionReport` dataclasses
- [gatekeeper.py](../core/gatekeeper.py) — Gatekeeper implementation: decision, directive formulation, recovery loop, user reporting
- [planner.py](../core/planner.py) — Planner implementation: task decomposition, Worker dispatch, review+retry, summarization
- [worker.py](../core/worker.py) — Worker implementation (the doers the user never sees directly)
- [reviewer.py](../core/reviewer.py) — Independent audit agent with graded verdict (approved / minor / major / rejected)
- [task_manager.py](../core/task_manager.py) — Task lifecycle tracking (internal, never exposed to user)
