# AgentCanary 项目分析报告

> 分析依据：对 `C:\Users\HI\Desktop\agentcanary\src\agentcanary\` 下全部 13 个 Python 源文件的逐模块阅读。
> 生成时间：2026-07-16

---

## 一、项目定位

**AgentCanary** 是一个 **CLI 驱动的自主 AI Agent 渗透测试工具**。它使用 LLM（DeepSeek）作为攻击决策引擎，覆盖 5 层攻击面（LLM 注入 / Agent 行为 / MCP 协议 / 供应链 / 多轮越狱），配备约束驱动自进化记忆系统。

- **定位**：不是固定攻击列表的扫描器（区别于 garak / deepteam），而是像真人渗透测试者一样「侦察 → 分析 → 攻击 → 学习」的自主 Agent。
- **输入**：用户在 TUI 中输入自然语言指令（如 `测 mock`、`测 https://...`、`测 KroWork`）。
- **输出**：实时流式显示推理过程 + 工具调用结果 + 攻击结论。
- **自进化**：每次攻击后自动记录经验到 Memory，6000 字容量限制触发 LLM 自主整理。
- **代码规模**：~1,869 有效行，14 个功能文件。

---

## 二、项目文件结构

```
C:\Users\HI\Desktop\agentcanary\
├── pyproject.toml                     # 构建配置 (hatchling), 入口 canary=agentcanary.main:main
└── src/agentcanary/
    ├── __init__.py                    # 版本 0.1.0
    ├── main.py          (13 行)       # ⭐ CLI 入口
    ├── chat.py          (438 行)      # ⭐ 核心对话引擎 (ChatLoop)
    ├── llm.py           (65 行)       # LLM 客户端封装
    ├── config.py        (41 行)       # 配置持久化 (~/.agentcanary/config.json)
    ├── security.py      (63 行)       # 安全层 (注入扫描 + 执行边界)
    ├── memory/
    │   ├── __init__.py  (空)
    │   └── store.py     (224 行)      # ⭐ 约束驱动记忆系统 (MemoryStore)
    └── tools/
        ├── __init__.py  (空)
        ├── registry.py  (42 行)       # 工具注册表 (Tool/ToolRegistry/ToolResult)
        ├── recon.py     (107 行)      # L1: 侦察+注入
        ├── agent.py     (48 行)       # L2: Agent 行为攻击
        ├── mcp.py       (39 行)       # L3: MCP 协议攻击
        ├── supply.py    (28 行)       # L4: 供应链审计
        ├── multiturn.py (38 行)       # L5: 多轮越狱
        ├── discovery.py (282 行)      # ⭐ 目标自动发现管线
        ├── binary.py    (404 行)      # ⭐ 二进制逆向分析 (PE/ELF/Mach-O)
        └── universal.py (67 行)       # 通用工具 (terminal/read_file/web_search)
```

---

## 三、程序启动流程

### 3.1 入口链

```
canary CLI 命令 (pyproject.toml 注册)
  → main.py:main()
    → ChatLoop() 初始化
      → 注册所有工具 (L1-L5 + discovery + universal + binary + memory)
      → 加载 MemoryStore (~/.agentcanary/MEMORY.md)
      → 创建 ExecutionBoundary
    → asyncio.run(loop.run())
```

### 3.2 核心执行循环 (ChatLoop.run)

```
while running:
  1. 读取用户输入 (rich Console.input)
  2. 检测内置命令: /help /tools /memory /skills /quit
  3. 检测 API key 输入 (sk-... 开头)
  4. 预处理输入 (_preprocess): 闲聊检测 → mock/KroWork/URL 路由
  5. 注入扫描 (security.scan_content)
  6. 调用 _chat_turn() 进入 LLM 驱动的工具调用循环
```

### 3.3 单回合工具调用循环 (_chat_turn)

```
for step in 0..25:                    # 最多 25 步
  1. Token 估算 (total_chars/4) → 超 400K 则 Sandwich 压缩
  2. LLM.think_with_tools()           # 最多 3 次重试（处理 429/503/timeout）
     → 返回 (reasoning, reply, tool_call)
  3. 显示推理内容 (DeepSeek thinking)
  4. 如果无 tool_call → 输出 reply, 退出循环
  5. 如果有 tool_call → _exec() 执行
     - 参数别名映射 (url→target_url, command→cmd 等)
     - ExecutionBoundary 校验
     - 调用对应工具函数
     - 结果回填 messages
  6. 循环结束 (25步耗尽) → 提示用户
  7. 自动反思 (_auto_reflect): 扫描 tool 输出中 SUCCESS/FAILED 信号 → 写入 Memory
```

