# 异常处理规范

## 1. 原则

- 只捕获能处理的异常；无法恢复时保留原异常并向上抛。
- 错误分类决定处理方式：重试、修复、重规划、人工裁决或终止不可互换。
- 异常消息供人排查，稳定错误码供程序分支；不要解析文案决定流程。
- 异常不代替业务结果；`partial`、`skipped` 是有效领域状态。
- 不用重试掩盖结构性错误。
- 不记录密钥、token、完整 prompt、原始外部响应或 traceback 给最终用户。

## 2. 分层职责

| 层 | 职责 | 异常约束 |
|---|---|---|
| Types | 数据结构与纯校验 | Pydantic `ValidationError` 在边界分类 |
| Config | 环境变量与启动校验 | 缺失/非法时启动失败，不静默回退 |
| Repo | HTTP、文件、对象存储等 I/O | 抛 `RepoError` 子类；不抛业务异常 |
| Service | 领域规则与用例 | 抛 `IlluminationError` 子类，带稳定 `code` |
| Runtime | 编排、重试、终态与审计 | 把可分类异常转成 `RunResult`；未知异常 fail closed |
| UI | 输入校验与错误码映射 | 转成 CLI 退出码/协议错误；不输出内部栈 |

依赖方向以 [boundaries.md](../architecture/boundaries.md) 为唯一事实源。

## 3. 错误分类

| 分类 | 当前例子 | 默认处理 | 禁止 |
|---|---|---|---|
| 可重试 | 429、瞬时 5xx、连接错误 | 有界指数退避，最多 `max_attempts` | 对 4xx 与校验错误盲目重试 |
| 可修复 | 结构化输出缺字段 | 换提示或重抽，受修复预算约束 | 无限重跑 |
| 需重规划 | 计划超预算、修复预算耗尽 | 交 Runtime 重规划或降级 | 原策略重跑 |
| 需人工 | 高风险决策（未来 HITL） | 中断并持久化载荷 | 自动替人改数值 |
| 不可恢复 | 状态损坏、安全违例 | 终止 run、保留现场、写审计 | 伪装成成功 |
| 领域状态 | `partial`、`skipped` | 作为类型化结果返回 | 当异常吞掉 |

## 4. 异常类型

Service（`service/errors.py`）：

```python
class IlluminationError(Exception):
    code = "illumination_error"

class StructuredOutputError(IlluminationError):
    code = "structured_output_invalid"

class PlanningError(IlluminationError):
    code = "planning_invalid"

class ConfigurationError(IlluminationError):
    code = "configuration_invalid"
```

Repo（`repo/errors.py`）：

```python
class RepoError(Exception): ...
class LLMAdapterError(RepoError): ...
class ArtifactStoreError(RepoError): ...
```

- 下层异常不 import 上层异常类型。
- 包装底层异常必须保留异常链：`raise NewError(...) from exc`。
- 不要 `raise exc`；需要原样传播时用 `raise`。

## 5. 重试

- 仅对 429、瞬时 5xx、连接错误与适配器定义的可重试超时重试。
- 默认最多 3 次，指数退避；有 `Retry-After` 时应遵守。
- 重试期间请求参数保持不变；变更请求属于换策略，不是重试。
- 非幂等写操作没有幂等键时不得重试。

## 6. Runtime 处理

1. 判断错误分类，不按错误字符串猜测。
2. 在有界策略内重试；每次 attempt 记录事件。
3. 需要修复/重规划时先保存产物。
4. 需要人工时持久化中断载荷与恢复点。
5. 不可恢复异常标记 run 失败并保留现场。
6. 日志字段包含 `run_id`、`step_id`、`error_code`、`attempt`，且脱敏。

## 7. UI 映射

| 情况 | CLI 退出码 | 对外响应 |
|---|---:|---|
| 参数错误 | 2 | 用法说明 |
| 业务/配置失败 | 1 | 稳定错误码与可安全显示的说明 |
| 成功 / partial | 0 | 结果路径与状态 |
| 未知内部错误 | 1 | 通用说明与关联 ID；栈只进受控日志 |

API 统一返回 `{"error": {"code": "...", "message": "..."}}`。请求校验失败为
422 `invalid_request`；缺少或无效会话为 401；Cookie 会话的 CSRF 失败为 403；
未知异常为 500 `internal_error`。领域异常按稳定 `code` 显式映射 HTTP 状态，
响应使用固定安全说明，不透出异常消息、校验输入详情或内部栈。

## 8. 禁止事项

- Repo 抛业务异常或做领域判断。
- UI 捕获业务异常后继续执行。
- Runtime 吞掉未知异常或返回空结果。
- 对所有异常一律重试三次。
- 丢失异常链、裸 `except:`、捕获后静默 `pass`。
- 向日志、审计或响应输出密钥、完整 prompt、原始外部响应或 traceback。

## 9. 测试要求

- 每种异常策略都有正反向测试。
- 可重试异常在上限内重试，耗尽后不继续。
- 非幂等请求没有幂等保障时不重试。
- 未知异常使 run 失败并保留现场。
- UI 响应不包含 traceback、密钥或上游正文。
