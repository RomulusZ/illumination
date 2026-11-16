# Illumination

通用 Agent Harness 骨架。工程基建与 Cairn 对齐：六层包结构、分层 import 检查、
锁定的工具链、CI 质量门与可复现测试；业务领域尚未确定，`types/` 只保留最小通用契约。

当前可运行的示例切片：

```text
RunRequest → 规划服务（结构化输出校验）→ LangGraph 计划节点 → artifact 落盘 → RunResult
```

> **状态：骨架阶段。** 执行器、审计账本、预算计量、HITL 与沙箱尚未实现；
> 规划步骤会被标记为 `skipped`，运行状态为 `partial`，这是有意为之。

## 快速开始

需要 Python 3.12 与 [uv](https://docs.astral.sh/uv/)。

```bash
uv sync --frozen --group dev
```

本地基础设施由 Docker 提供：

| 服务 | 容器 | 地址 |
|------|------|------|
| PostgreSQL 17 | `infra-postgres` | `127.0.0.1:15432` |
| Redis 7 | `infra-redis` | `127.0.0.1:16379` |

开发库与测试库必须是两个不同的 PostgreSQL 数据库：

```bash
docker exec infra-postgres psql -U postgres -c "CREATE DATABASE illumination;"
docker exec infra-postgres psql -U postgres -c "CREATE DATABASE illumination_test;"
cp .env.example .env   # 填入本地密码后使用
```

测试只连接 `illumination_test`，禁止指向开发库。Redis 当前未被 P0 使用，设计上仅作为可选的会话中间态缓存。

离线跑通链路（不调用外部模型）：

```bash
uv run illumination run --objective "验证骨架链路" --out out --stub
```

产物：`out/plan.json`（artifact store 写入）与 `out/result.json`（运行结果）。

接真实模型：

```bash
export OPENAI_API_KEY="..."
uv run illumination run --objective "你的目标" --out out
```

## 质量门

```bash
uv run ruff check .
uv run ruff format --check .
uv run mypy
uv run pytest
uv run python lint/arch_checker.py
```

CI 使用同一组命令；`.pre-commit-config.yaml` 已把 Ruff、mypy 与 `arch_checker` 挂到本地提交前。

## 目录

| 路径 | 作用 |
|------|------|
| `src/illumination/types/` | Pydantic 契约、Enum、Protocol / ports；不依赖任何其他层 |
| `src/illumination/config/` | `Settings`、环境变量、启动校验 |
| `src/illumination/repo/` | LLM、时钟、artifact store 等外部适配器 |
| `src/illumination/service/` | 领域规则与用例；只依赖 ports |
| `src/illumination/runtime/` | LangGraph 编排与组合根 |
| `src/illumination/ui/` | CLI 入口、参数校验、错误映射 |
| `tests/` | 单元、runtime、CLI、arch_checker fixture |
| `lint/` | 分层依赖检查 |
| `docs/` | 架构、规范、ADR 与 Agent 操作手册 |

唯一允许的 import 方向：

```text
Types → Config → Repo → Service → Runtime → UI
```

## 扩展点

1. **接入业务领域**：在 `types/` 增加领域契约，在 `service/` 增加纯规则与用例。
2. **接入真实工具**：在 `types/ports.py` 定义 Port，在 `repo/` 实现 Adapter，在 `runtime/composition.py` 装配。
3. **增加执行节点**：在 `runtime/pipeline.py` 的图中增加节点，并声明状态 reducer。
4. **增加 UI**：`ui/` 可新增 FastAPI 路由；Agent run 必须走 Runtime。

## 文档

- `AGENTS.md`：编码 Agent 的常驻硬约束与导航。
- `docs/agents.md`：项目地图、分层契约、运行时与测试方式。
- `docs/architecture/`：总览、边界、数据流。
- `docs/conventions/`：命名、异常、测试规范。
- `docs/token-budget.md`：预算字段、计量与降级。
- `docs/decisions/`：ADR 索引。

## 许可证

待定。
