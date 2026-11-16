# Illumination P0 实施 Todo List

> 目标：按设计完成“入口对话 → 就绪度 → 目标锁定 → 节奏单元 → 证据打卡 → 周期评估 → 收口”的最小完整闭环。
> 本清单只覆盖 P0，不承诺一次实现全部 35 表 / 143 端点。每个节点都是可独立提交、可运行、可验收的小功能。

## 0. 执行规则

- 顺序执行，不跳节点；每个节点完成后必须保证**历史测试也全部通过**。
- 每个节点的生产代码约 **40–200 行**，测试代码不计入；超过 200 行就先拆分，不堆隐式复杂度。
- 每个节点同时交付实现与测试，禁止先写空壳再补逻辑。
- 纯类型与领域服务测试使用 FakeLLM + FakeClock，不连接数据库、网络或真实模型。
- 仓储、迁移、幂等、并发与 E2E 测试必须使用隔离的 PostgreSQL 17 测试库，禁止 SQLite 替代或静默回退。
- PostgreSQL 测试会话只建一次 Schema，普通测试用事务回滚隔离；迁移测试使用独立空库。
- 保留现有六层骨架与 `RunRequest → RunPlan → RunResult` 示例，新增能力不得破坏它。
- 跨层依赖继续由 `arch_checker` 阻断；数据库和 HTTP 细节不得进入 `types` / `service`。
- 每节点提交前运行：

```bash
uv run ruff check .
uv run ruff format --check .
uv run mypy
uv run pytest
uv run python lint/arch_checker.py
```

## 1. 开始前的基线确认

开工前在 ADR 中冻结以下口径，否则后续节点不要开始：

1. 表数量、端点数量、错误码以哪份文档为准。
2. 客户端时钟偏差统一采用哪种处理：拒绝、按服务器时间记录并提示，或钳制并返回 warnings。
3. 每日提醒上限采用哪个值。
4. Agent 工具白名单到底是 6 个还是 10 个。
5. 不变式以 PRD 的 7 条还是需求整合的 5 条为准。

当前目录尚未初始化 Git。开始 P0-01 前执行一次：

```bash
git init
git add .
git commit -m "chore: bootstrap generic agent harness skeleton"
```

---

## 2. 领域契约与基础设施

### P0-01 增加应用依赖与配置

- **实现**：在 `pyproject.toml` 增加 FastAPI、SQLAlchemy、Alembic、APScheduler；在 `config/settings.py` 增加 `database_url`、`invite_code`、`agent_mode`、日界/提醒时间、时钟偏差策略，生产环境校验必填项。
- **规模**：约 80–130 行。
- **测试**：`tests/config/test_settings.py` 验证 PostgreSQL 默认值、拒绝 SQLite/其他数据库 URL、生产必须显式配置数据库、时界格式。
- **验收**：`uv sync` 后 `uv run pytest tests/config -q` 通过，旧配置测试不回归。
- **提交**：`feat(config): add application and persistence settings`

### P0-02 定义领域枚举与值对象

- **实现**：新增 `types/enums.py`，定义 Goal 8 态、Cycle 4 态、Evidence 来源/状态、就绪度 L0–L3、Review 结论与阶段决策；新增通用 `LocalDateRange` 等必要值对象。
- **规模**：约 70–120 行。
- **测试**：`tests/types/test_enums.py` 验证枚举序列化、非法值拒绝、日期区间边界。
- **验收**：所有枚举可由 JSON 稳定往返，非法状态直接 `ValidationError`。
- **提交**：`feat(types): define goal lifecycle domain enums`

### P0-03 建立 Goal / GoalVersion / Metric 契约

