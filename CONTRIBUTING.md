# 贡献指南

感谢你对 hermes-cognitive 项目的关注！我们欢迎各种形式的贡献。

## 如何贡献

### 报告问题

1. 使用 GitHub Issues 报告 bug
2. 提供详细的复现步骤
3. 包含错误日志和环境信息
4. 使用问题模板（如果有）

### 提交代码

1. Fork 本仓库
2. 创建功能分支：`git checkout -b feature/amazing-feature`
3. 提交更改：`git commit -m 'feat: add amazing feature'`
4. 推送分支：`git push origin feature/amazing-feature`
5. 创建 Pull Request

### 代码规范

- **Python 版本**: 3.11+
- **代码风格**: PEP 8
- **类型注解**: 必须为所有公共函数添加类型注解
- **文档字符串**: 必须为所有公共函数添加 docstring
- **测试**: 新功能必须包含测试用例

### 提交信息规范

使用 [Conventional Commits](https://www.conventionalcommits.org/) 规范：

```
<type>(<scope>): <description>

[optional body]

[optional footer]
```

类型（type）：
- `feat`: 新功能
- `fix`: 修复 bug
- `docs`: 文档更新
- `style`: 代码格式调整（不影响逻辑）
- `refactor`: 代码重构
- `perf`: 性能优化
- `test`: 测试相关
- `chore`: 构建/工具链相关

示例：
```
feat(memory): add semantic search with embeddings

- Implement vector-based semantic retrieval
- Add embedding model integration
- Support similarity threshold filtering

Closes #123
```

### 分支策略

- `main`: 稳定版本，只接受 PR
- `develop`: 开发分支，新功能合入此处
- `feature/*`: 功能分支
- `fix/*`: 修复分支
- `release/*`: 发布准备分支

### 测试要求

```bash
# 运行全部测试
python tests/test_all.py
python tests/test_integration.py
python tests/test_semantic_retrieval.py
python tests/test_remaining.py

# 确保所有测试通过
# 251/251 tests passed
```

### 代码审查

所有 PR 都需要经过代码审查：

1. 确保代码符合项目规范
2. 确保测试覆盖充分
3. 确保文档已更新
4. 确保没有引入 breaking changes

### 开发环境设置

```bash
# 克隆仓库
git clone https://github.com/yangchiniu/hermes-cognitive.git
cd hermes-cognitive

# 创建虚拟环境
python -m venv .venv
source .venv/bin/activate  # Linux/macOS
# .venv\Scripts\activate  # Windows

# 安装依赖
pip install -e ".[dev]"

# 运行测试
python tests/test_all.py
```

### 文档贡献

- 文档使用 Markdown 格式
- 放置在 `docs/` 目录
- 确保链接有效
- 提供代码示例

## 行为准则

- 尊重所有参与者
- 接受建设性批评
- 专注于对社区最有利的事情
- 对他人表示同理心

## 许可证

贡献即表示你同意你的贡献将在 MIT 许可证下发布。

## 联系方式

- GitHub Issues: 项目问题和讨论
- Email: [待补充]

感谢你的贡献！🎉
