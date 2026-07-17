# CLI 界面美学规范研究 —— Janus 设计参考文档

> 基于 clig.dev、12 Factor CLI Apps、Atlassian CLI 设计原则、ThoughtWorks CLI 指南、Evil Martians CLI UX 最佳实践等权威资料整理。

---

## 一、核心理念

### 1. 人类优先设计（Human-first Design）

传统 UNIX 命令假设主要用户是其他程序，而现代 CLI 工具的首要用户是**人类**。这意味着：

- 默认输出应该是人可读的，而非机器友好的
- 提供 `--plain` 或 `--json` 标志给脚本/自动化场景
- 当 stdout 是终端（TTY）时，使用颜色、格式、表格和 emoji；管道到文件时，自动降级为纯文本

### 2. 对话即交互（Conversation as the Norm）

CLI 的本质是用户与程序之间的**对话**：用户输入命令 → 得到反馈 → 调整 → 重试。好的 CLI 设计应拥抱这一特性：

- 输入无效时，尝试推测用户意图并给出建议（如 git 的 "Did you mean...?"）
- 多步骤操作时，每一步完成后提示下一步可执行的操作
- 破坏性操作前明确确认

### 3. 渐进披露与可发现性（Progressive Disclosure & Discoverability）

- `--help` 提供完整参考，无参数运行时给出精简帮助
- 用例子引导，而非长篇文档
- 常见命令和标志列在前面
- 完成一步后提示下一步该做什么

---

## 二、颜色使用规范

### 核心原则：用颜色传达语义，而非装饰

> **"以意图驱动颜色。信息密度越低的输出越可以从颜色中获益。"** —— clig.dev

### 推荐的 5 色语义调色板

| 颜色 | 语义 | 使用场景 | 示例 |
|------|------|----------|------|
| **绿色** | 成功/完成 | 操作成功、检查通过、完成状态 | `✓ Done` |
| **红色** | 错误/失败/危险 | 错误消息、删除确认、失败状态 | `✗ Error: file not found` |
| **黄色/琥珀色** | 警告/注意 | 警告信息、需要注意但非致命的问题 | `⚠ Warning: deprecated option` |
| **青色/蓝色** | 信息/强调 | 关键信息高亮、状态标签、链接 | `ℹ Fetching data...` |
| **白色/默认** | 普通正文 | 大多数输出内容 | 主体输出文本 |

### 颜色数量限制

- **不超过 5 种颜色**用于语义化输出（不包括默认前景色）
- 如果所有内容都有不同颜色，颜色就失去了意义——反而增加阅读难度
- 同一输出行中，最多使用 2 种前景色

### 辅助样式（不引入新颜色）

| 样式 | 用途 |
|------|------|
| **加粗** | 标题、命令名、重点关键词 |
| **暗化/灰色** | 次要信息、元数据、提示文字 |
| **下划线** | 可点击链接（如果终端支持） |
| **斜体** | 温和强调（终端支持有限） |

### 颜色禁用机制（必须遵守）

以下条件应**自动禁用颜色**：