---

## 四、核心类定义与关键接口

### 4.1 `main.py` — 入口点 (13 行)

```python
def main():
    loop = ChatLoop()
    asyncio.run(loop.run())

if __name__ == "__main__":
    main()
```

**关键点**：极简入口，仅初始化 ChatLoop 并启动 asyncio 事件循环。

---

### 4.2 `chat.py` — ChatLoop 类 (438 行)

| 方法 | 签名 | 职责 |
|---|---|---|
| `__init__` | `(self)` | 注册所有工具 (20+)；初始化 MemoryStore、ExecutionBoundary、ToolRegistry；内联注册 memory_add/memory_batch/memory_search 三个记忆工具 |
| `run` | `async (self)` | 主循环：显示欢迎面板 → 加载/请求 API key → 冻结记忆快照 → 构建系统提示词 → 循环读取用户输入并路由 |
| `_init_llm` | `(self, api_key: str)` | 从 config 加载模型参数，创建 LLMClient |
| `_preprocess` | `(self, text: str) -> str` | 输入预处理：闲聊检测 → mock 路由 → KroWork 路由 → URL 路由 |
| `_chat_turn` | `async (self, user_input: str)` | 核心 LLM 工具调用循环 (25 步)，含 Token 估算/压缩/重试/自动反思 |
| `_exec` | `async (self, name: str, args: str) -> ToolResult` | 工具执行：参数解析 → 别名映射 → 执行边界校验 → 调用工具函数 |
| `_compact_context` | `(self)` | Sandwich 上下文压缩：保护首尾，裁剪中间旧工具输出 |
| `_auto_reflect` | `(self)` | 自动反思：扫描最近 20 条消息中 SUCCESS/FAILED 信号 → 写入 Memory |
| `_show_memory` | `(self)` | 显示记忆内容 |
| `_show_help` | `(self)` | 显示帮助信息 |

**关键属性**：
- `self.llm: LLMClient | None` — LLM 客户端
- `self.memory: MemoryStore` — 记忆存储
- `self.boundary: ExecutionBoundary` — 执行边界
- `self.tools: ToolRegistry` — 工具注册表
- `self.messages: list[dict]` — 对话消息历史
- `self.running: bool` — 运行标志
- `self.target_url: str` — 当前目标 URL

---

### 4.3 `llm.py` — LLMClient 类 (65 行)

```python
@dataclass
class LLMConfig:
    api_key: str
    base_url: str = "https://api.deepseek.com"
    model: str = "deepseek-v4-pro"
    max_tokens: int = 2000
    deep_think: bool = True

class LLMClient:
    def __init__(self, config: LLMConfig):
        self.config = config
        self.client = AsyncOpenAI(api_key=config.api_key, base_url=config.base_url)
        self._is_deepseek = "deepseek" in config.base_url

    async def think_with_tools(
        self,
        messages: list[dict],
        tools: list[dict]
    ) -> tuple[str, str, dict | None]:
        """返回 (reasoning, reply, tool_call)"""
        # 仅 DeepSeek 启用 deep think
        # 返回: reasoning(推理内容), reply(回复文本), tool_call({"name", "arguments"})
```

**关键点**：
- 封装 OpenAI SDK (`AsyncOpenAI`)
- `think_with_tools` 返回三元组 `(reasoning, reply, tool_call)`
- 仅 DeepSeek API 启用 `thinking` 扩展参数（其他 provider 跳过）

---

### 4.4 `config.py` — 配置持久化 (41 行)

```python
def load() -> dict                        # 读取 ~/.agentcanary/config.json
def save(data: dict)                      # 写入配置
def get_api_key() -> str                  # 获取 API key
def set_api_key(key: str)                 # 保存 API key
def get_llm_config() -> dict              # 返回 LLM 配置（含默认值）
```

**存储路径**: `~/.agentcanary/config.json`，明文存储 API key（安全问题 #2）。

---

### 4.5 `security.py` — 安全层 (63 行)

