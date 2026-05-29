# 配置指南

hermes-cognitive 使用 YAML 格式的配置文件管理系统行为。

## 配置文件位置

```
hermes-cognitive/
└── config/
    └── policy.yaml    # 主配置文件
```

## 策略配置 (policy.yaml)

### 完整配置示例

```yaml
version: 1

# ---------------------------------------------------------------------------
# 禁止的操作 — 这些操作永远不会被允许
# ---------------------------------------------------------------------------
forbidden_actions:
  - captcha_bypass          # CAPTCHA 绕过
  - destructive_shell       # 破坏性 Shell 操作
  - infinite_loop           # 无限循环
  - credential_harvest      # 凭证窃取
  - unauthorized_port_scan  # 未授权端口扫描

# ---------------------------------------------------------------------------
# 资源和并发限制
# ---------------------------------------------------------------------------
limits:
  max_runtime_minutes: 20      # 单次任务最大运行时间（分钟）
  max_requests_per_domain: 30  # 每域名最大请求数
  max_parallel_browsers: 3     # 最大并行浏览器数
  max_retry_per_step: 3        # 每步骤最大重试次数
  max_concurrent_tasks: 2      # 最大并发任务数
  max_memory_percent: 85       # 最大内存使用百分比
  max_disk_percent: 90         # 最大磁盘使用百分比

# ---------------------------------------------------------------------------
# 风险阈值
# ---------------------------------------------------------------------------
# 工具的风险等级高于此值时，需要用户确认
# 有效值: none, low, medium, high
default_risk_threshold: medium

# ---------------------------------------------------------------------------
# 需要确认的操作
# ---------------------------------------------------------------------------
# 匹配以下任何条件的操作都会触发用户确认
require_confirmation:
  - risk: high                    # 高风险操作
  - type: destructive_shell       # 破坏性 Shell 操作
  - type: captcha_bypass          # CAPTCHA 绕过

# ---------------------------------------------------------------------------
# 工具特定规则
# ---------------------------------------------------------------------------
tool_specific:
  # 终端命令执行
  terminal_exec:
    max_timeout: 300              # 最大超时时间（秒）
    blocked_commands:             # 禁止的命令
      - "rm -rf /"
      - "mkfs"
      - "dd if="

  # 浏览器交互
  browser_interact:
    max_pages: 10                 # 最大打开页面数
    block_domains:                # 禁止访问的域名
      - "*paypal*"
      - "*bank*"

  # 代码执行
  code_exec:
    max_timeout: 600              # 最大超时时间（秒）
    blocked_modules:              # 禁止导入的模块
      - "subprocess"
      - "os.system"
```

### 配置项详解

#### forbidden_actions

禁止的操作类型列表。这些操作会被 PolicyEngine 无条件拒绝，无论上下文如何。

| 操作类型 | 说明 |
|----------|------|
| `captcha_bypass` | 尝试绕过验证码 |
| `destructive_shell` | 可能破坏系统的 Shell 命令 |
| `infinite_loop` | 可能导致无限循环的操作 |
| `credential_harvest` | 尝试窃取凭证信息 |
| `unauthorized_port_scan` | 未授权的端口扫描 |

#### limits

系统资源限制。超过这些限制的操作会被拒绝或排队。

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `max_runtime_minutes` | int | 20 | 单次任务最大运行时间 |
| `max_requests_per_domain` | int | 30 | 每域名最大请求数 |
| `max_parallel_browsers` | int | 3 | 最大并行浏览器数 |
| `max_retry_per_step` | int | 3 | 每步骤最大重试次数 |
| `max_concurrent_tasks` | int | 2 | 最大并发任务数 |
| `max_memory_percent` | int | 85 | 最大内存使用百分比 |
| `max_disk_percent` | int | 90 | 最大磁盘使用百分比 |

#### default_risk_threshold

风险阈值，决定哪些操作需要用户确认。

| 值 | 说明 |
|-----|------|
| `none` | 不需要确认 |
| `low` | 仅高风险需要确认 |
| `medium` | 中等及以上风险需要确认 |
| `high` | 仅极高风险需要确认 |

#### require_confirmation

需要确认的条件列表。每个条件是一个字典，包含以下可选键：

- `risk`: 风险等级 (none, low, medium, high)
- `type`: 操作类型字符串
- `domain`: 域名 glob 模式（可选）

#### tool_specific

工具特定的配置，覆盖或扩展全局检查。

**terminal_exec**:
- `max_timeout`: 命令最大执行时间（秒）
- `blocked_commands`: 禁止执行的命令列表

**browser_interact**:
- `max_pages`: 最大打开页面数
- `block_domains`: 禁止访问的域名列表（支持 glob）

**code_exec**:
- `max_timeout`: 代码最大执行时间（秒）
- `blocked_modules`: 禁止导入的模块列表

## 环境变量

### LLM 配置

```bash
# OpenAI API
export OPENAI_API_KEY="your-api-key"
export OPENAI_BASE_URL="https://api.openai.com/v1"

# 模型选择
export HERMES_LLM_MODEL="gpt-4"
export HERMES_LLM_TEMPERATURE="0.7"
```

### 系统配置

```bash
# 数据目录
export HERMES_DATA_DIR="~/.hermes/core/data"

# 日志级别
export HERMES_LOG_LEVEL="INFO"

# 调试模式
export HERMES_DEBUG="0"
```

## 动态配置

### 运行时更新策略

```python
from hermes_cognitive.core import get_policy_engine

policy = get_policy_engine()

# 更新风险阈值
policy.update_config({
    "default_risk_threshold": "high"
})

# 添加禁止的操作
policy.add_forbidden_action("new_dangerous_action")

# 重新加载配置文件
policy.reload_config()
```

### 程序化配置

```python
from hermes_cognitive.core.policy_engine import PolicyEngine

# 创建自定义策略引擎
policy = PolicyEngine(config={
    "version": 1,
    "forbidden_actions": ["my_forbidden_action"],
    "limits": {
        "max_runtime_minutes": 60,
    },
    "default_risk_threshold": "low",
})
```

## 配置验证

### 使用 check_env.py

```bash
python scripts/check_env.py
```

### 手动验证

```python
import yaml
from pathlib import Path

config_path = Path("config/policy.yaml")
with open(config_path) as f:
    config = yaml.safe_load(f)

# 检查必需字段
required_fields = ["version", "forbidden_actions", "limits"]
for field in required_fields:
    assert field in config, f"Missing required field: {field}"

print("Configuration is valid")
```

## 最佳实践

1. **版本控制**: 将配置文件纳入版本控制
2. **环境分离**: 为不同环境（开发、测试、生产）使用不同配置
3. **最小权限**: 只授予必要的权限
4. **定期审查**: 定期审查和更新安全策略
5. **文档记录**: 记录配置变更的原因和影响

## 故障排除

### 配置文件不生效

```python
# 检查配置文件路径
from hermes_cognitive.core.policy_engine import PolicyEngine
engine = PolicyEngine()
print(f"Config path: {engine.config_path}")
print(f"Loaded config: {engine.config}")
```

### 策略过于严格

```yaml
# 临时降低风险阈值
default_risk_threshold: low

# 移除特定确认条件
require_confirmation:
  - risk: high  # 只确认高风险
```

### 策略过于宽松

```yaml
# 提高风险阈值
default_risk_threshold: high

# 添加更多确认条件
require_confirmation:
  - risk: medium
  - risk: high
  - type: terminal_exec
```
