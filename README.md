  █████╗ █████╗ ███╗   ██╗██╗   ██╗███████╗
  ╚══██║██╔══██╗████╗  ██║██║   ██║██╔════╝
     ██║███████║██╔██╗ ██║██║   ██║███████╗
     ██║██╔══██║██║╚██╗██║██║   ██║╚════██║
  ██╗██║██║  ██║██║ ╚████║╚██████╔╝███████║
  ╚████╝╚═╝  ╚═╝╚═╝  ╚═══╝ ╚═════╝ ╚══════╝
        past  ◆  present  ◆  future


# Janus

*Human Management Wisdom → Agent Architecture*

**Janus is not another agent framework. It's a design philosophy that asks: "How do humans manage humans?" — then maps the answer to LLM agents.**

Most frameworks give you tools to build agents. Janus gives you a **management system** for agents — four specialized roles with hard boundaries, inherited from 3,000+ years of human organizational wisdom: military command chains, judicial review standards, manufacturing quality control, academic peer review.



## Why Janus Exists

Existing agent frameworks are designed around **what LLMs can do**. Janus is designed around **how humans organize**.

| Framework | Core Metaphor | The Problem |
|-----------|--------------|-------------|
| **LangGraph** | State machine / graph | You design the control flow. Great when you know the path. Fails when you don't. |
| **AutoGen** | Conversation / chat | Agents talk. But conversation isn't management — there's no hierarchy, no audit, no accountability. |
| **CrewAI** | Role-playing team | Fun metaphor, but roles have no hard boundaries. Anyone can do anything. |
| **MetaGPT** | Software company SOP | Powerful for code generation. But the SOP is rigid — it's a script, not a management system. |

**Janus's insight:** LLM agents suffer from the same structural problems as human organizations — task decomposition quality varies, outputs deviate from intent, failures cascade silently, and there's no independent audit. Humans solved these problems with **hierarchical management, independent review, graded escalation, and context discipline**. Janus applies those solutions directly.


## Architecture


```
                         ┌─────────────────────────────────────────┐
                         │                 USER                     │
                         └─────────────────┬───────────────────────┘
                                           │ goal
                                           ▼
┌──────────────────────────────────────────────────────────────────────────┐
│                           GATEKEEPER  (Strategic)                        │
│   ──────────────────────────────────────────────────────────────────     │
│   Understands user intent. Formulates Directive. Reports results.        │
│   ZERO TOOLS — no file access, no command execution, no web.             │
│   Only sees: Directive (input) → ExecutionReport (output).              │
└───────────────────────────────────┬──────────────────────────────────────┘
                                    │ Directive (goal + intent + constraints)
                                    ▼
┌──────────────────────────────────────────────────────────────────────────┐
│                            PLANNER  (Tactical)                            │
│   ──────────────────────────────────────────────────────────────────     │
│   Decomposes Directive into executable TaskSpecs. Dispatches Workers.    │
│   Tracks progress. Summarizes results. ZERO TOOLS.                       │
│   Has its own LLM — can be lighter/cheaper than Gatekeeper's.            │
└───────┬──────────────────────────────┬───────────────────────────────────┘
        │ TaskSpec                     │ TaskSpec
        ▼                              ▼
┌───────────────┐             ┌───────────────┐
│    WORKER     │             │    WORKER     │     ... more Workers
│  ───────────  │             │  ───────────  │
│  Executes     │             │  Executes     │
│  Has tools:   │             │  Has tools:   │
│  read/write   │             │  read/write   │
│  terminal     │             │  terminal     │
│  web_search   │             │  web_search   │
│  browser      │             │  browser      │
│  Self-decomp. │             │  Self-decomp. │
└───────┬───────┘             └───────┬───────┘
        │ TaskResult                   │ TaskResult
        ▼                              ▼
┌──────────────────────────────────────────────────────────────────────────┐
│                          REVIEWER  (Independent Audit)                    │
│   ──────────────────────────────────────────────────────────────────     │
│   Audits EVERY Worker output against acceptance criteria.               │
│   Five-level verdict. Four-level defect severity. ZERO TOOLS.           │
│   Independent LLM instance — does NOT share context with Workers.       │
└──────────────────────────────────────────────────────────────────────────┘
```


**Four roles. Hard boundaries. No overlap.**

---

## Quick Start

```bash
# 1. Clone
git clone https://github.com/isheng-eqi/janus-agent.git
cd janus

# 2. Install dependencies
pip install pyyaml colorama openai

# 3. Set your DeepSeek API key
echo 'DEEPSEEK_API_KEY=«redacted:sk-…»' > .env

# 4. Run
python main.py
```

```text
❯ 帮我写一个 Python 脚本来排序 CSV 文件

Gatekeeper → Planner → Workers → Reviewer → Report

✅ task-1: Parse CSV reading logic — PASSED
✅ task-2: Implement sorting algorithm — PASSED
✅ task-3: Write output with error handling — PASSED

产出文件: ./output/sort_csv.py
```

