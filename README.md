# hermes-cognitive

> Hermes Agent 的认知架构核心 — 为 AI Agent 提供自主决策、学习和适应能力

[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)
[![Tests](https://img.shields.io/badge/tests-251/251-brightgreen.svg)](#testing)

**hermes-cognitive** 是一个模块化的认知架构框架，为 AI Agent 提供完整的决策-执行-学习闭环。它实现了 OODA 决策循环、自适应策略引擎、多层记忆系统和事件溯源，使 Agent 具备自主推理、环境感知和持续进化的能力。

## ✨ 核心特性

| 模块 | 功能 | 状态 |
|------|------|------|
| 🧠 **OODA Loop** | 观察-判断-决策-行动决策循环 | ✅ 生产就绪 |
| 📊 **PolicyEngine** | 可配置的安全策略和风险控制 | ✅ 生产就绪 |
| 🔄 **DriftAnalyzer** | 行为漂移检测 → 策略自适应反馈闭环 | ✅ 生产就绪 |
| 🗄️ **MemoryManager** | 五层记忆系统（语义/情景/程序/环境/索引） | ✅ 生产就绪 |
| 📝 **EventSourcing** | 完整的操作历史记录和回放能力 | ✅ 生产就绪 |
| 🎯 **Planner** | LLM 驱动的任务分解和执行计划 | ✅ 生产就绪 |
| 📈 **Telemetry** | 实时遥测和健康监控 | ✅ 生产就绪 |
| 🛡️ **ReflectionEngine** | 自我反思和策略优化 | ✅ 生产就绪 |
| 🔧 **ToolRegistry** | 工具注册和能力发现 | ✅ 生产就绪 |
| 🏃 **FieldRunner** | 端到端任务执行引擎 | ✅ 生产就绪 |

## 🚀 快速开始

### 安装

```bash
# 克隆仓库
git clone https://github.com/yangchiniu/hermes-cognitive.git
cd hermes-cognitive

# 运行安装脚本
chmod +x scripts/install.sh
./scripts/install.sh

# 或手动安装
pip install -e .
```

### 环境检查

```bash
python scripts/check_env.py
```

### 基本使用

```python
from hermes_cognitive.core import (
    core_initialize,
    core_health_check,
    get_kernel_singleton,
    get_policy_engine,
    get_memory_manager_singleton,
)

# 初始化核心系统
core_initialize()

# 健康检查
status = core_health_check()
print(f"系统状态: {status}")

# 获取内核实例
kernel = get_kernel_singleton()

# 获取策略引擎
policy = get_policy_engine()
result = policy.evaluate_action("terminal_exec", {"command": "ls -la"})
print(f"策略评估: {result}")

# 获取记忆管理器
memory = get_memory_manager_singleton()
memory.store("key", "value", category="working")
value = memory.retrieve("key")
print(f"记忆检索: {value}")
```

## 🏗️ 架构概览

```
┌─────────────────────────────────────────────────────────────┐
│                     Agent Kernel                            │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────┐   │
│  │  OODA    │  │ Planner  │  │ Field    │  │ Goal     │   │
│  │  Loop    │  │          │  │ Runner   │  │ Manager  │   │
│  └────┬─────┘  └────┬─────┘  └────┬─────┘  └────┬─────┘   │
│       │              │              │              │         │
│  ┌────┴──────────────┴──────────────┴──────────────┴────┐   │
│  │              Event Bus (事件总线)                     │   │
│  └────┬──────────────┬──────────────┬──────────────┬────┘   │
│       │              │              │              │         │
│  ┌────┴─────┐  ┌─────┴────┐  ┌─────┴────┐  ┌─────┴────┐   │
│  │ Policy   │  │ Memory   │  │ Drift    │  │ Telemetry│   │
│  │ Engine   │  │ Manager  │  │ Analyzer │  │          │   │
│  └──────────┘  └──────────┘  └──────────┘  └──────────┘   │
│                                                             │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────┐   │
│  │ World    │  │ Tool     │  │Reflection│  │ Recovery │   │
│  │ Model    │  │ Registry │  │ Engine   │  │ Manager  │   │
│  └──────────┘  └──────────┘  └──────────┘  └──────────┘   │
└─────────────────────────────────────────────────────────────┘
```

### 核心模块说明

| 模块 | 文件 | 职责 |
|------|------|------|
| **kernel.py** | AgentKernel | 中央协调器，初始化和管理所有子系统 |
| **ooda_loop.py** | OODALoop | 观察-判断-决策-行动循环引擎 |
| **planner.py** | Planner | LLM 驱动的任务分解和计划生成 |
| **policy_engine.py** | PolicyEngine | 安全策略评估和风险控制 |
| **memory_manager.py** | MemoryManager | 五层记忆系统的统一接口 |
| **drift_analyzer.py** | DriftAnalyzer | 行为漂移检测和策略优化建议 |
| **event_bus.py** | EventBus | 发布-订阅事件总线 |
| **telemetry.py** | Telemetry | 性能指标收集和健康监控 |
| **reflection_engine.py** | ReflectionEngine | 自我反思和策略优化 |
| **tool_registry.py** | ToolRegistry | 工具注册和能力发现 |
| **world_model.py** | WorldModel | 环境状态建模和预测 |
| **state_manager.py** | StateManager | 系统状态持久化和恢复 |
| **goal_manager.py** | GoalManager | 目标追踪和优先级管理 |
| **watchdog.py** | Watchdog | 系统健康监控和自动恢复 |
| **recovery_manager.py** | RecoveryManager | 故障恢复和回滚机制 |
| **experience_manager.py** | ExperienceManager | 经验积累和学习 |
| **semantic_retrieval.py** | SemanticRetrieval | 语义向量检索引擎 |

## 📁 项目结构

```
hermes-cognitive/
├── LICENSE                    # MIT 许可证
├── README.md                  # 本文件
├── CHANGELOG.md               # 版本历史
├── CONTRIBUTING.md            # 贡献指南
├── pyproject.toml             # 项目元数据和构建配置
├── requirements.txt           # Python 依赖
├── config/
│   └── policy.yaml            # 默认策略配置
├── scripts/
│   ├── install.sh             # 一键安装脚本
│   └── check_env.py           # 环境检查脚本
├── docs/
│   ├── architecture.md        # 架构设计文档
│   ├── quickstart.md          # 快速开始指南
│   └── configuration.md       # 配置指南
├── examples/
│   ├── basic_usage.py         # 基础使用示例
│   ├── custom_policy.py       # 自定义策略示例
│   └── llm_integration.py     # LLM 集成示例
├── src/
│   └── hermes_cognitive/
│       ├── __init__.py
│       ├── core/              # 核心模块（27个）
│       └── plugins/           # 插件系统
└── tests/
    ├── test_all.py            # 单元测试 (64)
    ├── test_integration.py    # 集成测试 (82)
    ├── test_semantic_retrieval.py # 语义检索测试 (18)
    ├── test_remaining.py      # 补充测试 (81)
    ├── test_planner.py        # Planner 专项测试
    ├── test_ooda.py           # OODA 专项测试
    ├── test_event_bus.py      # EventBus 专项测试
    ├── benchmarks/            # 性能基准测试
    ├── chaos/                 # 混沌工程测试
    ├── replay/                # 重放测试
    └── stability/             # 稳定性测试
```

## 🧪 测试

```bash
# 运行全部测试
python tests/test_all.py           # 64 单元测试
python tests/test_integration.py   # 82 集成测试
python tests/test_semantic_retrieval.py  # 18 语义检索测试
python tests/test_remaining.py     # 81 补充测试

# 性能基准测试
python tests/benchmarks/run_benchmark.py

# 混沌工程测试
python tests/chaos/run_chaos.py

# 稳定性测试
python tests/stability/run_stability.py
```

## ⚙️ 配置

### 策略配置 (config/policy.yaml)

```yaml
version: 1

# 禁止的操作类型
forbidden_actions:
  - captcha_bypass
  - destructive_shell
  - infinite_loop
  - credential_harvest
  - unauthorized_port_scan

# 资源限制
limits:
  max_runtime_minutes: 20
  max_requests_per_domain: 30
  max_parallel_browsers: 3
  max_retry_per_step: 3
  max_concurrent_tasks: 2
  max_memory_percent: 85
  max_disk_percent: 90

# 风险阈值
default_risk_threshold: medium

# 需要确认的操作
require_confirmation:
  - risk: high
  - type: destructive_shell

# 工具特定规则
tool_specific:
  terminal_exec:
    max_timeout: 300
    blocked_commands:
      - "rm -rf /"
      - "mkfs"
      - "dd if="
  browser_interact:
    max_pages: 10
    block_domains:
      - "*paypal*"
      - "*bank*"
  code_exec:
    max_timeout: 600
    blocked_modules:
      - "subprocess"
      - "os.system"
```

完整配置文档请参阅 [docs/configuration.md](docs/configuration.md)。

## 🔌 插件系统

hermes-cognitive 支持通过插件扩展功能：

```yaml
# plugin.yaml
name: my-plugin
version: 1.0.0
description: "自定义插件"
hooks:
  - on_session_start
  - pre_tool_call
  - post_tool_call
commands:
  - /my-command
```

## 📊 性能指标

| 指标 | 值 | 说明 |
|------|-----|------|
| 测试通过率 | 251/251 (100%) | 全部测试通过 |
| 模块覆盖 | 27/27 (100%) | 所有模块已激活 |
| 类型注解覆盖 | 96.7% | 高类型安全 |
| 代码质量评分 | 8.82/10 | 高质量代码 |
| Hook 延迟 | ~13.5ms | 低开销 |
| 内存占用 | ~30MB | 轻量级 |

## 🤝 贡献

欢迎贡献！请阅读 [CONTRIBUTING.md](CONTRIBUTING.md) 了解详情。

## 📄 许可证

本项目采用 MIT 许可证 - 详见 [LICENSE](LICENSE)

## 🔗 相关项目

- [hermes-research-station](https://github.com/yangchiniu/hermes-research-station) - 面向理工科研究生的科研工作站
