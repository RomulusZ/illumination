# AGENTS.md

<!-- 常驻入口：只放每次都必须遵守的硬约束与导航，详细契约见 docs/agents.md。
     预算：≤ 150 行。易变内容、模块专属内容不要写在这里。 -->

## 1. 项目

**Illumination**：通用 Agent Harness 骨架。业务领域尚未确定，先固化工程基建：
六层包结构、分层 import 检查、锁定工具链、CI 质量门、可复现测试与文档契约。

当前可运行示例切片：`RunRequest → 规划服务 → LangGraph plan 节点 → artifact 落盘 → RunResult`。
执行器、审计账本、预算计量、HITL、沙箱、真实工具尚未实现；计划步骤标记为 `skipped`、
运行状态为 `partial` 是骨架的预期行为。以本仓库 `docs/` 为唯一事实源。

## 2. 命令

```bash
# 安装依赖：严格按 uv.lock
uv sync --frozen --group dev

# 静态检查
uv run ruff check . && uv run ruff format --check .
uv run mypy
uv run python lint/arch_checker.py

# 测试
uv run pytest

# 本地提交前检查
uv run pre-commit install
uv run pre-commit run --all-files

# 离线跑通示例
uv run illumination run --objective "验证骨架链路" --out out --stub
```

## 3. 架构不变量

- **唯一允许的 import 方向**：`Types → Config → Repo → Service → Runtime → UI`。
  反向 import 一律拒绝，由 `lint/arch_checker.py` 在 CI 阻断。
- **Service 只依赖 Ports**：不得 import `repo` 具体实现；外部能力经 `types/ports.py` 注入。
- **UI 不 import Repo**：Agent run 必须走 Runtime；同步查询/短命令可走 Service。
- **组合根唯一**：只有 `runtime/composition.py` 可以实例化具体适配器；禁止模块级全局客户端。
- **契约是 Pydantic 模型**：跨节点状态、请求/响应与结构化输出不得用裸 `dict` 传递关键字段。
- **状态只存指针**：图中只放 `{artifact_id, path, summary, sha256}`；正文与大批量数据落 artifact store。
- **判断与计算分离**：能用代码判定的写成代码；只有真正需要判断的才交给模型。
- **模型输出必须校验**：结构化输出一律经过 schema 与 Pydantic 校验，失败即分类报错。
- **失败要分类**：可重试、可修复、需重规划、需人工、不可恢复不得混为一谈。
- **审计只追加**：审计与事件账本 append-only；骨架阶段尚未实现，接入时不得改成覆写。
- **预算单写**：token、工具调用、墙钟与修复次数由 harness 写入，模型不可写。
- **并发合并显式**：并行状态字段必须声明 reducer；同 key 不同值抛错，禁止静默覆盖。
- **prompt injection 四层**：规划输入不含外部原始文本；外部内容包裹为数据块；
  工具参数由代码构造；出口校验指令性语句与外链。

## 4. 红线（违反即停）

- 不提交密钥、token、`.env`；生产环境缺少 `OPENAI_API_KEY` 时启动必须失败。
- 不让模型产生或改写确定性数值与计数。
- 不把失败伪装成成功；`partial`、`skipped`、`not_found` 等状态必须如实写入产物。
- 不静默丢弃输入或步骤；丢弃必须写入可审计的理由。
- 不把正文、完整 prompt、原始外部响应或 traceback 塞进 state、日志或 API 响应。
- 不删除或弱化测试、不跳过 hook 来“修绿”。
- 不改公开契约而不更新测试与文档；破坏性契约变更必须写 ADR。
- 不确定时停下来提问或写 ADR，不要猜。

## 5. 工作流门禁

1. 开工前：读本文件 → `docs/agents.md` → 与本任务相关的 `docs/` 章节。
2. 计划：拆成可验收的小步，每步有产出物、通过条件与预算。
3. 实现：先写或改测试，再写实现；测试默认 mock LLM 与网络，不做真实调用。
4. 自检：`arch_checker` → ruff → mypy → pytest。
5. 提交前安装并运行 pre-commit；完整 pytest 由本地或 CI 执行。
6. 交付：说明改了什么 / 为什么 / 怎么验证 / 风险与回滚；文档与命令同步更新。

## 6. 按任务找文档

| 任务 | 先读 |
|------|------|
| 任何实现 | `docs/agents.md` |
| 分层与依赖 | `docs/architecture/boundaries.md`（唯一事实源） |
| 编排 / 状态 / 数据流 | `docs/architecture/data-flow.md`、`docs/agents.md` §5 |
| 预算与降级 | `docs/token-budget.md` |
| 新增 Port / Adapter | `docs/agents.md` §4、`src/illumination/types/ports.py` |
| 数据契约 | `docs/agents.md` §6 |
| 异常处理 | `docs/conventions/error-handling.md` |
| 测试 | `docs/conventions/testing.md` |
| 架构决策 | `docs/decisions/README.md` |

## 7. 维护

- 规则只写一处；本文件只放摘要与链接。
- 架构、命令、质量门变化时，同一提交内更新 `docs/agents.md`。
- 本文件超过 150 行，或出现“只适用于某模块”的内容时，下沉到 `docs/`。
