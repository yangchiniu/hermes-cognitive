# 架构设计文档

## 概述

hermes-cognitive 是一个模块化的认知架构框架，为 AI Agent 提供完整的决策-执行-学习闭环。本文档描述系统的整体架构设计、核心模块职责和数据流。

## 设计原则

1. **模块化**: 每个子系统独立封装，通过事件总线松耦合通信
2. **渐进式**: 支持按需加载，避免启动时全量初始化
3. **可观测**: 完整的事件溯源和遥测系统
4. **自适应**: DriftAnalyzer → PolicyEngine 反馈闭环
5. **安全第一**: PolicyEngine 硬性拦截危险操作

## 系统架构

```
┌─────────────────────────────────────────────────────────────────┐
│                        Agent Kernel                             │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────┐       │
│  │  OODA    │  │ Planner  │  │ Field    │  │ Goal     │       │
│  │  Loop    │  │          │  │ Runner   │  │ Manager  │       │
│  └────┬─────┘  └────┬─────┘  └────┬─────┘  └────┬─────┘       │
│       │              │              │              │             │
│  ┌────┴──────────────┴──────────────┴──────────────┴────┐       │
│  │              Event Bus (事件总线)                     │       │
│  └────┬──────────────┬──────────────┬──────────────┬────┘       │
│       │              │              │              │             │
│  ┌────┴─────┐  ┌─────┴────┐  ┌─────┴────┐  ┌─────┴────┐       │
│  │ Policy   │  │ Memory   │  │ Drift    │  │ Telemetry│       │
│  │ Engine   │  │ Manager  │  │ Analyzer │  │          │       │
│  └──────────┘  └──────────┘  └──────────┘  └──────────┘       │
│                                                                 │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────┐       │
│  │ World    │  │ Tool     │  │Reflection│  │ Recovery │       │
│  │ Model    │  │ Registry │  │ Engine   │  │ Manager  │       │
│  └──────────┘  └──────────┘  └──────────┘  └──────────┘       │
└─────────────────────────────────────────────────────────────────┘
```

## 核心模块

### 1. AgentKernel (kernel.py)

**职责**: 中央协调器，负责初始化和管理所有子系统。

**关键功能**:
- 子系统生命周期管理（初始化、启动、停止、重置）
- 健康检查和状态报告
- 子系统间协调

**单例模式**: 全局唯一实例，通过 `get_kernel()` 获取。

### 2. OODA Loop (ooda_loop.py)

**职责**: 实现观察-判断-决策-行动决策循环。

**循环阶段**:
1. **Observe (观察)**: 收集环境状态和上下文信息
2. **Orient (判断)**: 分析当前状态，识别关键因素
3. **Decide (决策)**: 基于分析结果生成行动方案
4. **Act (行动)**: 执行决策并收集反馈

**与 Planner 集成**: OODA 的 Decide 阶段调用 Planner 生成详细计划。

### 3. Planner (planner.py)

**职责**: LLM 驱动的任务分解和计划生成。

**关键功能**:
- 任务分解为可执行步骤
- 依赖关系分析
- 资源需求评估
- 计划优化和验证

**LLM 集成**: 支持 OpenAI 兼容 API，通过环境变量配置。

### 4. PolicyEngine (policy_engine.py)

**职责**: 安全策略评估和风险控制。

**策略类型**:
- **forbidden_actions**: 禁止的操作类型（硬性拦截）
- **limits**: 资源和并发限制
- **require_confirmation**: 需要用户确认的操作
- **tool_specific**: 工具特定的规则

**风险评估**: 基于操作类型、风险等级和上下文综合评估。

### 5. MemoryManager (memory_manager.py)

**职责**: 五层记忆系统的统一接口。

**记忆层次**:

| 层次 | 类型 | 用途 | 持久化 |
|------|------|------|--------|
| L1 | Working Memory | 当前任务上下文 | 否 |
| L2 | Episodic Memory | 事件序列记录 | 是 |
| L3 | Semantic Memory | 向量化知识检索 | 是 |
| L4 | Procedural Memory | 学习到的模式 | 是 |
| L5 | Environmental Memory | 环境状态快照 | 否 |

