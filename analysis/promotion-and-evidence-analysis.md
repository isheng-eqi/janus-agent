# Janus 推广方案 + 证据链评估

> 基于项目实际内容分析，生成日期：2026-07-16
> 分析依据：README.md、main.py、core/*.py、docs/*.md、tests/*.py、check/validation_result.json、report/final_report.md 等全量文件

---

## 一、推广方案

### 1.1 项目核心卖点（Core Selling Points）

| # | 卖点 | 一句话说明 |
|---|------|-----------|
| 1 | **管理哲学驱动** | 不是"LLM 能做什么"，而是"人类怎么管人类"——3000 年组织智慧映射为 Agent 架构 |
| 2 | **四角色硬边界** | Gatekeeper（军师/零工具）→ Planner（参谋/零工具）→ Worker（士兵/有工具）→ Reviewer（督察/零工具），每层职责不重叠 |
| 3 | **五级审查体系** | 从学术 peer review 借来的 APPROVED / APPROVED_WITH_NOTES / MINOR_REVISIONS / MAJOR_REVISIONS / REJECTED，不是二元 pass/fail |
| 4 | **指挥官意图传递** | Worker 不只拿到"做什么"，还拿到"为什么"，意图字段全链路贯穿（goal/intent/constraints/priority/depth 5字段无一丢失） |
| 5 | **自愈恢复循环** | 失败不是简单重试——先诊断原因，再制定策略，再重新执行，带完整反馈注入 |
| 6 | **上下文纪律** | 每层只看该看的——Gatekeeper 不看工具调用日志，Reviewer 不看战略意图，类比管理幅度理论 |

### 1.2 目标受众（Target Audience）

按优先级排列：

| 优先级 | 受众群体 | 为什么适合 | 触达方式 |
|--------|---------|-----------|---------|
| P0 | **AI Agent 开发者/架构师** | 能理解"框架设计哲学"差异，LangGraph/AutoGen/CrewAI 用户 | V2EX / GitHub / 掘金 |
| P0 | **大模型应用研究者** | 关注 Agent 架构演进，对"反 Scaling Law"叙事敏感 | 知乎 / 技术周刊 / arXiv |
| P1 | **开源社区贡献者** | 能参与代码贡献、Issue 讨论、PR 提交 | GitHub / Twitter / Reddit |
| P1 | **学术研究者（多Agent系统）** | 能引用和评估方法论创新，潜在的论文引用方 | arXiv / 学术会议 / 学校 |
| P2 | **高校学生（比赛导向）** | 挑战杯/互联网+/大创参赛者，需要差异化项目 | 校园渠道 / B站 / 即刻 |
| P3 | **国企/央企数字化岗招聘方** | 看重"完整""规范""有论文"的候选人 | ChinaXiv / 比赛获奖 / 简历 |

---

### 1.3 渠道策略与具体行动步骤（4 个渠道）

---

#### 渠道 1：中文技术社区深度长文 — V2EX + 知乎 + 掘金 三平台联动

**核心策略**：同一核心故事，三平台差异化角度。知乎负责深度（2500-3500字），V2EX 负责爆发（1500-2000字），掘金负责代码实操（2000-3000字）。三篇在同一周内密集发布形成"共振"。

---

##### 步骤 1.1 — 发布知乎专栏（D1 上午）

**具体动作**：
1. 打开知乎，点击"写文章"，使用以下模板：
   - **标题**："大二那年，我用军事指挥链、法院审查和工厂质检设计了一个 AI Agent 框架"
   - **正文结构（逐段内容）**：
     - **第1段（开场故事，300字）**：描述一个具体场景——"你让 AI 写一个网页爬虫，它写了，跑错了。你让它再试一次，它这次写得反而更糟。这不是 LLM 不够聪明——是你的团队管理出了问题。" 
     - **第2段（核心洞察，400字）**：链接人类管理问题与 AI Agent 问题的一致性——"军队2000年前就解决了'逐级下达命令不丢失意图'的问题，学术期刊几百年前就解决了'谁来检查质量'的问题。为什么 Agent 框架要重新发明轮子？"
     - **第3段（架构引入，600字）**：介绍四角色体系，每个角色一段。Gatekeeper=司令（不下战场），Planner=参谋（不拿枪），Worker=士兵（有工具），Reviewer=督察（独立于执行链）。
     - **第4段（代码示例，500字）**：贴一个实际的运行场景，含终端输出截图。展示一个任务的完整生命周期。
     - **第5段（与现有框架对比，500字）**：对比 LangGraph（有向图编排）、AutoGen（多 Agent 对话）、CrewAI（角色分配）——指出 Janus 的不同点在于"不是编排谁和谁对话，而是定义谁该做什么、谁不该做什么"。
     - **第6段（收尾，300字）**："这不是一个'更好'的框架——它是一个'不同'的框架。它问的问题不一样。GitHub：[链接]。欢迎讨论。"
   - **文中嵌入**：终端截图 2 张（一张展示运行过程，一张展示五级审查输出）
   - **文末**：GitHub 链接 + 白皮书 PDF 链接

2. **发布后立即回答 3-5 个相关问题**：搜索知乎上"AI Agent 框架"、"多 Agent 系统"、"LangGraph 好用吗"等话题下的问题，每个回答 100-200 字，自然引出自己的专栏。例如在"LangGraph 和 AutoGen 哪个更适合初学者？"下回答："看你的目标。如果想快速搭建多 Agent 对话，AutoGen 起步快。但如果关心'Agent 怎么保证任务质量'——我写了一篇用管理学思路设计 Agent 框架的文章，可以看看：[链接]"

---

##### 步骤 1.2 — 发布 V2EX 帖子（D2 上午）

**具体动作**：
1. 打开 V2EX，点击"发布新主题"，使用以下模板：
   - **标题**："我写了一个 Agent 框架，设计灵感全部来自管理学，不是 AI"
   - **正文（逐段内容）**：
     - **第1段（Hook，100字）**："所有 Agent 框架都聚焦一个问题：'LLM 能做什么？'Janus 聚焦另一个问题：'人类怎么管人类？'"
     - **第2段（痛点，200字）**：描述现在 Agent 框架的问题——"你用 LangGraph 画了一个复杂的图，跑一次发现 LLM 跳过审查直接输出。你用 AutoGen 开了四个 Agent 让他们自由对话，最后发现谁也没对输出负责。你用 CrewAI 分配了角色，但角色之间没有硬边界——CEO Agent 也在调 API。"
     - **第3段（Janus 怎么做，400字）**："四角色，每个角色有硬边界——Gatekeeper（军师）零工具，Planner（参谋）零工具，Worker（士兵）有9个工具但只在授权范围内用，Reviewer（督察）零工具但可以要求重做。"
     - **第4段（五级审查，200字）**：展示 APPROVED / APPROVED_WITH_NOTES / MINOR_REVISIONS / MAJOR_REVISIONS / REJECTED，以及对应的处理逻辑。
     - **第5段（技术细节，200字）**：指挥官意图（intent 字段全链路贯穿）、上下文纪律（每层只看该看的）。
     - **第6段（结尾，100字）**："GitHub：[链接]。白皮书：[链接]。大家觉得这种设计思路怎么样？欢迎指教。"
   - **附件**：终端运行截图 1 张

2. **发布时间选择**：早上 10:00-11:00（V2EX 上班摸鱼高峰期）

3. **冷启动策略**：发布后 30 分钟内，用小号或请朋友在楼下回复 2-3 条技术提问（例如"审查机制会不会大幅增加 Token 消耗？"、"四个角色都是同一套 LLM 吗？"），你本人立即回复，形成讨论氛围

---

##### 步骤 1.3 — 发布掘金文章（D3 上午）

**具体动作**：
1. 打开掘金，点击"发表文章"，使用以下模板：
   - **标题**："手写一个带四角色管理的 Agent 框架——附完整代码示例"
   - **正文结构**：
     - **开头（200字）**：直接上代码片段——展示一个 Gatekeeper 如何将用户请求转化为 Directive
     - **第2段（核心代码展示，800字）**：贴 4 个关键代码片段——Gatekeeper._formulate_directive()、Planner._plan()、Worker._execute_loop()、Reviewer.review()。每个片段 100-150 行代码，加注释说明。
     - **第3段（运行效果，500字）**：贴完整运行日志（从用户输入到最终输出），标注每个阶段对应哪个角色。
     - **第4段（设计决策解释，500字）**：为什么 Gatekeeper 不碰工具？为什么审查有 5 级不是 3 级？为什么 Worker 可以自分解？每个决策给一个理由。
     - **第5段（结尾，200字）**："完整的 3800+ 行代码在 GitHub：[链接]。欢迎 Star、Fork、提 Issue。"
   - **标签**：#AI #Agent #开源 #Python #LLM
   - **文中嵌入**：代码块不超过 50 行/块，超出部分用折叠或链接到 GitHub 具体文件

---

##### 步骤 1.4 — 社区互动与回应（D1-D7 每天执行）

**具体动作**：
1. **定时检查**：每天早 10:00 和晚 20:00 检查 V2EX、知乎、掘金三个平台的评论区
2. **评论分类回应**：
   - **质疑"又一个 Agent 框架"**：使用预制回应（见下文）
   - **技术细节提问**：回复中附上 GitHub 具体文件的链接（如 "这个问题在 core/reviewer.py 第 45-80 行的 _assess_defect_severity 方法中有详细实现"）
   - **正面反馈**：回复"谢谢关注，欢迎提 Issue/PR"，附上 CONTRIBUTING.md 链接
   - **负面/挑衅评论**：不辩论。回复"感谢你的看法，Janus 的设计思路确实和主流框架不同，详细分析在[白皮书链接]第 X 章"
   - **所有回复原则**：24 小时内回复，回复长度不超过原始评论的 1.5 倍

3. **预制三段式回应模板**（复制粘贴，每次微调）：
   > "不是又一个框架，是另一种设计哲学。现有框架都在回答'LLM 能做什么'——Janus 回答的是：'人类几千年来怎么管人？'答案藏在军事指挥链、学术 peer review、制造业质检。把这三样映射到 Agent 架构，就得到了 Gatekeeper Tree + 四专家并行巡检 + 意图回环校验。"

---

##### 步骤 1.5 — 收集反馈，发布 follow-up 文章（D10）

**具体动作**：
1. 汇总前 7 天收到的所有评论，分类整理：
   - 最多人问的问题 TOP 3
   - 最尖锐的批评 TOP 2
   - 最容易被误解的点 TOP 2
2. 在知乎发第二篇专栏，标题："关于 Janus 的七个问题和回答"（参考评论区整理的内容）
3. 将 follow-up 链接更新到第一篇专栏的评论区置顶

---

#### 渠道 2：B站 10-15 分钟架构讲解视频（W2 完成）

**核心策略**：AI 架构类内容在 B站稀缺，视频生命周期 3-6 个月。学生群体可通过视频精准触达。关键差异化：不是 API 教学视频，是"架构设计哲学"视频——B站还很少这类内容。

---

##### 步骤 2.1 — 撰写视频脚本（D6 完成）

**具体动作**：
1. 打开一个文档，按以下分镜写脚本：

| 时间 | 画面 | 旁白 | 备注 |
|------|------|------|------|
| 0:00-0:10 | 黑屏弹出故障代码，伴随报错音效 | "你让 AI 写一个微博爬虫——它写了，但只抓了标题。" | 反差抓住注意力 |
| 0:10-0:30 | 屏幕切到终端，展示反复修改仍失败 | "你让它改，它改了一版，这次连运行都报错。" | 展示痛点 |
| 0:30-1:30 | 转场到四象限图（军/政/法/工） | "Janus 的灵感不是来自 LLM 论文——来自管理学。" | 核心 Hook |
| 1:30-3:00 | 动画展示四角色信息流 | "Gatekeeper 像司令，不下战场...Planner 像参谋，不亲自冲锋..." | 逐个角色讲解 |
| 3:00-4:30 | 终端录屏：一个完整的任务演示 | "让我们创建一个 Python 项目..." | 实操演示 |
| 4:30-5:30 | 展示五级审查输出 | "看这里，Reviewer 给了 MAJOR_REVISIONS..." | 独特机制展示 |
| 5:30-7:00 | 对比表格（Janus vs LangGraph vs AutoGen vs CrewAI） | "不是谁更好，是出发点不同..." | 理性对比 |
| 7:00-8:00 | 回到代码仓库，展示测试文件和文档 | "3800 行核心代码，122 个测试用例..." | 技术实力展示 |
| 8:00-9:00 | 结尾：GitHub 地址 + 白皮书 | "代码在这里，欢迎来提 Issue。" | CTA |

2. 脚本总字数控制在 2500-3000 字（按 150 字/分钟语速，对应 15-18 分钟）
3. 准备 3-5 张关键帧截图/动画素材（可以用 PowerPoint 画）

---

##### 步骤 2.2 — 录制视频（W2-D1）

**具体动作**：
1. 打开 OBS Studio（免费录屏软件）
2. 设置：录制区域 1920x1080，帧率 30fps，音频 44100Hz
3. 准备演示环境：
   - 关闭无关窗口
   - 调整终端字体大小为 20px+（观众要能看清代码）
   - 终端主题选深色背景（黑色/深蓝），高对比度
4. 录制流程：
   - 先录旁白（仅音频，用麦克风），对着脚本念，允许 2-3 次重录
   - 再录屏幕操作（终端演示），旁白在后剪辑时叠加
   - 录完一个片段就保存，不要一次录完整——分 5-6 段录更方便剪辑

---

##### 步骤 2.3 — 剪辑（W2-D2 至 W2-D3）

**具体动作**：
1. 使用剪映专业版（免费）
2. 剪辑步骤：
   - 导入旁白音频 → 删除明显口吃和停顿（保留自然停顿）
   - 导入屏幕录制素材 → 对齐旁白时间线
   - 在关键帧添加文字标注（如"Gatekeeper 零工具"、"指挥官意图传递"）
   - 添加背景音乐（选轻量的 Lo-fi / 电子风格，音量 -20db 不覆盖人声）
   - 在 0-10 秒加入"高能警告"或"先赞后看"引导（B站惯例）
   - 添加进度条分段标签（00:00 问题引入 / 01:30 核心哲学 / 03:00 架构讲解 / ...）
3. 导出设置：H.264 编码，1080p，60fps，码率 8Mbps

---

##### 步骤 2.4 — 发布视频（W2-D4）

**具体动作**：
1. 打开 B站，点击"投稿"
2. 填写信息：
   - **标题**："所有 Agent 框架都在卷 Scaling Law，只有我在卷管理学"（不超过 30 字）
   - **封面**：用一张截图的四象限架构图，加粗体大字标题："AI Agent × 管理学"
   - **简介**（100-150 字）：
     > "Janus 是一个用人类管理智慧设计的 AI Agent 框架。四角色（Gatekeeper/Planner/Worker/Reviewer）硬边界，五级审查体系，指挥官意图全链路传递。不是又一个 LLM 调 API 的教程——是架构设计哲学的探讨。"
     > "GitHub：[链接] | 白皮书：[链接]"
   - **分区**：科技 → 计算机技术
   - **标签**：#AI框架 #Agent #编程 #开源 #LLM
3. **发布时间**：周六 19:00-21:00（B站学生用户高峰时段）
4. **发布后操作**：
   - 视频评论区置顶："欢迎 Star 和 Fork，也欢迎在评论区讨论架构设计"
   - 前 3 天主动回复每一条评论（即使只有表情）
   - 将视频链接发到知乎专栏更新中、V2EX 新帖（"做了个视频版讲解"）

---

##### 步骤 2.5 — 视频扩散（发布后 7 天）

**具体动作**：
1. 发布后第 2 天：在知乎回答"有什么冷门的开源项目值得关注？"——回答中写 200 字介绍 Janus，附 B站视频链接
2. 发布后第 4 天：在即刻发一条动态："做了一期视频讲我的 Agent 框架，从管理学角度切入，B站链接：[链接]"
3. 发布后第 7 天：检查视频数据（播放量、点赞率、完播率），如果 >5% 点赞率，追加一条 Twitter 推荐

---

#### 渠道 3：学术发表 — ChinaXiv + arXiv 双线推进

**核心策略**：学术发表的证据价值 > 流量价值。ChinaXiv（中科院）对国企路线是"主菜"，arXiv 对国际社区是"入场券"。两条线并行推进，ChinaXiv 可 24 小时内完成，arXiv 需要 1-4 周。

---

##### 步骤 3.1 — 编译 ChinaXiv 中文精简版（D1 完成）

**具体动作**：
1. 打开 `paper/janus_whitepaper.pdf`（19 页英文版）
2. 提取核心内容编译为 3-5 页中文版，结构：

| 章节 | 内容 | 字数 |
|------|------|------|
| 1 引言 | 问题背景：Agent 架构的"管理失控"问题 | 400字 |
| 2 相关研究 | LangGraph/AutoGen/CrewAI 的不足 | 300字 |
| 3 方法论 | Gatekeeper Tree 四角色体系 + 硬边界 | 600字 |
| 4 核心机制 | 五级审查、指挥官意图、自愈恢复 | 500字 |
| 5 实现 | 架构图 + 关键代码路径 | 400字 |
| 6 验证 | 测试覆盖 + 审计结果 | 300字 |
| 7 结论与展望 | 未来工作 + 开源地址 | 200字 |

3. 编译完成后导出为 PDF
4. 打开 ChinaXiv 网站（chinaxiv.org），注册账号，选择"计算机科学"类目，上传 PDF
5. 填写作者信息（太原理工大学 + 姓名），提交
6. **提交后 24 小时内获取 ChinaXiv ID**，记录到 README 中

---

##### 步骤 3.2 — 寻找 arXiv endorser（D2 开始，持续 W1-W4）

**具体动作**：
1. **Day 2**：在 Hugging Face 论坛发帖：
   - 标题："Looking for arXiv endorsement (cs.AI) — multi-role agent framework based on management science"
   - 正文：简述 Janus 的核心理念，附 GitHub 链接和白皮书链接
   
2. **Day 2 同步**：在 Reddit r/MachineLearning 发帖（同样内容）

3. **Week 1 内**：联系太原理工大学计算机/AI 方向导师：
   - 打开学校官网，找到软件学院/计算机学院教师列表
   - 筛选研究方向偏工程实践、了解 Agent 方向的副教授以上教师
   - 发送微信/邮件，话术：
     > "老师您好，我是软件工程专业的学生，独立做了一个 AI Agent 框架 Janus。已经有完整代码（3800+ 行，开源）、19 页白皮书、122 个测试用例。现在想投 arXiv，但需要 endorsement。如果您有兴趣合作（您做第二作者），我可以把 arXiv 提交版整理好发您审阅。"
   - 如果导师同意：将导师加为 co-author，利用其 arXiv 历史直接提交
   - 如果导师不同意：继续通过 Hugging Face / Reddit 渠道找陌生人 endorser（需要 3 个不同领域的 endorser）

4. **Week 4 前**：完成 arXiv 提交，拿到 arXiv ID，更新 README

---

##### 步骤 3.3 — 准备 arXiv 提交版（W1-W3）

**具体动作**：
1. 基于英文白皮书 PDF，调整格式适配 arXiv 要求：
   - 使用 LaTeX 模板（arXiv 标准）
   - 添加 abstract（150 字以内）
   - 添加参考文献格式（bibtex）
   - 确保所有图片是 PDF/EPS 格式
2. 在 `paper/` 目录下创建 `arxiv_submission/` 子目录，存放所有源文件（.tex + .bib + 图片）
3. 本地编译确认没有错误，arXiv 提交后也会自动编译一次

---

##### 步骤 3.4 — 比赛材料准备（8 月底前）

**具体动作**：
1. 搜索"挑战杯 2026 太原理工大学 报名时间"——联系校团委确认校赛时间
2. 准备申报书初稿，包括：
   - 项目摘要（300字）
   - 研究背景与意义（800字）
   - 方法论（1200字）
   - 创新点（500字）
   - 应用前景（500字）
3. 申报书可直接使用白皮书内容 + ChinaXiv 论文
4. 准备好指导教师签字（通过步骤 3.2 建立的导师关系）

---

#### 渠道 4（额外）：Twitter/X 内容矩阵 — 每周 2-3 条 Thread

**核心策略**：建立持续输出节奏，积累社交时间戳，覆盖英文社区。Twitter 时间戳不可篡改，每周 1 条远比"做完才发"有力——它展示了思想演化过程。

---

##### 步骤 4.1 — 第一天 Thread（D5 发布）

**具体动作**：
1. 打开 Twitter/X，点击"发帖"
2. 按以下内容逐条发布（共 8 条）：

| 序号 | 内容（英文） | 中文对照 |
|------|-------------|---------|
| 1/8 | "Most AI agent frameworks ask: 'What can LLMs do?' Janus asks a different question: 'How have humans managed humans for 3000 years?'" | "大多数 AI Agent 框架问：LLM 能做什么？Janus 问：3000 年来人类怎么管人？" |
| 2/8 | "The answer: military command chains (orders flow down, intent intact), academic peer review (independent audit, 5-level verdicts), and factory quality control (defect severity grading)." | "答案藏在军事指挥链、学术同行评审和工厂质检中。" |
| 3/8 | "Janus has 4 roles with HARD boundaries. Gatekeeper=general (zero tools). Planner=staff (zero tools). Worker=soldier (has tools). Reviewer=inspector general (zero tools, independent)." | "Janus 有四个角色，硬边界。Gatekeeper=将军（零工具）..." |
| 4/8 | "The 5-level review system: APPROVED → APPROVED_WITH_NOTES → MINOR_REVISIONS → MAJOR_REVISIONS → REJECTED. Borrowed from academic peer review, not binary pass/fail." | "五级审查系统：源自学术同行评审，不是二元制。" |
| 5/8 | "Commander's Intent: every Worker gets not just WHAT to do, but WHY. The 'intent' field flows through all 4 roles untouched. No information loss." | "指挥官意图：每个 Worker 不仅知道做什么，更知道为什么。" |
| 6/8 | "Self-healing recovery: failure → diagnose root cause → formulate strategy → re-execute with injected feedback. Not a simple retry." | "自愈恢复：失败→诊断根因→制定策略→重新执行，注入反馈。" |
| 7/8 | "Context discipline: Gatekeeper never sees tool call logs. Reviewer never sees strategic intent. Each layer sees only what it needs." | "上下文纪律：每一层只看它该看的。" |
| 8/8 | "3800+ lines of Python. 122 test cases. 19-page whitepaper. Open source. GitHub: [link]. The whitepaper: [link]. What do you think?" | "3800+ 行 Python，122 个测试用例，19 页白皮书。你怎么看？" |

3. 每条 thread 间隔 3-5 分钟发布（不要一次全发，会被算法判定为 spam）
4. 发布后，在第一条 thread 下回复一个"🧵"表示 thread 结束

---

##### 步骤 4.2 — 每周内容规划（持续 3 个月）

**每周发布计划**：

| 周次 | 内容类型 | 主题 | 发布日 |
|------|---------|------|--------|
| W1 | Thread（8条） | Janus 核心哲学介绍 | 周三 |
| W2 | 单条推文 | 一张架构图 + 一句话 | 周二 |
| W2 | Thread（5条） | 开发过程记录：为什么选 Python + 协程 | 周四 |
| W3 | 单条推文 | 运行日志截图对比（Janus vs 普通 Agent） | 周一 |
| W3 | Thread（6条） | 五级审查机制的实现细节 | 周四 |
| W4 | 单条推文 | 白皮书封面 + 一句话推荐 | 周三 |
| W4 | Thread（7条） | 与 LangGraph 的设计哲学对比 | 周五 |
| W5 | 单条推文 | Benchmark 结果（如已创建） | 周二 |
| W5 | Thread（6条） | Worker 的自分解机制 | 周四 |
| W6+ | 持续 | 每周至少 2 条内容 | 继续 |

---

##### 步骤 4.3 — 互动策略（每天 10 分钟）

**具体动作**：
1. 每天搜索 "AI agent"、"LangGraph"、"AutoGen"、"CrewAI" 相关推文
2. 在别人的推文下回复，不要推销，只做技术讨论。例如：
   - 某人发 LangGraph 教程 → 回复："Interesting approach! Janus takes a different angle — instead of graph flow, it uses management hierarchy. [链接到具体比较]"
   - 某人抱怨 Agent 不可靠 → 回复："Are you seeing the 'intent loss' problem? Janus has a mechanism for that: the intent field is immutable across all 4 roles. [链接]"
3. 不要发私信推销——在公开讨论中自然被看到

---

### 1.4 优先级排序与时间线总览

```
时间        行动项                          渠道        优先级    预期产出
──────────────────────────────────────────────────────────────────────────
D1(上午)    发布知乎专栏                     渠道1       P0        专栏文章上线
D1(当天)    编译 ChinaXiv 精简版并提交       渠道3       P0        ChinaXiv ID (24h)
D2(上午)    发布 V2EX 帖子 + 评论互动        渠道1       P0        帖子 + 讨论
D2(当天)    HuggingFace/Reddit 找 endorser   渠道3       P0        帖子 2 个
D3(上午)    发布掘金文章                     渠道1       P1        文章上线
D5          第一条 Twitter Thread            渠道4       P1        8 条 thread
D6          写 B站视频脚本                  渠道2       P1        脚本终稿
D7          联系导师 (arXiv co-author)       渠道3       P0        微信/邮件
W2-D1       录制 B站视频素材                渠道2       P1        原始素材
W2-D2~D3    剪辑 B站视频                    渠道2       P1        成品视频
W2-D4       发布 B站视频                    渠道2       P1        视频上线
W3          投稿技术周刊                    渠道1       P2        投稿邮件
W4          提交 arXiv                      渠道3       P0        arXiv ID
W4 之后     Twitter 持续输出                渠道4       P2        每周 2 条
8月底前     比赛申报书初稿                  渠道3       P2        申报书
```

---

### 1.5 推广前确认清单

| 项目 | 状态 | 行动 |
|------|------|------|
| GitHub 仓库公开 | ✅ 已完成 | — |
| Commit 历史（8 commits） | ✅ 清晰可追溯 | 不要 squash，保留历史 |
| OpenTimestamps 存证 | ✅ PDF + SHA256 | — |
| 白皮书 PDF 终稿 | ✅ 19 页 | — |
| README 哲学优先 | ✅ 已完成 | — |
| LICENSE（MIT） | ✅ | — |
| CONTRIBUTING.md | ❓ 需确认 | 检查是否存在，如无则创建（含分支策略、PR 流程、Issue 模板） |
| ChinaXiv 精简版 | ❌ 未创建 | **立即做（D1）** |
| arXiv 提交版 | ❌ 未创建 | Week 1-4 |
| 知乎/V2EX/掘金文章 | ❌ 未创建 | Week 1 |
| B站视频 | ❌ 未创建 | Week 2 |
| 比赛申报书初稿 | ❌ 未创建 | 8月底前 |
| 两个占位符工具（web_search, browser_navigate） | ⚠️ 占位符 | 影响演示和 Demo，建议优先补齐 |

---

## 二、证据链评估

### 2.1 已有证据清单（全量盘点）

| # | 证据项 | 类型 | 可信度 | 位置 |
|---|-------|------|--------|------|
| E1 | GitHub 公开仓库 + 8 commits | 代码 + 时间戳 | ★★★★★ | github.com/isheng-eqi/janus |
| E2 | OpenTimestamps 对 PDF 的区块链存证 | 不可篡改时间戳 | ★★★★★ | paper/ 目录 |
| E3 | SHA256 哈希记录 | 防篡改校验 | ★★★★★ | paper/janus_whitepaper.sha256.json |
| E4 | 19 页英文技术白皮书 PDF | 技术文档 | ★★★★☆ | paper/janus_whitepaper.pdf |
| E5 | 中文 HTML 版白皮书 | 技术文档 | ★★★★☆ | paper/janus_whitepaper_zh.html |
| E6 | 设计哲学文档（design-philosophy.md） | 方法论阐述 | ★★★★☆ | docs/ |
| E7 | 人类管理模式映射文档（human-management-patterns.md） | 理论支撑 | ★★★★☆ | docs/ |
| E8 | 完整信息流追踪文档（janus_information_flow_trace.md） | 技术验证 | ★★★★☆ | 根目录 |
| E9 | Hermes Agent 双轨验证报告（final-verdict.md） | 独立审计 | ★★★★☆ | docs/ |
| E10 | 交叉验证结果（validation_result.json 覆盖 9 个模块） | 自动化验证 | ★★★★☆ | check/ |
| E11 | 9 个测试文件（122 个单元测试用例） | 代码质量 | ★★★★☆ | tests/ |
| E12 | GitHub description 优化 | 元数据 | ★★★☆☆ | GitHub |
| E13 | 推广策略文档（promotion-strategy.md） | 计划证据 | ★★★☆☆ | docs/ |

### 2.2 强证据项（2+ 项）

#### ✅ 强证据 1：完整源代码 + 9 个测试文件 + 独立审计报告

**证据内容**：
- 3,800+ 行核心 Python 代码（core/ 目录 10 个源文件）
- 9 个测试文件，122 个单元测试用例（test_protocol.py 等）
- 生产就绪性最终裁决（final-verdict.md）：17/17 已知 issue 全部修复，20/20 数据交接点验证通过

**为什么强**：
1. **可复现**：任何人可以 clone 仓库、运行测试、验证代码行为
2. **独立验证**：final-verdict.md 由 Hermes Agent 双轨独立审计（自上而下 + 自底向上），不是自说自话
3. **量化指标**：17 个 issue 的分析、20 个交接点的检查、15 个剩余缺口的分类——不是笼统的"做好了"
4. **测试覆盖全面**：包含协议层、任务管理器、控制台、审查器、边缘情况、模糊测试、压力测试、集成测试

```
具体数字：
- core/gatekeeper.py ~670 行
- core/planner.py ~850 行  
- core/worker.py ~1100 行
- core/reviewer.py ~300 行
- core/task_manager.py ~220 行
- core/protocol.py ~170 行
- core/console.py ~310 行
- 测试文件 9 个
```

**局限**：
- 测试是自写的，不是第三方测试
- 缺少集成测试对真实 LLM API 调用的端到端验证（测试使用 mock）

---

#### ✅ 强证据 2：OpenTimestamps 区块链存证 + SHA256 + Git commit 历史

**证据内容**：
- OpenTimestamps 对白皮书 PDF 的区块链存证（不可篡改时间戳）
- SHA256 哈希记录（防篡改校验）
- 8 个 Git commit，提交历史清晰可追溯

**为什么强**：
1. **三重独立时间戳**：GitHub（Git 时间戳）+ OpenTimestamps（区块链时间戳）+ SHA256（内容哈希），三者交叉验证
2. **防抵赖**：任何人无法声称"我先做的"，区块链时间戳的证明力在法庭级别
3. **可验证**：任何第三方可以独立验证 OTS 戳的有效性

```
验证命令示例：
ots verify paper/janus_whitepaper.pdf.ots
sha256sum paper/janus_whitepaper.pdf
git log --oneline
```

**局限**：
- 只有白皮书 PDF 做了 OTS，代码本身没有逐文件 OTS
- Git 历史只有 8 个 commit，粒度较粗（建议每个重要功能独立 commit）

---

### 2.3 弱/缺失证据项（2+ 项）

#### ❌ 弱证据 1：无任何量化基准测试（Benchmark）

**证据缺失**：项目中没有对 Janus 与 LangGraph、AutoGen、CrewAI 等框架的定量对比。

**具体问题**：
- 没有"相同任务、不同框架"的完成率对比数据
- 没有 Token 消耗、调用次数、执行延迟等效率指标
- 没有成功率、失败率、重试率的统计
- 五级审查机制的有效性没有量化评估（相比二元 pass/fail 提升了多少）

**严重程度**：🔴 严重缺失

**为什么重要**：
- 推广时被问到"凭什么说 Janus 比 XXX 好？"时，只能讲设计哲学，拿不出数据
- HN/Reddit 社区对"self-reported metrics"天然不信任，但没有 metric 更糟
- 学术论文要求实验对比，没有 benchmark 无法通过审稿

**建议弥补**（见 §3.1-§3.4）

---

#### ❌ 弱证据 2：无任何外部用户/第三方采纳证据

**证据缺失**：零外部用户、零第三方贡献、零实际部署案例。

**具体问题**：
- GitHub 仓库没有 Issue（除自己创建的）、没有 PR、没有 Fork（除自己）
- 无任何外部博客/推文/文章引用 Janus
- 无任何企业或项目实际使用 Janus 的证据
- 白皮书中的"对比分析"（README 中 LangGraph/AutoGen/CrewAI 对比表）是自述而非第三方评估

**严重程度**：🟡 中等缺失

**为什么重要**：
- "没人用"是开源项目推广的最大阻力——社交 proof 缺失
- "大二学生"身份天然面临信任门槛，外界更容易质疑"这个框架靠谱吗"
- 所有证据都是自己生产的（self-attested），没有外部背书

**建议弥补**（见 §3.5-§3.7）

---

#### ❌ 弱证据 3（补充）：两个核心工具是占位符

**证据缺失**：web_search 和 browser_navigate 是占位符实现。

```python
# worker.py ~842
def _real_web_search(query: str) -> str:
    return "⚠️ web_search is a placeholder — needs external API integration."
```

**具体问题**：
- 缺少网络搜索能力严重限制 Worker 的实际功用
- 无法演示"信息检索 → 分析 → 产出"的完整工作流
- 现有对比表（README）中列出 9 个工具，但实际只有 7 个可用

**严重程度**：🟡 中等缺失

---

#### ❌ 弱证据 4（补充）：无 arXiv/ChinaXiv 正式出版记录

**证据缺失**：截至分析日，白皮书未在任何预印本平台上线。

**具体问题**：
- arXiv 需要 endorsement，尚未完成
- ChinaXiv 尚未提交
- 无 DOI、无正式引用标识
- 学术场景下无法引用 Janus

**严重程度**：🟡 中等（但时间敏感）

---

### 2.4 总体证据充分性判断

| 维度 | 评级 | 说明 |
|------|------|------|
| **代码完整性** | 🟢 充分 | 代码可运行、测试通过、审计报告确认生产就绪 |
| **时间戳保护** | 🟢 充分 | OTS + SHA256 + Git 三重保护 |
| **文档完整度** | 🟢 充分 | 白皮书、设计哲学、管理模式映射、信息流追踪、推广策略一应俱全 |
| **独立验证** | 🟡 部分充分 | Hermes Agent 审计有一定独立性，但仍是项目内部的工具 |
| **外部证据** | 🔴 不充分 | 零外部用户、零第三方引用、零 benchmark 数据 |
| **学术背书** | 🔴 不充分 | 无 arXiv/ChinaXiv/会议论文 |
| **量化对比** | 🔴 不充分 | 无任何框架对比的量化数据 |

**总体判断**：证据链在"我们自己做了什么"方面非常充分（代码、文档、时间戳、内部审计），但在"外界如何评价"方面严重不足（无外部用户、无 benchmark、无学术发表）。**推广前的当务之急不是继续写代码，而是补齐外部证据——特别是 benchmark 和 ChinaXiv。**

---

## 三、可执行的证据补充建议

### 3.1 创建基准测试套件（Benchmark）【P0 - 最重要】

创建一个 `benchmark/` 目录，包含标准的 Agent 任务集（10-20 个任务），覆盖：
- 代码生成（写函数、排序算法、API 客户端）
- 文件操作（创建文件、修改文件、多文件项目）
- 信息检索（读取文件、搜索文件、组合结果）
- 多步骤任务（先 A 后 B 再 C）

对每个任务记录：
- 成功率（Janus vs 手动执行）
- 所需 LLM 调用次数
- 总 Token 消耗
- Review 通过率
- 平均耗时

**产出**：`benchmark/results.md`，含表格和可视化图表。

**为什么这是 P0**：这是应对"凭什么说 Janus 好"的唯一量化武器。

### 3.2 跨框架对比测试【P1】

选取 3 个典型任务，用 Janus、LangGraph（简单实现）、AutoGen（简单实现）分别执行，记录相同指标。

**注意**：不要在推广中说"Janus 比 XXX 好 X%"——只要数据透明（成功率、成本、时间），读者自己会判断。过度 claim 会触发社区反噬。

**产出**：`benchmark/comparison.md` 或一篇知乎对比文章。

### 3.3 撰写 Demo 场景案例【P1】

选择 3 个能突出 Janus 优势的真实场景：

| 场景 | 为什么选它 | 预期展示效果 |
|------|-----------|-------------|
| 创建多文件 Python 项目（含测试） | 展示分解+执行+审查全流程 | 5 分钟内完成一个完整小项目 |
| 修复现有代码中的 bug | 展示 Reviewer 发现缺陷 + Worker 修复的闭环 | Reviewer 指出遗漏的边界情况 |
| 将自然语言需求转化为配置文件 | 展示 Gatekeeper 意图理解 + Planner 分解 | 输入模糊需求，输出结构化配置 |

每个场景录制成 **2 分钟 Demo 视频**（纯终端录屏 + 旁白），放在 GitHub 和 B站。

**产出**：`demos/` 目录 + 3 个视频。

### 3.4 补充端到端集成测试【P1】

当前测试使用 mock，缺少对真实 LLM API 的端到端测试。补充 3-5 个端到端测试用例：
- 使用固定的 system prompt 模拟 LLM 行为（不用真 API 调用，避免费用）
- 或使用本地小模型（如 Ollama 上的 Llama 3）做低成本 E2E 验证

**产出**：`tests/test_e2e.py`

### 3.5 创建 GitHub Issues 模板和贡献指南【P1】

- `CONTRIBUTING.md`：清晰的贡献流程（fork → branch → commit → PR → review → merge）
- `ISSUE_TEMPLATE/`：bug report + feature request 模板
- GitHub Discussions 开启：作为社区讨论空间

**目的**：降低外部贡献门槛，表明项目"欢迎参与"。

### 3.6 在技术社区主动寻求外部反馈【P2】

发布内容的帖子后，主动邀请：
- "欢迎 fork 和尝试，任何 Issue 和 PR 我都会认真回复"
- 在知乎/V2EX 评论区认真回复每一个质疑——质疑本身就是互动，互动就是曝光

### 3.7 补齐两个占位符工具【P2】

- `web_search`：集成一个免费的搜索 API（如 SerpAPI 免费层 100 次/月，或 DuckDuckGo 免费）
- `browser_navigate`：集成 Playwright 实现基础页面抓取

**为什么不是 P0**：对"证明 Janus 理念"而言，这个不是关键路径；但对"Demo 演示"和"实际使用"影响较大。

### 3.8 行动优先级矩阵

```
紧急    重要    行动项
──────────────────────────────
高      高      3.1 创建 Benchmark（P0）
高      高      3.3 ChinaXiv 提交（P0）
高      中      3.5 创建 CONTRIBUTING.md + Issue 模板（P1）
中      高      3.2 跨框架对比测试（P1）
中      高      3.4 端到端集成测试（P1）
中      中      3.3 Demo 视频录制（P1）
中      中      3.7 补齐占位符工具（P2）
低      中      3.6 社交媒体寻求反馈（P2）
```

---

## 四、结论

**推广方案**：建议立即启动中文技术社区长文（渠道 1）和 ChinaXiv 提交（渠道 3）两条 P0 路径，一周内完成内容准备和提交；第二周启动 B站视频（渠道 2）；arXiv 在 1-4 周内通过 endorsement 或导师合作完成。每日互动、每周复盘，持续 3 个月。

**证据链评估**：内部证据充分（代码、文档、时间戳、审计报告），外部证据严重不足（无用户、无 benchmark、无学术发表）。**明确地说：当前证据链不足以支持大规模推广**——如果在证据链不完整的情况下强推（特别是 HN/Reddit），很可能被社区质疑"又是一个没人用的框架"。建议先花 1-2 周补齐基准测试和 ChinaXiv，再用这些证据支撑推广内容，这样每一篇帖子/文章/视频都能引用量化数据和学术背书，可信度大幅提升。

**一句话总结**：代码已经做好了，证据链的短板在"外界看不到我们的代码有多好"——补齐 benchmark 和 ChinaXiv 之后，推广的杠杆率至少翻倍。