- **实现**：新增 `types/goal.py`，包含 Intent、Goal、GoalVersion、Metric 及创建 DTO；代码校验 1 主 + 最多 2 辅、主指标唯一、GoalVersion append-only 输入约束。
- **规模**：约 140–190 行。
- **测试**：`tests/types/test_goal_models.py` 覆盖合法模型、辅指标超额、重复主指标、缺必填字段。
- **验收**：非法名额和指标组合无法构造；合法模型 JSON 往返一致。
- **提交**：`feat(types): add goal version and metric contracts`

### P0-04 建立 Cycle / Action / Evidence / Review 契约

- **实现**：新增 `types/execution.py`，定义四个核心实体与命令 DTO；校验 Action 必须绑定观测项、Evidence 必须有来源与时间、Review 必须关联 Cycle。
- **规模**：约 150–200 行。
- **测试**：`tests/types/test_execution_models.py` 覆盖缺观测项、非法证据状态、跨日边界与评估缺失。
- **验收**：核心实体可独立构造和序列化；设计中的必填约束均有失败测试。
- **提交**：`feat(types): add execution and review contracts`

### P0-05 建立 Goal 侧持久化模型与迁移

- **实现**：`repo/db.py` 提供 engine/session 工厂；`repo/models.py` 增加 users/goals/goal_versions/metrics 表；`alembic/` 建立首个可重复执行迁移。
- **规模**：约 170–200 行。
- **测试**：`tests/repo/test_db_schema.py` 用独立空 PostgreSQL 17 测试库执行 upgrade/downgrade/upgrade，验证部分唯一索引、外键与事务行为；CI 提供 PostgreSQL 17 Service。
- **验收**：空 PostgreSQL 库可迁移到目标版本，重复 upgrade 到 head 无副作用；数据库不可用时明确失败，不回退 SQLite。
- **提交**：`feat(repo): add goal persistence schema`

### P0-06 建立执行侧持久化模型与幂等表

- **实现**：增加 cycles/actions/evidence/reviews/idempotency_keys/client_ops 表及迁移；实现单 active Goal、单 active Cycle 所需的数据库约束或服务端兜底索引。
- **规模**：约 180–200 行。
- **测试**：`tests/repo/test_execution_schema.py` 验证外键、唯一幂等键、证据来源/状态约束。
- **验收**：迁移可重复执行；重复 `client_op_id` 不能产生第二条操作记录。
- **提交**：`feat(repo): add execution and idempotency schema`

### P0-07 实现仓储 Port 与 SQL 适配器

- **实现**：`types/ports.py` 增加 Goal/Cycle/Evidence/Review Repository Protocol 与 UnitOfWork；`repo/sql_repositories.py` 实现 CRUD、append-only version 和事务提交。
- **规模**：约 180–200 行。
- **测试**：`tests/repo/test_sql_repositories.py` 用真实 PostgreSQL 验证保存/读取、版本追加、事务回滚；幂等与唯一约束增加独立连接的并发竞争用例。
- **验收**：Service 可只依赖 Protocol；异常时事务完整回滚，不出现半写入。
- **提交**：`feat(repo): implement transactional domain repositories`

---

## 3. 可独立验收的领域服务

### P0-08 实现本地时间与周期归属

- **实现**：`service/time_rules.py` 统一时区、04:00 日界、周期起止和客户端偏差处理；所有输入为显式时间，不读系统时钟。
- **规模**：约 80–140 行。
- **测试**：`tests/service/test_time_rules.py` 覆盖 03:59/04:00、跨月、DST 策略与超偏差输入。
- **验收**：相同输入始终得到相同业务日期；偏差行为与基线 ADR 一致。
- **提交**：`feat(service): add timezone-aware cycle attribution`

### P0-09 实现就绪度评估 L0–L3

- **实现**：`service/readiness.py` 根据结构化答案计算等级，处理 5 个必填 + 2 个可后置、Q1/Q2 答否停锁、基线无默认值。
- **规模**：约 100–160 行。
- **测试**：`tests/service/test_readiness.py` 覆盖 L0–L3 各一例、缺答案、Q1/Q2 否决、禁止默认基线。
- **验收**：等级和停锁原因以类型化结果返回，不通过异常文本判断。
- **提交**：`feat(service): implement readiness assessment rules`