```python
INJECTION_PATTERNS = [...]               # 13 个硬编码注入模式

def scan_content(text: str) -> str | None            # 注入检测（字符串包含匹配）
def sanitize_for_snapshot(entries: list[dict]) -> list[dict]  # 消毒记忆条目

class ExecutionBoundary:
    def __init__(self, allowed_urls: set = None)
    def allow_url(self, url: str)                     # 添加白名单 URL
    def validate(self, tool_name: str, params: dict) -> str | None  # 校验工具调用
```

**关键设计**：
- 双重防护：`scan_content`(注入检测) + `ExecutionBoundary`(执行边界)
- 硬编码不可关闭（"always on, cannot be disabled"）
- URL 白名单 + 自攻击防护（localhost/127.0.0.1 限制 payload）
- **漏洞**：13 个注入模式仅做 `in` 字符串匹配，可被 Unicode 变体/Base64 绕过

---

### 4.6 `memory/store.py` — MemoryStore 类 (224 行)

```python
class MemoryStore:
    def __init__(self, char_limit: int = 6000)

    # 核心方法
    def add(self, content: str, category: str = "tactic", confidence: float = 0.5) -> dict
    def batch(self, operations: list[dict]) -> dict       # 批量操作（remove/replace/add）
    def search(self, query: str, top_n: int = 5) -> list[str]
    def freeze(self)                                       # 冻结快照（session-start）
    def snapshot_text(self) -> str
    def stats(self) -> dict
    def all_entries(self) -> list[str]

    # 内部方法
    def _load(self)                                        # 从文件加载
    def _save(self)                                        # 写入文件
    def _usage(self) -> int                                # 当前字符数

    # 关键属性
    self.path: Path                              # ~/.agentcanary/MEMORY.md
    self.entries: list[str]                      # 实时条目（live state）
    self._snapshot: list[str]                    # 冻结快照（prefix cache 稳定）
    self.char_limit: int = 6000                  # 容量上限
    self._consolidation_failures: int            # 整理失败计数（3次降级）
```

**关键设计模式**：
- **双态模型**：`entries`(实时) + `_snapshot`(冻结快照) — 保护 system prompt 的 prefix cache 命中率
- **约束驱动**：容量超限返回 `needs_consolidation` 信号而非拒绝写入
- **批量原子操作**：`batch()` 支持 remove/replace/add 原子操作，全或全不
- **3 次失败降级**：`MAX_CONSOLIDATION_FAILURES_PER_TURN = 3` — 连续 3 次整理失败后停止重试
- **外部漂移检测**：`_detect_drift()` 检测文件被外部修改 → 创建 `.bak` 备份

---

### 4.7 `tools/registry.py` — 工具系统 (42 行)

```python
@dataclass
class ToolResult:
    tool_name: str
    success: bool
    output: str
    error: str = ""

@dataclass
class Tool:
    name: str
    description: str
    parameters: dict                    # {name: description}
    func: Callable[..., Awaitable[ToolResult]]
    required: list[str] | None = None   # None = 仅第一个参数必填

class ToolRegistry:
    def register(self, tool: Tool)
    def get(self, name: str) -> Tool | None
    def list_all(self) -> list[Tool]
    def describe(self) -> str
```

**轻量设计**：无依赖注入，纯数据类 + 字典注册。

---

### 4.8 工具模块概览 (5 层攻击面 + 辅助)

#### L1: 侦察 + 注入 (`recon.py`, 107 行)

| 工具函数 | 参数 | 描述 |
|---|---|---|
| `recon_probe(target_url)` | URL | MITRE ATLAS 侦察 — 探测目标能力/防御 |
| `send_payload(target_url, payload)` | URL + payload | LLM 注入攻击 (ASI01/LLM01/LLM07) |
| `analyze_result(response)` | 响应文本 | 分析攻击结果 (SUCCESS/FAILED/UNCERTAIN) |
| `test_memory_poison(target_url, poison_text)` | URL + 毒文本 | 记忆投毒测试 (ASI06) |
| `verify_memory_poison(target_url, trigger)` | URL + 触发词 | 验证投毒持久化 |

#### L2: Agent 行为 (`agent.py`, 48 行)

