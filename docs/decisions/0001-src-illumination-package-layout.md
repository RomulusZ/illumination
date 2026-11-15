# 0001：`src/illumination` 包布局与分层

- 状态：Accepted
- 日期：2026-11-15

## 背景

新项目需要一套可直接复用的 Agent Harness 骨架。Cairn 已验证 `src/<package>/` 布局与
`Types → Config → Repo → Service → Runtime → UI` 分层能有效阻断反向依赖；直接复制该结构
可以复用 `arch_checker`、CI 与测试约定，降低新项目的启动成本。

本仓库根目录同样叫 `illumination`，与包名相同，会让“按目录名查找包根”的静态检查
误判模块名。

## 决策

1. 业务代码放在 `src/illumination/`，按六层划分子包。
2. 唯一允许的 import 方向为 `types → config → repo → service → runtime → ui`。
3. `service` 只依赖 ports；`runtime` 是唯一组合根；`ui` 不 import `repo`。
4. `lint/arch_checker.py` 在定位包根时，优先选择父目录为 `src` 的同名目录；
   不存在时退回最后一个同名目录，以兼容测试 fixture。
5. 分层规则以 `docs/architecture/boundaries.md` 为唯一事实源。

## 替代方案

- **扁平布局**（`illumination/` 在仓库根）：包与根目录同名，静态检查与工具配置更容易冲突。
- **按功能分包**（`features/planning/`）：在骨架阶段没有足够业务信息，容易过早抽象。
- **预先引入 DDD 四层**：对通用 harness 过重，且无法强制导入方向。

## 后果

- 正面：分层可被机器验证；新项目可复制即用；测试目录有稳定镜像规则。
- 负面：骨架阶段存在空层与少量未使用的 Port，需要后续业务填充。
- 约束：新增层或改变 import 方向必须写新 ADR 并更新 `arch_checker`。

## 失效条件

出现以下任一情况时重新评估：

- 业务域需要多包（monorepo）或独立部署单元；
- 分层被证明阻碍交付，且两次以上需要口头例外；
- `src` 布局与构建/部署工具产生不可调和的冲突。