### P0-10 实现目标锁定与版本快照

- **实现**：`service/goal_lifecycle.py` 完成就绪度检查、名额检查、GoalVersion 快照、Goal 激活和显式事务；版本只追加不覆盖。
- **规模**：约 140–190 行。
- **测试**：`tests/service/test_goal_lifecycle.py` 覆盖成功锁定、未就绪、已有 active Goal、重复提交和回滚。
- **验收**：锁定后可读回完整快照；失败不产生 Goal 或版本残留。
- **提交**：`feat(service): lock goals with append-only snapshots`

### P0-11 实现 Cycle 创建与激活

- **实现**：`service/cycle_service.py` 支持 frozen → active，校验单 active Cycle、提前激活不超过 14 天、同单元单线程。
- **规模**：约 100–160 行。
- **测试**：`tests/service/test_cycle_service.py` 覆盖正常激活、重复 active、过早激活、非法状态跳转。
- **验收**：非法跳转返回稳定错误码；成功路径状态和时间持久化。
- **提交**：`feat(service): enforce cycle activation invariants`

### P0-12 实现 Action 计划与完成规则

- **实现**：`service/action_service.py` 创建动作、绑定观测项、完成动作；完成必须有对应周期且状态合法。
- **规模**：约 90–150 行。
- **测试**：`tests/service/test_action_service.py` 覆盖创建、完成、重复完成幂等、缺观测项、已关闭周期。
- **验收**：重复完成不增加计数；所有完成记录可追溯到 Action 和 Cycle。
- **提交**：`feat(service): plan and complete observable actions`

### P0-13 实现证据提交与 confirmed 计数

- **实现**：`service/evidence_service.py` 支持 manual/candidate/confirmed，按 `Idempotency-Key` 或 `client_op_id` 去重；统计只读取 confirmed。
- **规模**：约 140–190 行。
- **测试**：`tests/service/test_evidence_service.py` 覆盖首次提交、两种幂等通道、candidate 不计数、跨用户键隔离。
- **验收**：相同请求重放返回原结果；计数在 candidate/confirmed 混合数据下正确。
- **提交**：`feat(service): add idempotent confirmed evidence tracking`

### P0-14 实现离线同步逐字段裁决

- **实现**：`service/offline_sync.py` 只接受交证据和完成动作，按基线 ADR 的时钟策略处理；输出逐字段接受/拒绝/冲突结果。
- **规模**：约 120–180 行。
- **测试**：`tests/service/test_offline_sync.py` 覆盖允许操作、拒绝目标编辑、重复操作、时间偏差与字段冲突。
- **验收**：离线包可重复同步且无重复写入；每项裁决都有原因码。
- **提交**：`feat(service): resolve offline operation conflicts`

### P0-15 实现周期评估与 closing/reviewed 收口

- **实现**：`service/review_service.py` 聚合 confirmed 证据和动作，生成周期结论，执行 active → closing → reviewed；无评估不得关闭。
- **规模**：约 140–190 行。
- **测试**：`tests/service/test_review_service.py` 覆盖评估生成、无评估关闭失败、重复评估、状态回退拒绝。
- **验收**：评估结果可由输入确定性重算；Review 后周期只读。
- **提交**：`feat(service): close cycles with deterministic reviews`

### P0-16 实现 Goal 撤回、暂停与终态

- **实现**：扩展 `goal_lifecycle.py` 或新增 `goal_closure.py`，支持 24h 撤回、暂停/恢复、achieved/archived/abandoned/expired 合法迁移。
- **规模**：约 120–180 行。
- **测试**：`tests/service/test_goal_closure.py` 覆盖撤回窗口内外、各合法迁移、终态不可再修改。
- **验收**：状态迁移表中的每条非法路径都有拒绝测试；历史版本不重算。
- **提交**：`feat(service): enforce goal closure transitions`

