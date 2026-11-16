# docs/agents.md — Illumination Agent 操作手册与项目地图

> 读者：编码 Agent 与新加入的工程师。
> 关系：根 `AGENTS.md` 是常驻硬约束；本文是按需阅读的详细版。
> 冲突优先级：代码与测试 > 根 `AGENTS.md` > 本文 > 其他文档。
> 当前状态：骨架阶段；工程基建与最小规划切片已落地。

## 0. 怎么用本文

1. 接到任务先读 §1、§2，理解项目定位与术语。
2. 动手前读 §3、§4、§8，确认分层、Port 与测试方式。
3. 交付前对照 §10 的完成定义。

## 1. 项目背景

- **目标**：提供一套可直接复制到新业务项目的 Agent Harness 骨架。
- **输入**：`RunRequest`（目标、上下文、步骤上限）。
- **当前输出**：`RunResult` 与 `plan.json`。
- **非目标**：骨架阶段不内置业务领域，不产生真实副作用，不承诺预算/HITL/审计已完成。
- **核心判据**：能用代码判定的写成代码；只有真正需要判断的才交给模型。

### 1.1 当前落地状态

| 资产 | 状态 | 位置 |
|---|---|---|
| 六层包骨架 | 已落地 | `src/illumination/` |
| 分层 import 检查 | 已落地 | `lint/arch_checker.py` |
| Settings 与启动校验 | 已落地 | `src/illumination/config/settings.py` |
| LLM Port + OpenAI 兼容适配器 | 已落地 | `types/ports.py`、`repo/llm_adapter.py` |
| 规划服务与结构化输出校验 | 已落地 | `service/planning.py`、`service/llm_structured.py` |
| LangGraph 最小图 | 已落地（plan → finalize） | `runtime/pipeline.py` |
| Artifact store | 已落地（本地文件） | `repo/file_artifact_store.py` |
| 目标与执行侧持久化 | 已落地（users/goals/goal_versions/goal_metrics/cycles/actions/evidences/reviews/idempotency_keys/sync_ops） | `repo/db.py`、`repo/models.py`、`alembic/` |
| 仓储 Port 与 SQL 适配器 | 已落地（Goal/Cycle/Evidence/Review + UnitOfWork） | `types/ports.py`、`repo/sql_repositories.py` |
| 业务日期、周期窗口与时钟偏差 | 已落地 | `service/time_rules.py` |
| 就绪度 L0–L3 与停锁规则 | 已落地 | `service/readiness.py` |
| CLI | 已落地（`run`） | `ui/cli.py` |
| 执行器 / 预算 / 审计 / HITL / 沙箱 | 未实现 | 见路线图 |

### 1.2 路线图

| 阶段 | 范围 | 验收标准 |
|---|---|---|
| M0 骨架 | 六层结构、工具链、CI、最小规划切片 | 所有质量门通过 |
| M1 执行层 | 工具 Port、执行节点、失败分类 | 每步可执行、失败可分类、产物可回放 |
| M2 运行层 | 预算计量、审计账本、checkpoint | 预算写入产物；中断可恢复 |
| M3 人工门 | HITL 中断与审批载荷 | 恢复不新增对话轮；审批不改数值 |
| M4 隔离层 | 沙箱与 conformance | 未知输入不拖垮编排层 |

## 2. 领域词汇表

| 术语 | 含义 | 代码对应 |
|---|---|---|
| Harness | 承载 Agent 的工程外壳：编排、契约、预算、审计、UI | 全仓库 |
| Run | 一次目标执行 | `RunRequest`、`RunResult`、`RunState` |
| Plan | 有界、结构化的步骤序列 | `RunPlan`、`PlanStep` |
| Step Outcome | 单步观察结果 | `StepOutcome`、`StepStatus` |
| Artifact | 落在状态之外的产物 | `ArtifactRef`、`ArtifactStorePort` |
| Port | Service 依赖的外部能力接口 | `types/ports.py` |
| Adapter | Port 的具体实现 | `repo/` |
| 组合根 | 唯一实例化适配器并完成注入的位置 | `runtime/composition.py` |
| 结构化输出 | 模型返回的、经 schema 校验的 JSON | `service/llm_structured.py` |
| partial | 有产出但未完成全部步骤的诚实状态 | `RunStatus.PARTIAL` |
| 稳定错误码 | 供程序分支的 `snake_case` 常量 | `*.code` |

## 3. 分层契约（摘要）

唯一允许的方向：`Types → Config → Repo → Service → Runtime → UI`。

- `service` 不 import `repo`；通过 ports 注入。
- `ui` 不 import `repo`；run 走 runtime。
- `runtime` 允许 import `repo` 与 `service`，是唯一组合根。
- `types` 不依赖任何其他层。
- 完整规则与 ARCH 编号见 [architecture/boundaries.md](architecture/boundaries.md)。

## 4. Port 与 Adapter

新增外部能力的三步：

1. 在 `types/ports.py` 定义 `Protocol`，只使用领域类型与基本类型。
2. 在 `repo/` 实现 Adapter；I/O 异常转成 `RepoError` 子类，保留异常链。
3. 在 `runtime/composition.py` 装配；Service 通过参数接收 Port。

禁止：

- 在 Service 内实例化 Adapter；
- 在模块顶层创建全局客户端；
- 让 Port 暴露 `httpx`、数据库连接、`Settings` 或图状态类型。

## 5. 运行时契约

### 5.1 图

```text
START → plan → finalize → END
```

- `plan`：调用 `service.planning.plan_run`；领域错误转 `failed`。
- `finalize`：写 `plan.json`；存储失败转 `failed`。
- 节点返回值是状态增量，不是完整状态。
- 新增并行字段时必须声明 reducer；同 key 不同值抛错。