| 工具函数 | 参数 | 描述 |
|---|---|---|
| `inject_params(target_url, payload)` | URL + payload | 参数注入 (ASI02/ASI05) |
| `hijack_goal(target_url, fake_goal)` | URL + 伪造目标 | 目标劫持 (ASI01) |
| `abuse_trust(target_url, spoofed_source)` | URL + 伪装来源 | 信任滥用 (ASI09) |

#### L3: MCP 协议 (`mcp.py`, 39 行)

| 工具函数 | 参数 | 描述 |
|---|---|---|
| `scan_mcp_tools(mcp_url)` | MCP URL | 扫描工具描述投毒 (MCP Top 10) |

#### L4: 供应链 (`supply.py`, 28 行)

| 工具函数 | 参数 | 描述 |
|---|---|---|
| `audit_skills(target_url)` | URL | 供应链审计 (ASI04) |

#### L5: 多轮越狱 (`multiturn.py`, 38 行)

| 工具函数 | 参数 | 描述 |
|---|---|---|
| `multi_turn_attack(target_url, turns_json)` | URL + JSON 轮次 | 多轮分布式攻击 |

#### 目标发现 (`discovery.py`, 282 行)

| 工具函数 | 参数 | 描述 |
|---|---|---|
| `discover_target(target_name)` | 目标名称 | 4 步自动发现管线 |

**内部方法**：
- `_scan_processes(target)` — 通过 `tasklist` 扫描进程
- `_scan_ports(target)` — 通过 `netstat -ano` 扫描端口
- `_extract_from_logs(target)` — 从日志/配置中提取 API URL/Token/Model
- `_probe_endpoints(target)` — HTTP 探测常见 API 路径

**KNOWN_AGENTS** 字典硬编码：KroWork、OpenClaw、Cursor 的进程名/日志路径/配置路径。

#### 二进制逆向 (`binary.py`, 404 行)

| 工具函数 | 参数 | 描述 |
|---|---|---|
| `analyze_binary(path)` | 文件路径 | 4 阶段专业逆向 |

**4 阶段管线**：
1. **Phase 1**: 二进制识别 (MZ/PE/ELF/Mach-O/PyInstaller/.NET/Electron/UPX) + 香农熵检测
2. **Phase 2**: 字符串提取 + 安全相关性评分 (12 种模式: API key/endpoint/model/MCP/auth)
3. **Phase 3**: PyInstaller `.pyc` 提取, Electron asar 分析
4. **Phase 4**: 安全信号汇总 (API 端点/疑似凭证/模型名/端口)

#### 通用工具 (`universal.py`, 67 行)

| 工具函数 | 参数 | 描述 |
|---|---|---|
| `tool_terminal(cmd)` | shell 命令 | 执行 shell 命令 (⚠ `create_subprocess_shell` — shell 注入风险) |
| `tool_read_file(path)` | 文件路径 | 读取文件 (4000 字符限制) |
| `tool_web_search(query)` | 搜索词 | DuckDuckGo HTML 抓取 |

---

## 五、数据流架构

```
用户输入
  │
  ▼
ChatLoop._preprocess()
  │  闲聊检测 → mock/URL/KroWork 路由
  ▼
security.scan_content()         ─── 注入检测（13 个硬编码模式）
  │
  ▼
ChatLoop._chat_turn()
  │
  ├─ Token 估算 → 超 400K → Sandwich 压缩
  ├─ LLM.think_with_tools()     ─── DeepSeek/OpenAI API
  ├─ 有 tool_call?
  │   ├─ 否 → 输出 reply → 结束
  │   └─ 是 → ChatLoop._exec()
  │         ├─ 参数别名映射
  │         ├─ ExecutionBoundary.validate()  ─── URL 白名单 + 自攻击防护
  │         └─ tool.func(**params)           ─── 调用实际工具
  ├─ 结果回填 messages
  └─ 循环 (最多 25 步)
  │
  ▼
ChatLoop._auto_reflect()
  │  SUCCESS/FAILED 信号扫描 → MemoryStore.add()
  ▼
MemoryStore
  ├─ 双态模型 (entries + _snapshot)
  ├─ 6000 字容量触发 → needs_consolidation
  └─ 文件持久化 (~/.agentcanary/MEMORY.md)
```

---

## 六、类关系图

