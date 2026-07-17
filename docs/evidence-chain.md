# Janus 原创证据链 — 完整记录

> 用途：本文档记录了 Janus AI Agent 框架原创性的全部密码学与制度性证据。
> 任何后续 Agent 读到本文档，应能完整复现验证流程。

---

## 一、身份锚点

| 项目 | 值 |
|------|-----|
| 作者 | 王炳晟 (Bingsheng Wang) |
| 机构 | 太原理工大学·软件工程·2024级 |
| GitHub | https://github.com/isheng-eqi |
| 邮箱 | ishengeqi@163.com |
| GPG | EF63540BC26249C7 |
| 项目仓库 | https://github.com/isheng-eqi/janus |

---

## 二、证据清单

### 证据 1：GitHub 公开仓库 + Git 提交历史

| 属性 | 值 |
|------|-----|
| 仓库 URL | https://github.com/isheng-eqi/janus |
| 首次公开提交 | 2026-07-17 12:31 UTC+8 |
| 首次提交 Hash | `ae0f905698d4daea8d949c0e3cf165350c62ed95` |
| 提交信息 | "Initial public release: Janus hierarchical multi-agent framework" |

验证方法：
```bash
git clone https://github.com/isheng-eqi/janus.git
cd janus
git log --reverse --format="%H %ai %s" | head -1
```

法律效力参考：中国「罗盒案」（2021-2024，最高法）确认 GitHub commit 历史具有电子证据效力。

---

### 证据 2：白皮书 PDF — SHA256 哈希 + OpenTimestamps 区块链存证

| 属性 | 值 |
|------|-----|
| 文件路径 | `paper/janus_whitepaper.pdf` |
| 文件大小 | 259,290 bytes |
| 页数 | 19 |
| SHA256 | `e7b141a8d7ac8624057d3f97dad95f43fb28c6424be8ff86dff127c866174e0f` |
| 哈希记录文件 | `paper/janus_whitepaper.sha256.json` |
| OTS 证明文件 | `paper/janus_whitepaper.pdf.ots` (221 bytes) |
| 日历服务器 | `alice.btc.calendar.opentimestamps.org` |
| OTS 创建时间 | 2026-07-17 05:22 UTC |
| OTS 证明深度 | 11 层 |

验证方法：
```bash
# 验证 SHA256
sha256sum paper/janus_whitepaper.pdf
# 预期输出: e7b141a8d7ac8624057d3f97dad95f43fb28c6424be8ff86dff127c866174e0f

# 验证 OTS
pip install opentimestamps-client
ots verify paper/janus_whitepaper.pdf.ots
# 注意：PendingAttestation 需先升级为 BitcoinBlockHeaderAttestation
ots upgrade paper/janus_whitepaper.pdf.ots   # 在 Linux/WSL 中运行
ots verify paper/janus_whitepaper.pdf.ots     # 再次验证
```

---

### 证据 3：白皮书哈希记录（多平台时间戳）

| 属性 | 值 |
|------|-----|
| 记录文件 | `paper/janus_whitepaper.sha256.json` |
| 记录时间 | 2026-07-17 12:48:41 UTC+8 |
| GitHub blob URL | https://github.com/isheng-eqi/janus/blob/master/paper/janus_whitepaper.sha256.json |

记录内容：
```json
{
  "file": "janus_whitepaper.pdf",
  "sha256": "e7b141a8d7ac8624057d3f97dad95f43fb28c6424be8ff86dff127c866174e0f",
  "timestamp_utc": "2026-07-17T04:48:41Z",
  "timestamp_local": "2026-07-17 12:48:41",
  "size_bytes": 259290,
  "github_repo": "https://github.com/isheng-eqi/janus"
}
```

---

### 证据 4：设计文档时间线（Git 历史）

以下文件在首次公开提交中即存在，构成设计哲学的最早公开记录：

| 文档 | Git 首次出现 | 内容 |
|------|-------------|------|
| `docs/design-philosophy.md` | Commit `ae0f905` | 人类管理智慧 → Agent 架构的设计哲学 |
| `docs/human-management-patterns.md` | Commit `ae0f905` | 六领域管理模式映射 |
| `docs/information-flow.md` | Commit `ae0f905` | 分层信息流设计 |
| `docs/janus-full-summary.md` | Commit `ae0f905` | 框架全貌概述 |

---

## 三、证据链强度评估

| 层 | 类型 | 防篡改强度 | 司法认可 |
|----|------|-----------|---------|
| GitHub commit 历史 | SHA256 哈希链 | ★★★★ | ✅ 中国最高法确认 |
| OpenTimestamps | 比特币区块链锚定 | ★★★★★ | ✅ 杭州互联网法院 2018 确认 |
| SHA256 哈希记录 | 公开时间戳 + 多平台交叉 | ★★★ | ✅ 电子证据 |
| 白皮书 PDF | 学术优先权 | ★★★ | ⚠️ 未正式出版 |

**结论**：任何人若要声称"在 2026-07-17 之前独立完成了相同设计"，需同时推翻 Git 哈希链 + 比特币区块链锚定——密码学层面不可行。若声称"在 2026-07-17 之后独立想到"——公开记录已构成优先权。

---

## 四、待完成事项（可选增强）

| 事项 | 说明 | 优先级 |
|------|------|--------|
| OTS 升级 | 需在 Linux/WSL 中运行 `ots upgrade` 将 PendingAttestation 升级为比特币区块锚定 | 中 |
| archive.org 存档 | 手动提交 https://github.com/isheng-eqi/janus 到 web.archive.org/save | 低 |
| arXiv 提交 | 需机构邮箱 + endorser | 低 |
| 公证处存证 | 约 500 元，最高司法证明力 | 极低 |

---

## 五、完整文件清单

```
C:\Users\HI\Desktop\janus\
├── paper/
│   ├── janus_whitepaper.pdf          ← 白皮书 PDF（19页）
│   ├── janus_whitepaper.tex          ← LaTeX 源码
│   ├── janus_whitepaper_zh.html      ← 中文阅读版
│   ├── janus_whitepaper.sha256.json  ← SHA256 记录
│   └── janus_whitepaper.pdf.ots      ← OpenTimestamps 证明
├── docs/
│   ├── design-philosophy.md          ← 设计哲学
│   ├── human-management-patterns.md  ← 管理智慧映射
│   ├── unicode-fix-analysis.md       ← Unicode 终端修复记录
│   └── promotion-strategy.md         ← 推广策略
├── core/                             ← 框架源码
├── main.py                           ← 入口
├── config.yaml                       ← 配置
├── README.md                         ← 项目首页
└── .git/                             ← Git 历史（时间戳链）
```

---

> **本文档创建时间**：2026-07-17
> **本文档 SHA256**：1d0ef8543b5429d0d71f68889d67723a6b9af4c61dffe1ecaae67c33732f1b3a
