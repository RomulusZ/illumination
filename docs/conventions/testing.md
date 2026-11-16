# 测试规范

测试的目标是证明四件事：领域规则正确、外部边界可控、失败可分类、结果可复现。

## 1. 基本原则

- 测试必须可重复：不依赖真实网络、真实 LLM、真实时间、本机路径或未隔离的外部服务状态。
- 外部依赖默认通过 Port / Adapter 替换为 fake；数据库集成测试例外，必须使用隔离的 PostgreSQL 测试库。
- 先写失败测试，再实现；一次只验证一个行为。
- 断言必须具体：断言值、类型、错误码、调用次数或产物内容。
- 禁止为通过而 skip、xfail、删断言或放宽容差。
- 测试失败信息包含输入、期望、实际与关键上下文。
- 测试代码同样遵守命名规范与分层边界。

## 2. 目录结构

测试目录镜像 `src/illumination/`：

```text
src/illumination/types/plan.py       →  tests/types/test_plan.py
src/illumination/config/settings.py  →  tests/config/test_settings.py
src/illumination/repo/llm_adapter.py →  tests/repo/test_llm_adapter.py
src/illumination/service/planning.py →  tests/service/test_planning.py
src/illumination/runtime/pipeline.py →  tests/runtime/test_pipeline.py
src/illumination/ui/cli.py          →  tests/cli/test_cli.py
```

其他目录：

```text
tests/arch/       arch_checker 的合法/违规 import fixture
tests/fixtures/   共享 fixture 与小型样本（需要时再建）
tests/fakes.py    跨测试复用的 fake ports
```

## 3. 分层测试策略

| 层 | 替换什么 | 重点验证 |
|---|---|---|
| Types | 不替换外部依赖 | Pydantic 校验、边界值、序列化、稳定字段名 |
| Config | 环境变量与 `.env` | 必填缺失失败、默认值、条件校验、密钥不泄露 |
| Repo | HTTP / 文件系统 / 时钟 | 请求构造、响应映射、重试、429、400、畸形载荷 |
| SQL Repo / 迁移 | 隔离的 PostgreSQL 17 测试库 | 约束、事务、并发、幂等、upgrade/downgrade |
| Service | Ports | 领域规则、结构化输出校验、异常分类 |
| Runtime | Ports、clock、store | 图分支、状态增量、失败终态、组合根装配 |
| UI | Runtime / Service | 参数校验、退出码、响应不泄露内部信息 |
| arch_checker | 临时包与 import fixture | 每条 ARCH 规则至少一个合法、一个违规用例 |

## 4. Fake 与 Mock

- `FakeLLM`：脚本化响应，记录收到的请求；不进行 I/O。
- `FakeClock`：手动推进时间；禁止在测试中 `time.sleep()`。
- `InMemoryArtifactStore`：只保存指针与内容，不写磁盘。
- HTTP：使用 `httpx.MockTransport`，不发起真实请求。
- 需要验证“下一步取决于观察”的分支时，使用脚本化响应序列，而不是随机模型。

## 5. 数据库测试

- Types、Config 与纯领域 Service 不连接数据库；SQL Repo、涉及持久化的 Service 和 E2E 使用真实 PostgreSQL 17。
- 测试库必须与开发库、生产库隔离；禁止 SQLite 替代、自动降级或静默回退。
- 会话级 Fixture 只创建一次 Schema；普通测试用事务回滚隔离，需要验证真实提交的用例使用独立事务并清理数据。
- 迁移测试在独立空库执行 upgrade/downgrade/upgrade，不用已有开发库验证迁移。
- 幂等与唯一约束至少有一个使用独立连接/线程的并发竞争测试，不能只顺序调用两次。
- CI 必须启动 PostgreSQL 17 Service；数据库不可用时测试明确失败并说明配置缺失。

## 6. Agent 测试

- 必须 mock LLM，不做真实调用。
- 覆盖意图解析边界、工具参数构造、失败重试上限与 `partial` 返回。
- 结构化输出测试必须验证 schema、缺失字段与非法枚举。
- 不断言 prompt 全文；断言契约、工具选择与禁止行为。
- 不依赖网络、时钟或外部向量库。

## 7. 时间、网络与快照

- 时间由 `ClockPort` 注入；检索日期、审计时间与重试等待不得依赖真实时钟。
- HTTP 用 `httpx.MockTransport`。
- 稳定产物（计划、矩阵、SVG 等）可用快照测试；快照变化必须人工确认。
- 随机性必须固定种子；测试不得依赖执行顺序或并发调度。

## 8. 覆盖率与质量门

- 整体覆盖率目标 ≥ 80%；Service 与 Types 更高。
- `arch_checker` 必须覆盖全部 ARCH 规则。
- 覆盖率不能替代断言质量。
- PR 合并前必须通过完整本地门禁与 CI：

```bash
uv run ruff check .
uv run ruff format --check .
uv run mypy
uv run pytest
uv run python lint/arch_checker.py
```

## 9. 命名与结构

- 测试文件：`test_<module>.py`。
- 测试函数：`test_<行为>_<条件>_<期望>`，例如
  `test_rate_limit_is_retried()`、`test_run_once_failure_is_typed()`。
- 使用 Arrange / Act / Assert。
- 一个测试只验证一个行为；多个断言服务于同一结论。
- 参数化用 `pytest.mark.parametrize`，不要把多个场景塞进一个长测试。

## 10. 禁止事项

- 真实网络、真实 LLM、开发库或生产库连接。
- 为通过测试而 skip、xfail、删断言或降低覆盖率阈值。
- 用 `time.sleep()` 等待重试或异步任务。
- 依赖测试执行顺序、共享可变全局状态或开发者本机 `.env`。
- 在测试中复制业务规则实现，导致测试与生产同时写错。
- 只用“没有异常”代替结果验证。
