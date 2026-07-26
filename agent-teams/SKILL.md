---
name: "Agent Teams 协同开发"
description: "基于 Claude Code Agent Teams 的多Agent并行开发工作流：分析→拆分→创建分支+任务清单→Agent Teams并行编码→ask-codex逐分支审查→合并→集成审查→修复"
alwaysAllow: ["Bash", "Write"]
---

# Agent Teams 协同开发 — 标准工作流

当用户要求进行多文件开发、项目搭建、功能开发、代码重构，或提到"多agent""并行""协同""拆任务""teams"时，按本流程执行。

**适用场景**：
- 🆕 **从 0 到 1 新建项目**：搭建目录结构、多模块并行开发
- ✨ **新功能开发**：跨多文件的新功能实现
- 🔧 **代码重构**：已有代码的结构调整和优化
- 📝 **批量修改**：配置迁移、API 替换、依赖升级等

**并行执行方式**：Claude Code Agent Teams（Team Lead 自动 spawn 队友，共享任务列表，同一目录并行）

---

## 总览：五个阶段

```
阶段一  需求分析与方案设计 → 生成【总计划.md】
阶段二  任务拆分 → 按强相关性分组 → 生成 N 组【分支任务清单.md】+ 创建 N 个 feature 分支
阶段三  Agent Teams 并行执行 → Team Lead spawn N 个队友 → 各队友在各自分支编码 → commit
阶段四  逐分支审查 → ask-codex 审查每个分支 → 如需修复 → fix/ 分支
阶段五  合并 + 集成审查 → integrate/ 分支 → ask-codex 审查合并结果 → 如需修复 → fix/ 分支
```

---

## 阶段一：需求分析与方案设计 → 总计划.md

在任何 Agent 动手之前，**先把所有决策做完**。Agent 擅长精确执行，不擅长在模糊中探索。

### 不同场景的分析侧重

| 场景 | 分析重点 |
|------|---------|
| **从 0 到 1** | 目录结构设计、模块划分、接口约定、数据流设计 |
| **新功能** | 现有代码阅读、插入点定位、接口设计、影响范围评估 |
| **重构** | 调用链分析、改动/不动范围、函数签名变更、向后兼容 |
| **批量修改** | 全量扫描、替换规则、边界情况、回归验证 |

### 执行步骤

1. **理解需求** — 明确要做什么、做成什么样、有什么约束
2. **分析现状** — 读代码 / 画架构图 / 画调用链（新项目则设计架构）
3. **确定范围** — 要创建/修改哪些文件，**不能碰**哪些文件
4. **做技术决策** — 目录结构、函数签名、数据结构、错误处理等全部确认
5. **识别风险** — 可能出错的地方和应对方案

### 产出模板：总计划.md

```markdown
# [项目名] 总计划

## 目标概述
一段话说清楚：为什么做、做成什么样、涉及哪些文件

## 现状分析
（调用链路图 / 架构图，建议用 Mermaid）

## 文件清单
| 文件 | 操作类型 | 归属队友 | 工作量 |
|------|---------|---------|--------|

## 不动文件清单（必须列出）
| 文件 | 不动原因 |
|------|---------|

## 技术决策记录
| 决策 | 结论 | 理由 |
|------|------|------|

## 分支策略
（分支全景图，Mermaid 或 ASCII）

## 任务文档索引
| 文档 | 分支 | 队友 | 内容 |
|------|------|------|------|
```

### 关键原则

- **"不动清单"比"改动清单"更重要** — 防止队友越界
- **"接口约定"比"内部实现"更重要** — 让各队友并行不冲突
- **决策全部前置** — 总计划中不应有"待定"项
- **架构图/调用链必须画** — 不画就拆任务 = 盲拆

---

## 阶段二：任务拆分 → 按强相关性分 N 组

### 分组原则

**Agent Teams 硬约束**：两个队友编辑同一文件会导致覆盖。**同一文件只能归属一个队友**。

#### 强相关（必须同一队友）

| 信号 | 示例 |
|------|------|
| 改/建同一个文件 | 创建类 + 添加方法 → 同一组 |
| 前后依赖且无接口隔离 | 写 util + 同文件调用 → 同一组 |
| 共享内部状态 | 改字段 + 改引用方法 → 同一组 |
| 必须原子提交 | 拆开会编译出错 → 同一组 |

#### 弱相关（可不同队友）

| 信号 | 示例 |
|------|------|
| 操作不同文件 | backend/ vs frontend/ → 不同组 |
| 仅通过接口关联 | A 导出函数，B import → 不同组 |
| 可独立验证 | 各自可单独 review → 不同组 |
| 属于不同层级 | 数据层 vs UI 层 → 不同组 |

#### 三问检验

```
1. 同一个文件是否只有一个队友改？  → 有重叠 → 合并
2. 每组任务量是否合适（5-6个）？   → 太少合并，太多拆分
3. 每个分支能否独立 review？       → 不能 → 重新划分
```