```
main.py
  │
  └── ChatLoop (chat.py)
        ├── LLMClient (llm.py)             ← OpenAI SDK
        ├── MemoryStore (memory/store.py)   ← ~/.agentcanary/MEMORY.md
        ├── ExecutionBoundary (security.py)  ← 安全校验
        ├── ToolRegistry (tools/registry.py)
        │     ├── L1: recon_probe / send_payload / analyze_result / ...
        │     ├── L2: inject_params / hijack_goal / abuse_trust
        │     ├── L3: scan_mcp_tools
        │     ├── L4: audit_skills
        │     ├── L5: multi_turn_attack
        │     ├── discovery: discover_target
        │     ├── binary: analyze_binary
        │     ├── universal: terminal / read_file / web_search
        │     └── memory: memory_add / memory_batch / memory_search
        └── config (config.py)              ← ~/.agentcanary/config.json
```

---

## 七、关键设计决策

| 决策 | 实现 | 理由 |
|---|---|---|
| 同步函数包装为 async | discovery.py 中 `_scan_processes` 等声明为 async 但内部使用同步 subprocess | (问题) 无实际并发收益 |
| 内存双态模型 | `entries` + `_snapshot` | 保护 system prompt prefix cache 命中率 |
| 约束驱动 Memory | 超限返回 `needs_consolidation` 而非拒绝 | 避免 LLM 工具调用因拒绝而中断 |
| Sandwich 压缩 | 保护 head+tail，裁剪中间旧工具输出 | 保留最近上下文，减少 Token 消耗 |
| 3 次失败降级 | `_consolidation_failures` 计数 | 防止 Memory 整理陷入死循环 |
| 参数别名映射 | `_exec` 中的 ALIASES 字典 | LLM 可能使用不同参数名（脆弱设计） |
| 安全硬编码 | `security.py` 不可配置/不可关闭 | 与 Hermes Agent 设计哲学一致 |
| 注入模式字符串匹配 | `in` 操作符 + 小写化 | (问题) 可被 Unicode/Base64 绕过 |

---

## 八、识别的问题

### 安全问题
1. **terminal 工具的 shell 注入风险** (`universal.py:14`) — 使用 `create_subprocess_shell(cmd)` 而非 `create_subprocess_exec`
2. **API Key 明文存储** (`config.py`) — `~/.agentcanary/config.json` 无加密
3. **注入检测可绕过** (`security.py`) — 13 个固定字符串 `in` 匹配
4. **Memory 条目无完整性校验** — `MEMORY.md` 可直接被文本编辑器注入

### 代码质量问题
1. `_sim()` 函数在 `chat.py:436` 和 `memory/store.py:222` 各有一份相同实现
2. 同步函数包装为 async — 无实际并发收益
3. 裸 `except: pass` — `discovery.py` 中多处吞掉异常
4. `pyyaml/pydantic` 依赖冗余 — 声明但未使用
5. 参数别名硬编码 — 新增工具时容易忘记更新 ALIASES

---

## 九、统计数据

| 指标 | 数值 |
|---|---|
| Python 源文件 | 13 个（含 3 个空 __init__.py） |
| 有效代码行数 | ~1,869 行 |
| 注册工具数 | 20+（5 层攻击面 + 发现 + 通用 + 二进制 + 3 个 memory 工具） |
| 最大模块 | `binary.py` (404 行) / `chat.py` (438 行) |
| 依赖 | openai / httpx / rich / pyyaml(冗余) / pydantic(冗余) |
| 默认 LLM | DeepSeek (deepseek-v4-pro) |
| 许可证 | MIT |

---

## 十、与 Janus 架构对比

| 维度 | AgentCanary | Janus |
|---|---|---|
| 架构模式 | 单 Agent ChatLoop | 四层 Agent (Gatekeeper→Planner→Worker→Reviewer) |
| 执行引擎 | LLM 驱动工具调用循环 | Planner 分解 + Worker 执行 + Reviewer 审查 |
| 记忆系统 | 约束驱动双态 MemoryStore | 无独立记忆系统 |
| 工具系统 | ToolRegistry 注册 + 直接调用 | read_file/write_file/terminal/web_extract/search_files/patch/execute_code |
| 入口点 | `main.py:main() → ChatLoop.run()` | `main.py:main() → loop.run()` |
| 安全模型 | 注入扫描 + 执行边界 | Gatekeeper 意图验证 |
| 上下文压缩 | Sandwich 压缩 (保护首尾) | 无 |