*Requirements: Python 3.10+, DeepSeek API key. See [config.yaml](config.yaml) for advanced configuration (heterogeneous models, max depth, tool limits).*


## Core Mechanisms

**Gatekeeper Tree** — Tasks decompose recursively. The Gatekeeper decides WHAT, the Planner decides HOW, Workers execute, Reviewer audits. Every layer has a single responsibility with hard boundaries.

**Five-Level Review** — Not just pass/fail. `APPROVED` | `APPROVED_WITH_NOTES` | `MINOR_REVISIONS` | `MAJOR_REVISIONS` | `REJECTED`. Inherited from academic peer review. Different verdicts trigger different retry strategies.

**Four-Level Defect Severity** — `CRITICAL` (unusable) → `MAJOR` (core unmet) → `MINOR` (partial deviation) → `SUGGESTION` (nice-to-have). From manufacturing quality control: not all bugs are equal.

**Commander's Intent** — Workers don't just get "what to do." They get `intent` — why this task matters in the bigger picture. When unexpected things happen, they make decisions aligned with purpose, not just instructions.

**Immutable Anchor** — The user's original words (`user_goal`) travel untouched through every layer. Gatekeeper → Planner → Worker → Reviewer all see the exact same original input. No telephone game.

**Self-Healing Recovery Loop** — When tasks fail, the Gatekeeper doesn't just retry. It diagnoses WHY (LLM analysis of failure patterns), reformulates strategy (different decomposition, different approach), and re-executes. Two levels: Planner retries fix execution; Gatekeeper recovery fixes direction.

**Self-Evolution** — Workers automatically record execution experience; the Planner references historical patterns when decomposing tasks, making the system smarter with every run.

**Intent Validation** — Before delivering results to the user, one final check: "Is this what the user actually asked for?" A lightweight LLM call that catches the most expensive kind of bug — delivering the wrong thing perfectly.

**Context Discipline** — Every role sees only what it needs. The Gatekeeper never sees Worker tool-call logs. The Reviewer never sees strategic intent that would bias its audit. Inherited from management's "span of control" — humans can't process everything, and neither can LLM context windows.


## Design Philosophy

> **"Agent managing Agent should mirror Human managing Human."**
>
> This is not a metaphor. It's Janus's first design principle.

*Every design decision starts with one question: "How do human organizations solve this?" — not "What's the most efficient technical solution?"*

- Military command chains → Gatekeeper → Planner → Worker hierarchy
- Judicial review standards → Five-level graded verdicts (not binary pass/fail)
- Manufacturing quality control → Three lines of defense, four-level defect severity
- Academic peer review → Independent Reviewer, desk reject, revision-and-resubmit
- Commander's Intent → `TaskSpec.intent` — know WHY, not just WHAT
- Span of Control → Context discipline — each role sees exactly what it needs

*We have 3,000+ years of organizational wisdom. Janus applies it to LLMs.*
[Read the full philosophy →](docs/design-philosophy.md)


## How Janus Differs

| Aspect | LangGraph / AutoGen / CrewAI | Janus |
|--------|------------------------------|-------|
| **Design principle** | "What can LLMs do?" | "How do humans manage?" |
| **Architecture** | You design the flow | Four-role system with hard boundaries |
| **Task decomposition** | Manual graph / conversation flow | Recursive Gatekeeper Tree with independent audit at each node |
| **Quality control** | Built-in pass/fail at best | Five-level verdict + four-level defect severity |
| **Failure handling** | Retry loop | Diagnosis → strategy reformulation → re-execution with merged reporting |
| **Context management** | Full history or manual pruning | Role-based context discipline — each role sees only its layer |
| **Intent alignment** | Implicit | Explicit: immutable user_goal anchor + pre-delivery validation |


> ### Documentation
>
> | Document | Description |
> |----------|-------------|
> | [**Whitepaper (PDF)**](paper/janus_whitepaper.pdf) | Full technical whitepaper — architecture, mechanisms, evaluation, comparison |
> | [**Whitepaper (Chinese)**](paper/janus_whitepaper_zh.html) | 中文版白皮书 |
> | [**Design Philosophy**](docs/design-philosophy.md) | Why human management patterns, and how they map to Janus |
> | [**Human Management Patterns**](docs/human-management-patterns.md) | Deep dive into 6 organizational domains and their agent mappings |
> | [**config.yaml**](config.yaml) | Configuration reference — models, depth limits, tool caps |
>
> ### Contributing
>
> Janus is in active development. Contributions are welcome!
>
> 1. Fork the repository
> 2. Create a feature branch (`git checkout -b feature/amazing-feature`)
> 3. Make your changes
> 4. Run tests (`pytest tests/`)
> 5. Submit a Pull Request
>
> *Please read [CONTRIBUTING.md](CONTRIBUTING.md) for detailed guidelines.*
>
> ### License
>
> MIT © 2026 · [LICENSE](LICENSE) · *Built on the insight that 3,000 years of human management wisdom is the best design manual for agent architectures.*
