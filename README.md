# LLM 代码审查工具

一个基于大语言模型(LLM)和检索增强生成(RAG)技术的智能代码审查工具，使用Ollama提供本地化的代码分析和审查服务。

## ✨ 主要特性

- 🧠 **智能代码审查**: 使用多个专业的LLM代理对代码进行全方位分析
- 🔍 **RAG增强**: 基于ChromaDB的向量数据库进行代码上下文检索，提供更准确的审查建议
- 📊 **多维度分析**: 涵盖设计、功能性、命名、一致性、代码风格、测试、健壮性、可读性、抽象化等9个维度
- 🎯 **严重程度分级**: 自动评估问题严重程度（Critical/High/Medium/Low）
- 📄 **Markdown输出**: 支持清晰易读的Markdown格式输出
- 🔄 **双模式扫描**: 支持Git Diff模式和全仓库扫描模式
- 🚀 **本地化部署**: 基于Ollama，保证代码隐私和安全

## 🏗️ 系统架构

```
┌─────────────────┐    ┌──────────────────┐    ┌─────────────────┐
│   Git Diff /    │    │   Code Indexer   │    │   Ollama LLMs   │
│ Repository Scan │───▶│   (ChromaDB)     │───▶│   (Multiple     │
└─────────────────┘    └──────────────────┘    │   Specialists)  │
                                               └─────────────────┘
                                                      │
┌─────────────────┐    ┌──────────────────┐           │
│   Formatter     │◀───│   Code Reviewer  │◀──────────┘
│  (Markdown)     │    │   (Planner)      │
└─────────────────┘    └──────────────────┘
```

### 核心组件

- **Code Indexer** (`indexer.py`): 使用ChromaDB对代码库进行向量化索引
- **Code Reviewer** (`planner.py`): 总体规划和协调各个专业审查代理
- **Review Workers** (`worker.py`): 9个专业化的代码审查代理
- **Context Retriever** (`context_retriever.py`): 智能上下文检索和相关性评分
- **Formatters** (`formatter.py`): 多种格式的结果输出

## 📋 审查维度

| 维度 | 关注点 | 严重程度倾向 |
|------|--------|------------|
| **Design** | 单一职责、耦合度、复杂性 | High |
| **Functionality** | 逻辑错误、边界情况、安全漏洞 | Critical |
| **Naming** | 命名规范、描述性、一致性 | Low-Medium |
| **Consistency** | 代码风格一致性、模式统一性 | Medium |
| **Coding Style** | 格式化、注释、表达式复杂度 | Low-Medium |
| **Tests** | 测试覆盖率、边界测试、断言质量 | High |
| **Robustness** | 异常处理、资源管理、并发安全 | High |
| **Readability** | 代码可读性、注释质量、结构清晰度 | Low-Medium |
| **Abstractions** | 代码重复、封装性、抽象层次 | Medium |

## 🚀 快速开始

### 环境要求

