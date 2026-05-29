# hermes-cognitive

> 让 AI Agent 学会自己思考

[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)
[![Tests](https://img.shields.io/badge/tests-251/251-brightgreen.svg)](#testing)

---

## 这是什么？

hermes-cognitive 是一个给 AI Agent 用的"大脑"。

你可以把它理解成：**一个让 AI 学会自己做决定、记住经验、越用越聪明的框架**。

现在的 AI 助手大多是你问一句它答一句，没有真正的"思考"能力。hermes-cognitive 试图解决这个问题——它让 AI 能够：

- 🔍 **观察**周围发生了什么
- 🤔 **判断**当前情况意味着什么
- 🎯 **决定**应该怎么做
- 🚀 **行动**并从结果中学习

就像人类的思考过程一样。

## 为什么要做这个？

我们发现，现有的 AI Agent 框架普遍存在几个问题：

1. **不会记仇** — 被坑了一次，下次还会被同样的方式坑
2. **不会反思** — 做错了就错了，不会总结经验教训
3. **不会保护自己** — 让它删文件它就删，没有安全意识
4. **不会规划** — 复杂任务直接硬做，不会拆分成小步骤

hermes-cognitive 就是为了解决这些问题而生的。

## 它是怎么工作的？

想象一下你接到一个复杂任务时的思考过程：

```
"帮我分析一下量子计算领域的最新进展"

你的大脑会这样处理：
1. 观察 → 这是一个学术研究任务，需要查找文献
2. 判断 → 我需要搜索论文、整理要点、形成报告
3. 决定 → 先搜索最新论文，再按主题分类，最后写总结
4. 行动 → 执行每一步，遇到问题及时调整
5. 记住 → 这次的经验下次可以复用
```

hermes-cognitive 就是把这个过程自动化了：

```
┌─────────────────────────────────────────────────────────┐
│                                                         │
│   用户请求  ──→  观察  ──→  判断  ──→  决定  ──→  行动  │
│                    ↑                              │     │
│                    │          学习反馈            │     │
│                    └──────────────────────────────┘     │
│                                                         │
│   记忆系统 ←─────────────────────────────────────────── │
│   (记住这次发生了什么，下次做得更好)                      │
│                                                         │
│   安全策略 (防止做危险的事情)                            │
│                                                         │
└─────────────────────────────────────────────────────────┘
```

## 核心能力

| 能力 | 说明 | 通俗解释 |
|------|------|----------|
| 🧠 **思考循环** | OODA 决策引擎 | 像人一样观察→判断→决定→行动 |
| 📚 **记忆系统** | 五层记忆架构 | 能记住事情，而且分得清轻重缓急 |
| 🛡️ **安全防护** | 策略引擎 | 知道什么能做，什么不能做 |
| 🔄 **自我反思** | 漂移检测 | 发现自己跑偏了会自动纠正 |
| 📝 **经验积累** | 事件溯源 | 每次行动都记录下来，方便复盘 |
| 🎯 **任务规划** | 智能分解 | 复杂任务拆成小步骤一步步做 |

## 快速开始

### 安装

```bash
# 克隆仓库
git clone https://github.com/yangchiniu/hermes-cognitive.git
cd hermes-cognitive

# 一键安装
chmod +x scripts/install.sh
./scripts/install.sh
```

### 跑起来看看

```python
from hermes_core.core import core_initialize, get_policy_engine, get_memory_manager

# 初始化
core_initialize()

# 试试安全策略
policy = get_policy_engine()
result = policy.evaluate_action("terminal_exec", {"command": "ls -la"})
print(f"这个操作安全吗？{result}")

# 试试记忆功能
memory = get_memory_manager()
memory.store("第一次见面", "你好世界", category="working")
print(memory.retrieve("第一次见面"))  # → "你好世界"
```

### 运行测试

```bash
python tests/test_all.py            # 基础测试
python tests/test_integration.py    # 集成测试
# ... 更多测试见 tests/ 目录
```

## 项目结构

```
hermes-cognitive/
├── src/hermes_core/          # 核心代码
│   ├── core/                 # 27 个核心模块
│   └── plugins/              # 插件系统
├── tests/                    # 测试套件（251 个测试）
├── docs/                     # 文档
│   ├── architecture.md       # 架构详解（想深入了解的看这个）
│   ├── quickstart.md         # 快速上手指南
│   └── configuration.md      # 配置说明
├── examples/                 # 示例代码
│   ├── basic_usage.py        # 基础用法
│   ├── custom_policy.py      # 自定义安全规则
│   └── llm_integration.py    # 接入大语言模型
├── config/
│   └── policy.yaml           # 安全策略配置
└── scripts/
    ├── install.sh            # 安装脚本
    └── check_env.py          # 环境检查
```

## 它能用来做什么？

- **科研助手** — 帮你搜索论文、整理文献、生成报告
- **自动化运维** — 监控系统、自动处理异常、记录操作日志
- **智能客服** — 理解用户意图、记住历史对话、持续改进回答
- **个人助理** — 管理任务、学习你的偏好、越用越顺手

任何需要 AI "有脑子"的场景，都可以考虑用它。

## 配置

安全策略在 `config/policy.yaml` 里配置：

```yaml
# 哪些事情绝对不能做
forbidden_actions:
  - captcha_bypass        # 不能破解验证码
  - destructive_shell     # 不能执行破坏性命令
  - credential_harvest    # 不能窃取密码

# 资源限制
limits:
  max_runtime_minutes: 20      # 单次任务最多跑 20 分钟
  max_concurrent_tasks: 2      # 最多同时跑 2 个任务
```

更多配置项见 [docs/configuration.md](docs/configuration.md)。

## 技术指标

| 指标 | 数值 |
|------|------|
| 核心模块 | 27 个 |
| 测试用例 | 251 个（100% 通过） |
| 类型注解覆盖 | 96.7% |
| 内存占用 | ~30MB |
| 单次 Hook 延迟 | ~13ms |

## 贡献

欢迎！不管是提 bug、写文档还是加功能，都请看 [CONTRIBUTING.md](CONTRIBUTING.md)。

## 许可证

MIT — 随便用，记得保留版权声明就行。

## 相关项目

- [hermes-research-station](https://github.com/yangchiniu/hermes-research-station) — 面向理工科研究生的科研工作站