1. `stdout` 或 `stderr` 不是 TTY（正在被管道）
2. `NO_COLOR` 环境变量已设置（符合 [no-color.org](https://no-color.org/) 标准）
3. `TERM=dumb`
4. 用户传入 `--no-color` 标志
5. 可选：提供 `MYAPP_NO_COLOR` 环境变量

### ANSI 颜色技术选型建议

- **优先使用 8 种基本 ANSI 颜色**（而非 RGB），因为终端配色方案可能被用户自定义
- 基本色在浅色/深色主题下都有较好的对比度
- 避免硬编码 RGB 值——尊重用户的终端主题选择
- 如需更丰富的颜色，使用 256 色调色板，而非 24-bit True Color

---

## 三、输出层级设计

### 三级信息架构

```
第一层：一句话摘要（用户扫一眼就能知道结果）
   └── 用颜色 + 图标快速标识状态（✓ 成功 / ✗ 失败 / ⚠ 警告）
   
第二层：结构化详情（表格、列表、分组）
   └── 用户想了解细节时，一目了然
   
第三层：调试/Raw 数据（仅在 --verbose 或出错时展示）
   └── 给高级用户和开发者
```

### 输出排版的黄金法则

1. **最重要的信息放在最后。** 用户的视线会自然落在输出的末尾——put the conclusion there.
2. **每个输出行 = 一条独立记录。** 这让 `grep`、`wc`、`awk` 能正常工作。
3. **表格不加边框。** 边框增加噪音且破坏 grep 兼容性。用空格对齐即可。
4. **根据终端宽度自适应。** 检测 `COLUMNS` 环境变量，超出宽度的内容截断或换行。
5. **使用空行分隔不同的逻辑块。** 但不要滥用——一个空行 = 一个语义分割。

### 成功输出的简洁原则

- 操作成功时，**简要汇报发生了什么**（特别是改变状态的操作）
- 无输出 = 用户会怀疑程序卡死了
- 示例：`git push` 告诉用户推送了多少对象、目标分支是什么
- 提供 `-q/--quiet` 选项让高级用户压制输出

### 状态变更时明确告知

```
✓ Created project "my-app" in ./my-app
✓ Initialized git repository
✓ Installed 42 dependencies
→ Next: cd my-app && janus start
```

---

## 四、信息密度控制

### 什么该显示（默认模式）

- ✅ 操作结果（成功/失败/警告）
- ✅ 当前进度（长任务时）
- ✅ 改变的系统状态
- ✅ 下一步建议
- ✅ 命令的执行摘要（完成后的统计）

### 什么不该显示（默认模式）

- ❌ 调试日志、堆栈跟踪
- ❌ 内部实现细节（如 "正在连接到 192.168.1.1:8080..."）
- ❌ 日志级别标签（如 `[INFO]`、`[DEBUG]`），除非在 verbose 模式
- ❌ 仅在开发者视角下有意义的输出
- ❌ 重复性信息（如多个同样类型的错误，应当归组显示）

### 分级显示策略

| 模式 | 触发条件 | 显示内容 |
|------|----------|----------|
| 安静模式 `-q` | 脚本/自动化 | 仅输出 bare data，无装饰 |
| 默认模式 | TTY 交互 | 彩色、格式化、进度指示、摘要 |
| 详细模式 `-v` | 故障排查 | + 每个步骤的名字和耗时 |
| 调试模式 `--debug` | 开发调试 | + 完整的请求/响应、堆栈跟踪 |

---

## 五、进度指示规范

### 三种模式的选择指南

| 模式 | 适用场景 | 典型用法 |
|------|----------|----------|
| **Spinner（旋转动画）** | 不确定耗时的短任务（几秒内）；无额外信息可显示 | `⠋ Fetching data...` |
| **X of Y（计数式）** | 步骤数量已知且可计量 | `Processing files... (3/12)` |
| **Progress Bar（进度条）** | 多个并行长任务；需要视觉化进度感知 | `[████████░░░░] 67%` |

### 进度显示铁律

1. **必须更新。** 静态的 spinner 比没有更糟糕——用户无法判断是卡死还是运行中。每次实际操作完成时 tick 一次。
2. **非 TTY 环境不使用动画。** 管道输出中的进度条会变成圣诞树状的乱码。
3. **任务完成后清理进度指示。** 用最终状态行替代进度行。
4. **动词时态要变。** 进行中用 `-ing`（Downloading...），完成后用 `-ed`（Downloaded ✓）。
5. **显示耗时时长。** 完成后展示总耗时对用户非常有用。

### Docker pull 的多层进度条（典范案例）

Docker 的镜像拉取是 CLI 进度显示的金标准：

```
latest: Pulling from library/ruby
6c33745f49b4: Pull complete
ef072fc32a84: Extracting [====>              ] 7.57MB/15.6MB
d599c07d28e6: Download complete
f2ecc74db11a: Downloading [========>         ] 89.1MB/192MB
```

- 每行一个独立操作（层）
- 每行显示操作类型（Pull/Extract/Download）和进度
- 操作完成的行保留在屏幕上作为历史记录
- 整体进度一目了然

### Janus 进度策略建议

| Janus 场景 | 推荐模式 |
|------------|----------|
| 启动 Agent | Spinner（快速，几秒内完成） |
| 下载模型 | Progress Bar（已知文件大小） |
| 代码分析（Agent 内部推理） | Spinner + 状态短语轮换 |
| 多文件批量处理 | X of Y + 当前文件名 |
| 长时间对话推理 | 流式输出 + 字符级实时显示 |

---

## 六、错误信息展示规范

### 好错误消息的 5 要素

一个好的 CLI 错误消息应包含：

```
Error: EPERM - Cannot write to output file

  Cannot write to report.json because the file does not have write permissions.

  Fix with:
    chmod +w report.json

  Learn more: https://docs.janus.dev/errors/eperm
```

1. **错误码** — 可搜索、可引用
2. **错误标题** — 一句话描述什么问题
3. **错误描述** — 为什么会发生（可选但推荐）
4. **修复建议** — 用户接下来应该做什么（最关键！）
5. **更多信息链接** — 详细文档 URL

### 错误输出的排版规则

- 错误信息写到 `stderr`，不要污染 `stdout`
- 最重要的信息放在最后（用户视线落点）
- 红色用于最关键的错误标识，不滥用
- 如果能推测用户意图，主动给出"你是不是想..."的建议（如 git 的 typo 纠正）
- 对于预料之外的错误，提供提交 bug 的途径和 debug 日志路径
- 多个同类错误归组显示，而非重复 N 次

### 信号噪声比

- 不要让用户在一大堆无关输出中寻找错误原因
- 如果堆栈跟踪不可避免，默认折叠，用 `--verbose` 展开
- 使用空行把错误与正常输出隔开

### 退出码规范

- `0` = 成功
- 非零 = 失败，每种失败模式映射到不同退出码
- 脚本依赖正确的退出码来判断流程

---

## 七、知名 CLI 工具的界面案例

### 1. Docker — 多层进度条的典范

```
$ docker pull ruby
latest: Pulling from library/ruby
6c33745f49b4: Pull complete
ef072fc32a84: Extracting [====>              ] 7.57MB/15.6MB
d599c07d28e6: Download complete
f2ecc74db11a: Downloading [=======>           ] 89.1MB/192MB
```

**亮点：**
- 每层一个状态行，操作类型明确标注（Pull/Extract/Download）
- 完成的行保留在屏幕作为历史
- 多行同步更新，视觉密度高但不混乱
- 管道到文件时自动输出 JSON 格式（`--progress=plain` 可切换）

### 2. Git — 命令建议和状态可视化

```
$ git status
On branch main
Your branch is up to date with 'origin/main'.

Changes not staged for commit:
  (use "git add <file>..." to update what will be committed)
  (use "git restore <file>..." to discard changes)
        modified:   src/main.rs

no changes added to commit (use "git add" and/or "git commit -a")
```

**亮点：**
- 不仅告诉你"是什么"，还告诉你"怎么做"
- 每个状态区块附带可操作的命令提示
- 拼写错误时主动建议正确命令（"Did you mean...?"）
- 用空白行和缩进组织信息层次

### 3. npm — 人类可读的错误消息

```
$ npm install
npm ERR! code ENOENT
npm ERR! syscall open
npm ERR! path /Users/user/package.json
npm ERR! errno -2
npm ERR! enoent ENOENT: no such file or directory, open '/Users/user/package.json'
npm ERR! enoent This is related to npm not being able to find a file.
npm ERR! enoent

npm ERR! A complete log of this run can be found in:
npm ERR!     /Users/user/.npm/_logs/2024-01-01T00_00_00_000Z-debug.log
```

**亮点：**
- 错误类型分类明确（ENOENT）
- 告诉用户是什么问题（找不到文件）
- 指出和什么相关（npm 找不到 package.json）
- 提供完整的调试日志路径

### 4. Cargo (Rust) — 彩色分阶段构建输出

```
$ cargo build
   Compiling my-crate v0.1.0
    Finished dev [unoptimized + debuginfo] target(s) in 2.34s
```

**亮点：**
- 颜色编码：绿色表示成功，黄色表示警告，红色表示错误
- 每个编译单元一行，清晰展示进度
- 最终汇总行告诉用户构建时间和优化状态
- 编译过程中的警告在最终汇总前单独列出

### 5. kubectl — 一致的命令树和 JSON 输出

```
$ kubectl get pods --namespace=default
NAME                        READY   STATUS    RESTARTS   AGE
my-pod-7d4f8c9b6-x8k2l     1/1     Running   0          2d

$ kubectl get pods my-pod -o jsonpath='{.status.phase}'
Running
```

**亮点：**
- 名词+动词的命令结构（`kubectl get pods`），一致性极强
- `get` + 资源类型模式适用于所有资源（pods/deployments/services...）
- `-o` 标志支持多种输出格式（json/yaml/jsonpath/wide）
- 表格输出默认干净、对齐、无边框

### 6. GitHub CLI (gh) — 现代 CLI 设计的标杆

```
$ gh pr list
Showing 3 of 3 open pull requests in owner/repo

#42  Fix authentication bug    bug      about 1 hour ago
#41  Add dark mode support     enhancement  about 3 days ago
#40  Update dependencies       chore    about 1 week ago
```

**亮点：**
- 清晰的摘要行（"Showing X of Y"）
- 彩色标签区分 PR 类型
- 交互式模式与脚本模式都支持
- Figma 设计师深度参与的 CLI UX 项目

### 7. Yarn — 颜色和 Emoji 的情绪化使用

**亮点：**
- 用颜色编码不同重要性级别的信息（success/warning/error）
- Emoji 增加愉悦感和可读性（✨ 成功完成、⚠ 警告）
- 输出组织良好的分区结构
- 完成后显示耗时统计

---

## 八、命令行标志和参数规范

### 通用标志命名（业界标准）

| 短标志 | 长标志 | 含义 |
|--------|--------|------|
| `-h` | `--help` | 帮助（仅此一义，不重载） |
| `-v` | `--version` | 版本 |
| `-q` | `--quiet` | 减少输出 |
| `-f` | `--force` | 强制执行、跳过确认 |
| `-n` | `--dry-run` | 试运行，不实际执行 |
| `-o` | `--output` | 输出文件 |
| `-d` | `--debug` | 调试输出 |
| `-a` | `--all` | 所有/全部 |
| `-p` | `--port` | 端口 |
| `-u` | `--user` | 用户 |

### 标志 vs 参数

- **优先使用标志（flags）而非位置参数（args）** — 提高可读性，降低记忆负担
- 位置参数规则：一个类型可以，两个可疑，三个绝对不行
- 所有标志要有完整形式（`--verbose`），短形式（`-v`）用于常用标志
- 标志与子命令应**顺序无关**（允许 `mycmd --foo subcmd` 和 `mycmd subcmd --foo`）

---

## 九、交互性和输入

### 提示（Prompts）的规范

- **只能在不要求时使用 Prompt。** 总是提供标志来覆盖 prompt，以便脚本自动化。
- 如果 `stdin` 不是 TTY，跳过所有交互并直接要求标志 — 不要阻塞 CI/CD 管道。
- 提供 `--no-input` 标志显式禁用所有交互。
- 密码输入必须关闭回显（不回显字符到屏幕）。
- 破坏性操作确认：严重程度分级 — 轻度（删除文件）、中度（删除目录）、重度（删除远程服务）用不同级别的确认策略。

---

## 十、针对 Janus 的具体设计建议

### Janus CLI 定位分析

Janus 是一个 **AI Agent 框架的 CLI 工具**，其用户是 **开发者**。需要在"终端原生体验"与"AI 对话式交互"之间找到平衡。

### 推荐的颜色方案

```
绿色  → 操作成功、Agent 完成、确认
青色  → 信息提示、Agent 状态、正在处理
黄色  → 警告、降级行为、速率限制
红色  → 错误、失败、中断
灰色  → 次要信息、时间戳、元数据
白色  → 主体输出、Agent 回复内容
```

### 推荐的信息层次

```
janus build --target=production

  ✓ Configuration loaded (config/janus.yaml)
  ⠋ Building project...                    ← 进行中
  ✓ Build completed in 3.2s               ← 完成

  ── Build Results ──────────────────────
  Output:   ./dist/bundle.js  (142KB)
  Warnings: 2 (use --verbose to view)

  → Next: janus deploy --target=production
```

### 进度指示

- Agent 推理阶段：spinner + 轮换状态短语（"Analyzing codebase..." → "Identifying patterns..." → "Generating plan..."）
- 文件处理：X of Y + 当前文件名
- 网络操作：带速度的进度条
- 长时间推理：流式输出（逐字/逐 token 输出 Agent 的"思考"过程）

### 表格输出规范

```
janus agent list

  NAME          STATUS    MODEL           LAST ACTIVE
  ───────────────────────────────────────────────────
  code-review   active    claude-4        2 min ago
  test-gen      idle      deepseek-v3     1 hour ago
  deploy-bot    stopped   gpt-4o          yesterday
```

- 无边框，用破折号线分隔表头和内容
- 列对齐，根据内容自动调整列宽
- 超出终端宽度时截断，除非 `--no-truncate`

### 错误消息示例

```
janus start --config=invalid.yaml

Error: CONFIG_NOT_FOUND

  The configuration file "invalid.yaml" was not found.

  Make sure the file exists and is readable:
    ls -la invalid.yaml

  Or create a new config file:
    janus init --output=config.yaml

  Learn more: https://docs.janus.dev/config
```

### Emoji 使用原则

- ✅ 用于状态标记（✓ ✗ ⚠ ℹ）
- ✅ 少量用于增添个性（🚀 启动时、✨ 新功能）
- ❌ 不要每行都加 emoji — 会显得像玩具
- ❌ 不要在管道输出中使用（非 TTY 时自动去除）
- ⚠ 确保核心信息不依赖 emoji（纯文本降级后仍可读）

---

## 十一、参考资源

| 资源 | 链接 |
|------|------|
| Command Line Interface Guidelines | [clig.dev](https://clig.dev) |
| 12 Factor CLI Apps | [jdxcode.medium.com](https://jdxcode.medium.com/12-factor-cli-apps-dd3c227a0e46) |
| Heroku CLI Style Guide | [devcenter.heroku.com](https://devcenter.heroku.com/articles/cli-style-guide) |
| Atlassian: 10 Design Principles for CLIs | [atlassian.com](https://www.atlassian.com/blog/it-teams/10-design-principles-for-delightful-clis) |
| ThoughtWorks CLI Design Guidelines | [thoughtworks.com](https://www.thoughtworks.com/en-us/insights/blog/engineering-effectiveness/elevate-developer-experiences-cli-design-guidelines) |
| Evil Martians: CLI UX Best Practices | [evilmartians.com](https://evilmartians.com/chronicles/cli-ux-best-practices-3-patterns-for-improving-progress-displays) |
| UX Patterns for CLI Tools | [lucasfcosta.com](https://lucasfcosta.com/2022/06/01/ux-patterns-cli-tools.html) |
| ANSI Escape Codes Reference | [gist.github.com/fnky](https://gist.github.com/fnky/458719343aabd01cfb17a3a4f7296797) |
| no-color.org | [no-color.org](https://no-color.org) |
| Google: Writing Helpful Error Messages | [developers.google.com](https://developers.google.com/tech-writing/error-messages) |

---

> **文档版本：** v1.0  
> **编制日期：** 2026-07-17  
> **适用范围：** Janus CLI 界面设计决策参考
