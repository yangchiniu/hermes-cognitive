# 快速开始指南

本指南帮助你在 5 分钟内开始使用 hermes-cognitive。

## 前置条件

- Python 3.11 或更高版本
- pip 包管理器
- (可选) OpenAI API Key（用于 LLM 集成）

## 安装

### 方式一：一键安装（推荐）

```bash
git clone https://github.com/yangchiniu/hermes-cognitive.git
cd hermes-cognitive
chmod +x scripts/install.sh
./scripts/install.sh
```

### 方式二：手动安装

```bash
git clone https://github.com/yangchiniu/hermes-cognitive.git
cd hermes-cognitive
python -m venv .venv
source .venv/bin/activate  # Linux/macOS
pip install -e .
```

### 方式三：pip 安装（即将支持）

```bash
pip install hermes-cognitive
```

## 环境验证

运行环境检查脚本确认一切正常：

```bash
python scripts/check_env.py
```

预期输出：
```
[Python Version]
  ✓ Python 3.12 (requires >= 3.11)

[Required Packages]
  ✓ yaml
  ✓ numpy

[hermes-cognitive Import]
  ✓ hermes_cognitive v1.0.0

✓ All checks passed (6/6)
```

## 基础使用

### 1. 初始化核心系统

```python
from hermes_cognitive.core import core_initialize, core_health_check

# 初始化所有子系统
result = core_initialize()
print(f"初始化结果: {result}")

# 健康检查
status = core_health_check()
print(f"系统状态: {status}")
```

### 2. 使用策略引擎

```python
from hermes_cognitive.core import get_policy_engine

policy = get_policy_engine()

# 评估操作安全性
result = policy.evaluate_action(
    action_type="terminal_exec",
    context={"command": "ls -la"}
)
print(f"策略评估: {result}")

# 检查禁止的操作
result = policy.evaluate_action(
    action_type="destructive_shell",
    context={"command": "rm -rf /"}
)
print(f"危险操作: {result}")  # 应该被拒绝
```

### 3. 使用记忆系统

```python
from hermes_cognitive.core import get_memory_manager_singleton

memory = get_memory_manager_singleton()

# 存储记忆
memory.store(
    key="project_goal",
    value="构建面向理工科研究生的科研工作站",
    category="semantic"
)

# 检索记忆
result = memory.retrieve("project_goal")
print(f"检索结果: {result}")

# 语义搜索
results = memory.semantic_search("科研工作站")
print(f"语义搜索: {results}")
```

### 4. 使用 OODA 循环

```python
from hermes_cognitive.core import get_kernel_singleton

kernel = get_kernel_singleton()

# 执行 OODA 循环
result = kernel.execute_ooda_cycle(
    observation="用户请求分析量子计算论文",
    context={"domain": "physics", "task_type": "research"}
)
print(f"OODA 结果: {result}")
```

### 5. 使用 Planner

```python
from hermes_cognitive.core.kernel import get_kernel

kernel = get_kernel()

# 生成执行计划
plan = kernel.planner.plan(
    goal="分析量子计算领域的最新进展",
    constraints={"max_steps": 10, "time_limit": 300}
)
print(f"执行计划: {plan}")
```

## 配置

### 策略配置

编辑 `config/policy.yaml` 自定义安全策略：

```yaml
version: 1

# 禁止的操作
forbidden_actions:
  - captcha_bypass
  - destructive_shell

# 资源限制
limits:
  max_runtime_minutes: 30
  max_concurrent_tasks: 4

# 风险阈值
default_risk_threshold: medium
```

### LLM 配置

设置环境变量配置 LLM 提供商：

```bash
# OpenAI
export OPENAI_API_KEY="your-api-key"
export OPENAI_BASE_URL="https://api.openai.com/v1"

# 或使用其他 OpenAI 兼容 API
export OPENAI_BASE_URL="https://your-api-endpoint/v1"
export OPENAI_API_KEY="your-key"
```

## 运行测试

```bash
# 运行全部测试
python tests/test_all.py           # 64 单元测试
python tests/test_integration.py   # 82 集成测试
python tests/test_semantic_retrieval.py  # 18 语义检索测试
python tests/test_remaining.py     # 81 补充测试

# 预期输出: 251/251 tests passed
```

## 下一步

- 阅读 [架构设计](architecture.md) 了解系统内部原理
- 查看 [配置指南](configuration.md) 自定义配置
- 浏览 [示例代码](../examples/) 获取更多用法
- 查看 [API 文档](#) (即将推出)

## 常见问题

### Q: 初始化失败怎么办？

```bash
# 检查环境
python scripts/check_env.py

# 查看详细错误
python -c "from hermes_cognitive.core import core_initialize; core_initialize()"
```

### Q: 如何重置系统状态？

```python
from hermes_cognitive.core import get_kernel_singleton

kernel = get_kernel_singleton()
kernel.reset()  # 重置所有子系统
```

### Q: 如何查看系统日志？

```python
from hermes_cognitive.core import get_telemetry_singleton

telemetry = get_telemetry_singleton()
report = telemetry.get_report()
print(report)
```