### 5.2 状态

- 只放契约对象、摘要与指针；正文落 artifact store。
- 时间来自 `ClockPort`。
- 一个节点只负责一类职责；不要在图节点里写领域规则。

### 5.3 错误

- Service 抛 `IlluminationError` 子类，带稳定 `code`。
- Repo 抛 `RepoError` 子类；不抛业务异常。
- Runtime 把可分类错误转成 `RunResult.error_code`，未知错误 fail closed。
- UI 只输出稳定错误码与可安全显示的说明，不输出 traceback。

## 6. 数据契约

当前契约：

| 模型 | 文件 | 约束 |
|---|---|---|
| `RunRequest` | `types/plan.py` | `objective` 非空；`max_steps` 1–32 |
| `PlanStep` / `RunPlan` | `types/plan.py` | 步骤非空；`step_id` 唯一 |
| `RunResult` | `types/plan.py` | `finished_at ≥ started_at` |
| `ArtifactRef` | `types/common.py` | `sha256` 为 64 位小写十六进制 |
| `LocalDateRange` | `types/common.py` | 本地日期闭区间；`end_date ≥ start_date`；`days` 含首尾 |
| 领域枚举 | `types/enums.py` | Goal 8 态、Cycle 4 态、Evidence 通道/状态、Readiness L0–L3、CycleResult、StageDecision、Goal/Metric 词汇；取值与 DDL 一致 |
| `Intent` / `Goal` / `GoalVersion` / `Metric` | `types/goal.py` | 版本只追加；1 主 + 2 辅槽位唯一；advance 必须有期限；基线三态与目标形态由代码校验 |
| `Cycle` / `Action` / `Evidence` / `Review` | `types/execution.py` | 周期左闭右开；动作必须绑定 `metric_key`；证据来源与 `captured_at` 必填、candidate 仅设备来源；Review 两种 scope 字段互斥 |
| `LLMRequest` / `LLMResponse` | `types/llm.py` | 消息非空；usage 非负 |

约定：

- 模型类不加 `Model` / `Schema` / `DTO` 后缀。
- 字段 `snake_case`；标识写清对象：`run_id`、`step_id`、`artifact_id`。
- 布尔字段用 `is_` / `has_` / `can_` / `should_` 前缀。
- 时间字段用 `_at` / `_date`；路径用 `_path` / `_root` / `_dir`。
- 摘要用 `_hash` / `_sha256` / `_digest`。

## 7. 配置

- 所有环境变量只通过 `config.settings.Settings` 读取。
- 自定义变量前缀 `ILLUMINATION_*`；模型与追踪沿用 `OPENAI_*` / `LANGSMITH_*`。
- 生产环境缺少 `OPENAI_API_KEY` 必须启动失败。
- 数据库仅支持 PostgreSQL；开发库与测试库必须是不同 URL，否则启动失败。
- 测试用 `Settings(_env_file=None, ...)`，不读取开发者 `.env`。
- 禁止在日志、异常或 `repr` 中泄露密钥；使用 `SecretStr`。

关键变量：

```text
ILLUMINATION_ENV / ILLUMINATION_WORKSPACE_ROOT / ILLUMINATION_MAX_STEPS
ILLUMINATION_DATABASE_URL / ILLUMINATION_TEST_DATABASE_URL
ILLUMINATION_INVITE_CODES / ILLUMINATION_AGENT_MODE
ILLUMINATION_DAY_CUTOFF / ILLUMINATION_LATEST_START_TIME
ILLUMINATION_CLOCK_SKEW_STRATEGY / ILLUMINATION_CLOCK_SKEW_LIMIT_MINUTES
OPENAI_API_KEY / OPENAI_BASE_URL / ILLUMINATION_MODEL_MAIN
ILLUMINATION_HTTP_TIMEOUT / ILLUMINATION_HTTP_MAX_ATTEMPTS
ILLUMINATION_RUN_TOKEN_BUDGET / ILLUMINATION_CALL_TOKEN_BUDGET
LANGSMITH_TRACING / LANGSMITH_API_KEY / LANGSMITH_PROJECT
```

## 8. 测试

- 默认 mock LLM 与网络，不做真实调用。
- 数据库相关集成、迁移、并发与 E2E 测试使用隔离的 PostgreSQL 17 测试库，禁止 SQLite 回退。
- 时间用 `FakeClock`，不依赖真实时钟。
- 目录镜像源码；共享 fake 放 `tests/fakes.py`。
- 每条 ARCH 规则至少一个合法、一个违规用例。
- 详细规范见 [conventions/testing.md](conventions/testing.md)。

```bash
uv run pytest
uv run pytest tests/runtime/test_pipeline.py -q
uv run pytest tests/arch/test_arch_checker.py
uv run alembic upgrade head   # 使用 ILLUMINATION_DATABASE_URL
```

## 9. 工作流

1. 读 `AGENTS.md` 与本文。
2. 把任务拆成可验收小步。
3. 先写失败测试，再写实现。
4. 自检：arch_checker → ruff → mypy → pytest。
5. 交付：改了什么 / 为什么 / 怎么验证 / 风险与回滚。

## 10. 完成定义

一个变更可交付，当且仅当：

- 分层方向未被破坏；
- 新行为有测试覆盖，失败路径也有断言；
- `uv run ruff check .`、`uv run ruff format --check .`、`uv run mypy`、
  `uv run pytest`、`uv run python lint/arch_checker.py` 全部通过；
- 公开契约变化同步更新测试与文档；
- 没有把未实现能力描述成已完成。