---

## 4. Agent 与运行时

### P0-17 实现入口对话结构化输出

- **实现**：`service/intake.py` 定义并校验识别、三问、回述、一行摘要的 LLM 输出；限制最多 8 轮，下游只读取约定的结构化字段。
- **规模**：约 130–180 行。
- **测试**：`tests/service/test_intake.py` 用 FakeLLM 覆盖正常输出、坏 JSON、缺字段、超轮次和注入文本不进入指令。
- **验收**：所有模型输出经 Pydantic；第 9 轮不会发起模型调用。
- **提交**：`feat(service): validate bounded intake conversations`

### P0-18 建立 Intake LangGraph 子图

- **实现**：`runtime/intake_graph.py` 建立 intake → readiness → recap → lock 路由；checkpoint 只存 ID/摘要/指针，复用组合根注入 LLM 与 Service。
- **规模**：约 160–200 行。
- **测试**：`tests/runtime/test_intake_graph.py` 覆盖正常锁定、停锁、模型失败、恢复后不新增对话轮。
- **验收**：FakeLLM 下图可完整运行；失败产生类型化终态而非伪装成功。
- **提交**：`feat(runtime): orchestrate intake and goal locking`

### P0-19 建立 FastAPI 应用、认证与错误映射

- **实现**：`runtime/app.py` 创建应用并复用组合根；`ui/api/` 增加 `/health`、邀请码登录、Cookie+CSRF/Bearer 依赖和统一 21 码中本阶段所需错误映射。
- **规模**：约 170–200 行。
- **测试**：`tests/cli/` 不动，新增 `tests/api/test_app.py` 覆盖健康检查、无凭证 401、错误响应无 traceback/密钥。
- **验收**：TestClient 可启动完整应用；`user_id` 只能来自会话，不能来自请求体。
- **提交**：`feat(api): bootstrap authenticated application`

### P0-20 实现 Intent / Goal API

- **实现**：`ui/api/goals.py` 提供创建 Intent、就绪度查询、锁定 Goal、读取当前 Goal/版本、24h 撤回；请求响应使用 Types DTO。
- **规模**：约 160–200 行。
- **测试**：`tests/api/test_goals.py` 覆盖完整 HTTP 成功路径、未就绪 409/422、重复锁定幂等、越权访问。
- **验收**：OpenAPI 可生成；HTTP 状态、错误码和 Service 结果一致。
- **提交**：`feat(api): expose intent and goal lifecycle`

### P0-21 实现 Cycle / Action API

- **实现**：`ui/api/cycles.py` 提供创建/激活 Cycle、查询当前单元、创建 Action、完成 Action；禁止请求体指定他人 `user_id`。
- **规模**：约 150–200 行。
- **测试**：`tests/api/test_cycles.py` 覆盖单 active、提前 14 天限制、完成幂等、非法状态迁移。
- **验收**：完整 HTTP 流程可执行；领域错误不被转换成无差别 500。
- **提交**：`feat(api): expose cycles and observable actions`

### P0-22 实现 Evidence 与离线同步 API

- **实现**：`ui/api/evidence.py` 提供证据提交、confirmed 计数查询、离线包同步；支持 `Idempotency-Key` 和 `client_op_id`。
- **规模**：约 150–200 行。
- **测试**：`tests/api/test_evidence.py` 覆盖重放响应一致、候选证据不计数、离线拒绝目标编辑、时钟偏差响应。
- **验收**：同一请求连续发送两次只产生一条证据；离线裁决逐项可见。
- **提交**：`feat(api): accept online and offline evidence`

### P0-23 实现 Review / Goal 收口 API