**语义检索**: 基于向量相似度的知识检索，支持语义搜索。

### 6. DriftAnalyzer (drift_analyzer.py)

**职责**: 行为漂移检测和策略优化建议。

**漂移类型**:
- **Goal Drift**: 目标偏离检测
- **Strategy Drift**: 策略效果退化检测
- **Performance Drift**: 性能指标异常检测

**反馈闭环**: DriftAnalyzer → PolicyEngine，自动调整策略参数。

### 7. EventBus (event_bus.py)

**职责**: 发布-订阅事件总线。

**事件类型**:
- 系统事件（启动、停止、错误）
- 工具事件（调用前、调用后）
- LLM 事件（请求前、响应后）
- 任务事件（开始、完成、失败）

**解耦设计**: 发布者和订阅者完全解耦，支持动态注册。

### 8. Telemetry (telemetry.py)

**职责**: 性能指标收集和健康监控。

**监控指标**:
- Hook 延迟（pre_tool, post_tool, pre_llm, post_llm）
- 任务执行时间
- 内存使用情况
- 错误率和错误类型

### 9. ReflectionEngine (reflection_engine.py)

**职责**: 自我反思和策略优化。

**反思触发条件**:
- 任务失败
- 性能退化
- 用户反馈
- 定期自检

### 10. ToolRegistry (tool_registry.py)

**职责**: 工具注册和能力发现。

**注册信息**:
- 工具名称和描述
- 输入输出模式
- 风险等级
- 资源需求

## 数据流

### 任务执行流程

```
用户请求
    ↓
AgentKernel.execute()
    ↓
OODA Loop.observe() → 收集上下文
    ↓
OODA Loop.orient() → 分析状态
    ↓
OODA Loop.decide() → Planner.plan()
    ↓                     ↓
    ↓              PolicyEngine.evaluate()
    ↓                     ↓
    ↓              返回执行计划
    ↓
FieldRunner.execute_plan()
    ↓
ToolRegistry.invoke()
    ↓
PolicyEngine.check_before_execute()
    ↓
执行工具
    ↓
EventBus.emit("post_tool_call")
    ↓
DriftAnalyzer.analyze()
    ↓
MemoryManager.store()
    ↓
返回结果
```

### 策略反馈闭环

```
DriftAnalyzer.analyze()
    ↓
检测到行为漂移
    ↓
生成优化建议
    ↓
PolicyEngine.update_policy()
    ↓
策略参数调整
    ↓
下次任务使用新策略
```

## 存储架构

### SQLite 数据库

- **cognitive.db**: 认知数据（记忆、事件、状态）
- **performance.db**: 性能数据（遥测、指标）

### 文件存储

- **semantic_index.pkl**: 语义向量索引
- **tool_registry.json**: 工具注册信息
- **planner_preferences.json**: Planner 配置

## 扩展点

### 1. 自定义工具

通过 `ToolRegistry.register()` 注册新工具。

### 2. 自定义策略

编辑 `config/policy.yaml` 或通过 `PolicyEngine.update_policy()` 动态调整。

### 3. 自定义记忆后端

实现 `MemoryBackend` 接口，替换默认的 SQLite 后端。

### 4. 自定义 LLM Provider

实现 `LLMProvider` 接口，支持不同的 LLM 服务。

## 性能特征

| 指标 | 值 | 说明 |
|------|-----|------|
| Hook 延迟 | ~13.5ms | 事件钩子平均延迟 |
| Pre-tool 延迟 | ~80ms | 工具调用前检查延迟 |
| 内存占用 | ~30MB | 基础运行时内存 |
| 数据库大小 | ~10MB | 包含所有历史数据 |
| 启动时间 | <2s | 完整初始化时间 |

## 安全设计

### 多层防御

1. **策略层**: PolicyEngine 定义允许/禁止的操作
2. **检查层**: 每次工具调用前进行安全检查
3. **监控层**: Telemetry 实时监控异常行为
4. **恢复层**: RecoveryManager 处理异常和回滚

### 硬性拦截

以下操作被无条件禁止：
- CAPTCHA 绕过
- 破坏性 Shell 操作
- 无限循环
- 凭证窃取
- 未授权端口扫描