### 每个分支要做的

1. 创建 git 分支：`feature/描述性名称`
2. 创建任务清单文档：`docs/branch{N}-xxx-tasks.md`

### 产出模板：分支任务清单.md

```markdown
# 分支 N：分支名

> 范围：只改 xxx.js、yyy.js
> 基于：从哪个分支检出
> 目标：一句话
> 依赖：（如有）分支 M 的接口约定

---

## 任务清单

### Task N.1 — 动作标题
- **文件**：精确到文件名（新建 / 修改 + 行号范围）
- **动作**：一句话说清
- **变更内容**：（新建给完整内容 / 修改给 diff）
- **完成标准**：可验证条件

### Task N.2 — ...

---

## Review 检查清单

### 功能正确性
- [ ] 检查项

### 代码清洁度
- [ ] 旧代码残留（搜索关键词）

### 不动文件确认
- [ ] xxx.js 未被修改
```

### 产出模板：集成审查清单.md

```markdown
# 集成审查清单

## 1. 端到端数据流验证
| 场景 | 调用链 | 预期行为 |
|------|--------|---------|

## 2. 模块边界确认
- [ ] 导出接口与导入调用一致
- [ ] 无循环依赖

## 3. 不动文件确认
- [ ] xxx.js 未被修改

## 4. 回归/验收场景
- [ ] 场景 → 预期结果
```

---

## 阶段三：Agent Teams 并行执行

### 运行机制

使用 Claude Code Agent Teams。用户将 **Team Lead 提示词** 发给 Claude Code，Team Lead 自动 spawn 队友、分配任务、协调执行。

### Team Lead 提示词模板

阶段二完成后，生成 `docs/team-lead-prompt.md`，用户直接粘贴给 Claude Code。

```markdown
# [功能名称] — Agent Teams 并行开发

创建一个 agent team，包含 N 个队友并行开发。
对每个队友，require plan approval before they make any changes.

## 项目背景
[技术栈、目录结构、关键约定]

## 目标
[要实现什么功能]

## 前置准备（Team Lead 先执行）
以下 feature 分支已创建好：
- `feature/branch-1` — 队友A使用
- `feature/branch-2` — 队友B使用

## 团队分工

### 队友A — [职责]
- **分支**：`feature/branch-1`
- **任务清单**：`docs/branch1-xxx-tasks.md`
- **操作文件**：[精确列出]
- **禁止触碰**：除上述外的所有文件

Spawn 提示词：
> 你是队友A，负责[职责]。先 `git checkout feature/branch-1`，
> 然后严格按 `docs/branch1-xxx-tasks.md` 执行。
> 只操作指定文件。完成后 git add + commit，报告 hash。

### 队友B — [职责]
（同上格式）

## 依赖关系（如有）
- 队友B 的 Task B.3 depends_on 队友A 的 Task A.2
- 接口约定已写在各自任务清单中，可并行无需等待

## 全局规则
1. 禁止操作 main 分支
2. 禁止 push
3. 禁止操作范围外的文件
4. commit 前缀：feat: / refactor: / fix:
5. Co-Authored-By: Craft Agent <agents-noreply@craft.do>

## 完成条件
所有队友报告 commit hash 后，Team Lead 汇总，通知用户进入审查。
```

### 依赖链处理

- **方案 A：接口先行（推荐）** — 任务清单写明接口签名，并行无需等待
- **方案 B：任务依赖** — 标注 depends_on，Agent Teams 原生支持
- **方案 C：公共模块先行** — Team Lead 先完成公共模块再 spawn 队友

---

## 阶段四：逐分支 ask-codex 审查

### 流程

```
对每个 feature 分支：

  ask-codex 审查 → 通过？── 是 → ✅
                    │
                    否 → 生成 fix-plan-branchN.md
                         → 创建 fix/branchN-review-fixes 分支
                         → 修复 → commit
                         → 再次 ask-codex → 循环直到通过
```

### 审查命令

```
ask-codex "对照 docs/branchN-xxx-tasks.md 的 Review 检查清单，
逐项审查 feature/branch-N 分支的改动。
审查代码质量、接口一致性和模块边界。"
```

### 修复流程

```bash
git checkout feature/branch-1
git checkout -b fix/branch1-review-fixes
# 修复 → commit
git checkout feature/branch-1
git merge fix/branch1-review-fixes --no-ff -m "merge: 合入分支1审查修复"
```

### 产出：fix-plan.md

```markdown
# 分支 N 审查修复计划

## 审查结果
- ✅ 通过项: ...
- ❌ 未通过项:

## 问题 1：标题
- **位置**：文件:行号
- **现状**：当前代码
- **期望**：应该怎样
- **修复方式**：具体怎么改
```

---

## 阶段五：合并 + 集成审查

### 步骤 A：创建合并分支

```bash
git checkout feature/branch-1
git checkout -b integrate/项目名
git merge feature/branch-2 --no-ff -m "merge: 合入分支2"
git merge feature/branch-N --no-ff -m "merge: 合入分支N"
```