- Python 3.12+
- [Ollama](https://ollama.ai/) 已安装并运行
- 足够的磁盘空间用于向量数据库

### 安装依赖
 
```bash
python3 -m venv .venv
pip install uv
uv venv --python 3.12
uv pip install -r requirements.txt
```

### 必需的Ollama模型

确保已下载以下模型：

```bash
# 规划和摘要模型
ollama pull gpt-oss:20b

# 代码审查工作模型  
ollama pull qwen3-coder:7b

# 嵌入模型
ollama pull nomic-embed-text:latest
```

## 💻 使用方法

### 1. Git Diff模式（推荐用于增量审查）

```bash
# 基本用法
python -m reviewer --diff path/to/your.diff --repo /path/to/repo

# 基本审查
python -m reviewer --diff changes.diff --repo ./my-project

# 自定义模型和提示词
python -m reviewer \
    --diff changes.diff \
    --repo ./my-project \
    --prompt "重点检查安全性和性能问题" \
    --planner-model llama3.1:latest \
    --worker-model qwen2.5-coder:7b-instruct-q8_0
```
示例命令
```

```


### 2. 全仓库扫描模式（适用于全面审查）

```bash
# 扫描整个仓库
python -m reviewer --scan-repo --repo /path/to/repo

# 扫描指定文件
python -m reviewer --scan-repo --repo ./my-project --files "src/main.py,src/utils.py"

# 生成详细的报告
python -m reviewer \
    --scan-repo \
    --repo ./my-project \
    --output-dir ./reports
```

示例命令
```bash
python -m reviewer.repo_scanner --repo /home/ubuntu/auto_tag_resource --planner-model gpt-oss:20b --worker-model qwen3-coder:7b --embedding-model nomic-embed-text:latest
```

### 3. 使用仓库扫描器（独立工具）

```bash
# 直接使用仓库扫描器
python -m reviewer.repo_scanner \
    --repo ./my-project \
    --log-file ./scan.log \
    --log-level INFO
```

## ⚙️ 配置选项

### 命令行参数

| 参数 | 说明 | 默认值 |
|------|------|--------|
| `--diff` | Git diff文件路径 | - |
| `--repo` | 仓库根目录路径 | `./` |
| `--scan-repo` | 启用全仓库扫描模式 | False |
| `--files` | 指定扫描的文件列表（逗号分隔） | 所有文件 |
| `--prompt` | 自定义系统提示词 | 默认审查提示 |
| `--ollama-host` | Ollama服务地址 | `http://localhost:11434` |
| `--planner-model` | 规划模型 | `llama3.1:latest` |
| `--worker-model` | 工作器模型 | `qwen2.5-coder:7b-instruct-q8_0` |
| `--embedding-model` | 嵌入模型 | `nomic-embed-text:latest` |
| `--format` | 输出格式 | `markdown` (仅支持) |
| `--reindex` | 强制重新索引 | False |

### 输出格式

- **markdown**: 清晰易读的Markdown表格格式，包含审查建议和严重程度分级

## 📊 输出示例

### Markdown格式

```markdown
# Code Review

## Changelog
- Fixed calculation error in the add function
- Added validation for user input
- Refactored authentication logic

## Review Comments
| File | Line | Category | Severity | Comment |
|------|------|----------|----------|---------|
| main.py | 42 | Functionality | 🔴 Critical | Missing null check for user input |
| utils.py | 15 | Naming | 🟢 Low | Consider renaming 'x' to 'userId' |
```

输出包含详细的审查信息：
- 按严重程度排序的问题列表
- 每个文件的详细审查建议
- 清晰的问题分类和描述
- 便于阅读的表格格式

## 🔧 高级配置

### 自定义审查提示词

```python
custom_prompt = """
重点关注以下方面：
1. 安全漏洞和潜在风险
2. 性能瓶颈和优化机会  
3. 代码可维护性
4. 测试覆盖率
"""

# 在命令行中使用
python -m reviewer --diff changes.diff --prompt "$custom_prompt"
```

### 调整严重程度阈值

工具会根据关键词和上下文自动调整严重程度：

- **Critical**: 安全漏洞、功能缺陷、潜在崩溃
- **High**: 性能问题、设计缺陷、错误处理缺失
- **Medium**: 代码风格、可读性、重复代码
- **Low**: 命名建议、文档改进、小优化

### 模型选择建议

| 用途 | 推荐模型 | 说明 |
|------|----------|------|
| 规划和摘要 | `gpt-oss` | 更好的逻辑推理能力 |
| 代码审查 | `qwen3-coder` | 专业的代码理解能力 |
| 嵌入向量 | `nomic-embed-text:latest` | 高质量的文本嵌入 |

## 🛠️ 开发和扩展

### 项目结构

```
reviewer/
├── __main__.py           # 主入口点
├── models.py            # 数据模型定义
├── planner.py           # 审查规划器
├── worker.py            # 专业审查代理
├── indexer.py           # 代码索引器
├── context_retriever.py # 上下文检索器
├── formatter.py         # 结果格式化器
├── diff_parser.py       # Diff解析器
├── ollama_client.py     # Ollama客户端
├── prompts.py           # 提示词模板
├── reranker.py          # 上下文重排序
├── repo_scanner.py      # 仓库扫描器
└── test_ui_generator.py # 测试UI生成器
```

### 添加新的审查维度

1. 在 `models.py` 中添加新的 `CodeReviewCategory`
2. 在 `prompts.py` 中定义相应的提示词
3. 工具会自动创建对应的审查代理

### 自定义格式化器

继承 `MarkdownFormatter` 基类：

```python
class CustomFormatter(MarkdownFormatter):
    def format(self) -> str:
        # 实现自定义Markdown格式化逻辑
        return formatted_output
```

## 🤝 贡献指南

1. Fork 项目
2. 创建特性分支 (`git checkout -b feature/amazing-feature`)
3. 提交更改 (`git commit -m 'Add amazing feature'`)
4. 推送到分支 (`git push origin feature/amazing-feature`)
5. 创建 Pull Request

## 📝 许可证

本项目采用 MIT 许可证 - 查看 [LICENSE](LICENSE) 文件了解详情。

## 🙏 致谢

- [Ollama](https://ollama.ai/) - 本地LLM服务
- [ChromaDB](https://www.trychroma.com/) - 向量数据库
- [Rich](https://rich.readthedocs.io/) - 终端美化
- [Pygments](https://pygments.org/) - 代码语法高亮

## 📞 支持

如有问题或建议，请：
1. 查看 [Issues](../../issues) 页面
2. 创建新的 Issue
3. 参考文档和示例

---

**让AI助力您的代码质量提升！** 🚀