- **实现**：`ui/api/reviews.py` 提供生成周期评估、确认 reviewed、Goal 状态迁移与历史查询。
- **规模**：约 130–180 行。
- **测试**：`tests/api/test_reviews.py` 覆盖无评估关闭失败、评估后只读、非法 Goal 迁移、历史版本不覆盖。
- **验收**：可从 API 走完 active Cycle 到 reviewed；所有终态可读取。
- **提交**：`feat(api): close cycles and goals through reviews`

### P0-24 实现 Agent SSE 接口

- **实现**：`ui/api/agent.py` 提供对话运行与流式输出，统一 `done/error/resync` 事件；截断可见、不重放，正文不进入 checkpoint。
- **规模**：约 160–200 行。
- **测试**：`tests/api/test_agent_sse.py` 用 FakeLLM 验证事件顺序、done 恰好一次、模型失败 error、断线 resync。
- **验收**：SSE 流可逐事件解析；错误后不错误发送 done，敏感正文不进入日志断言。
- **提交**：`feat(api): stream resumable agent conversations`

---

## 5. 任务、端到端与交付门禁

### P0-25 增加幂等周期任务

- **实现**：`runtime/jobs.py` 用 APScheduler 注册周期到期、Review 待办和提醒任务；每次执行带 `job_run_id`，重复触发不重复写副作用。
- **规模**：约 120–180 行。
- **测试**：`tests/runtime/test_jobs.py` 用 FakeClock 覆盖到期一次、重复执行幂等、无 active 单元、提醒上限。
- **验收**：同一任务同一时间窗重复运行结果一致；失败任务有稳定错误记录。
- **提交**：`feat(runtime): schedule idempotent cycle jobs`

### P0-26 完成 P0 端到端旅程

- **实现**：`tests/e2e/test_p0_journey.py` 用 TestClient + 隔离的 PostgreSQL 测试库 + FakeLLM 串联：登录 → 对话 → 就绪 → 锁 Goal → 激活 Cycle → 创建/完成 Action → 提交证据 → Review → 关闭。
- **规模**：测试约 150–200 行；只允许补不超过 40 行的装配修正，不借 E2E 新增大功能。
- **测试**：本文件即验收测试，另断言数据库只有 1 个 active Goal/Cycle、证据计数正确、版本追加。
- **验收**：`uv run pytest tests/e2e/test_p0_journey.py -q` 独立通过，完整 `uv run pytest` 通过。
- **提交**：`test(e2e): verify complete goal achievement journey`

### P0-27 冻结 OpenAPI 与质量门

- **实现**：增加 OpenAPI 快照/契约测试、已使用错误码清单和 README 启动命令；将 P0 已实现与未实现能力明确列出，不虚报 143 端点完成。
- **规模**：约 80–140 行（不含 OpenAPI 生成物）。
- **测试**：`tests/api/test_openapi_contract.py` 验证路由注册、错误响应 schema、快照变更必须显式更新。
- **验收**：五项质量门全部通过；CI 使用相同命令并启动 PostgreSQL 17 Service；从空 PostgreSQL 库可按 README 启动。
- **提交**：`docs(api): freeze p0 openapi and delivery gates`

---

## 6. 每个节点的提交模板

```bash
git add <本节点明确修改的文件>
git status
git diff --cached
git commit -m "<本节点给出的提交信息>"
git log -1 --oneline
```

不要 `git add -A` 后不看差异；不要提交 `.env`、数据库文件、`out/`、`.data/`、缓存或密钥。若某节点未通过完整测试，不创建“完成”提交。

## 7. P0 完成定义

- P0-01 至 P0-27 均有独立提交，且每次提交前完整质量门通过。
- 核心不变式由代码与测试保证，不只写在文档中。
- FakeLLM 下完整旅程可重复运行；真实模型不是测试通过的必要条件。
- 所有失败有稳定错误码，`partial/candidate/skipped` 不伪装成成功。
- OpenAPI、README 与实际实现一致，未实现的设备接入、完整内容库和全部端点明确列入后续阶段。