### 步骤 B：集成审查

```
ask-codex "对照 docs/integrate-review-tasks.md 审查合并结果。
重点：跨文件兼容、接口一致、端到端数据流。"
```

### 步骤 C：集成修复（如需）

```bash
git checkout integrate/项目名
git checkout -b fix/integrate-review-fixes
# 修复 → commit
git checkout integrate/项目名
git merge fix/integrate-review-fixes --no-ff -m "merge: 合入集成审查修复"
```

再次 ask-codex → 通过后 integrate 分支 = 最终交付。

---

## 完整分支生命周期图

```
当前分支 ──────────────────────────────────── (始终不动)
  │
  ├── feature/branch-1         ← 队友A（Agent Teams）
  │     └── fix/branch1-fixes  ← 审查修复（如需）
  │
  ├── feature/branch-2         ← 队友B（Agent Teams）
  │     └── fix/branch2-fixes  ← 审查修复（如需）
  │
  ├── feature/branch-N         ← 队友N
  │     └── fix/branchN-fixes
  │
  └── integrate/项目名          ← 合并所有 feature + fix
        └── fix/integrate-fixes ← 集成审查修复（如需）
```

---

## 文档体系

```
docs/
├── 总计划.md                     ← 阶段一：全局视角
├── branch1-xxx-tasks.md          ← 阶段二：队友A 施工图 + Review 清单
├── branch2-yyy-tasks.md          ← 阶段二：队友B 施工图 + Review 清单
├── integrate-review-tasks.md     ← 阶段二：合并后审查基准
├── team-lead-prompt.md           ← 阶段三：Team Lead 提示词
├── fix-plan-branch1.md           ← 阶段四（如需）
└── fix-plan-integrate.md         ← 阶段五（如需）
```

| 文档 | 谁写 | 谁读 |
|------|------|------|
| 总计划.md | 人/AI | 人（全局把控） |
| branchN-tasks.md | 人/AI | 队友（执行）+ codex（审查） |
| team-lead-prompt.md | AI | 用户 → Claude Code 启动 Agent Teams |
| integrate-review-tasks.md | 人/AI | codex（集成审查） |
| fix-plan-xxx.md | codex | 人/Agent（修复） |

---

## 核心原则

1. **决策前置，执行分离** — 人做判断，Agent 做执行
2. **一份文档两个用途** — 任务清单既是施工图，也是审查基准
3. **文件级隔离** — 同一文件只归属一个队友，消除冲突
4. **审查和修复分离** — codex 出 fix-plan，Agent 在 fix/ 分支修，可追踪可回滚
5. **穷举式禁令** — 具体行为禁止，不留解释空间

---

## 两轮 ask-codex 审查的区别

| | 逐分支审查（阶段四） | 集成审查（阶段五） |
|---|---|---|
| 审查对象 | 单分支改动 | 所有分支合并后 |
| 对照文档 | branchN-tasks.md Review 清单 | integrate-review-tasks.md |
| 重点 | 功能正确、接口一致、边界 | 跨文件兼容、全局一致、端到端 |
| 修复分支 | fix/branchN-review-fixes | fix/integrate-review-fixes |

---

## 与 multi-agent-dev 的区别

| 维度 | multi-agent-dev（旧） | agent-teams（本技能） |
|------|----------------------|---------------------|
| 并行方式 | Git Worktree 物理隔离 | Agent Teams 同一目录各自切分支 |
| 协调方式 | 人工分发 N 份提示词 | Team Lead 自动 spawn + 任务列表 |
| 提示词产出 | N 份 Agent 提示词 + 协调者手册 | 1 份 Team Lead 提示词 |
| 审查工具 | Codex CLI | ask-codex（MCP 集成） |
| 通信 | Agent 间完全隔离 | 队友间可直接 message |

---

## 适用边界

**✅ 适合**：多文件任务、模块间接口通信、有可验证标准
**❌ 不适合**：探索性开发、高度耦合同文件、单文件修复

---

## 快速上手清单

```
□ 1.  理解需求，分析现状
□ 2.  做技术决策，写 docs/总计划.md
□ 3.  按强相关性分 N 组，确保文件零交叉
□ 4.  每组创建 feature 分支 + docs/branchN-tasks.md（含 Review 清单）
□ 5.  写 docs/integrate-review-tasks.md
□ 6.  生成 docs/team-lead-prompt.md
□ 7.  用户粘贴给 Claude Code → Agent Teams 并行执行
□ 8.  每个分支完成后 ask-codex 逐分支审查
□ 9.  如需修复 → fix-plan.md → fix/ 分支 → 再审
□ 10. 创建 integrate/ 分支，合并所有通过的分支
□ 11. ask-codex 集成审查
□ 12. 如需修复 → fix/ 分支 → 合回 → 再审
□ 13. integrate/ 分支 = 最终交付
```
