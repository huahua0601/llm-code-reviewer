# 配置文件使用说明

代码仓库扫描器现在支持通过配置文件指定参数，这样可以更方便地管理复杂的配置。

## 支持的格式

- **YAML格式** (推荐): `.yaml` 或 `.yml` 扩展名
- **JSON格式**: `.json` 扩展名

## 使用方法

### 1. 纯配置文件方式

```bash
python -m reviewer.repo_scanner --config config.yaml
```

### 2. 混合方式（配置文件 + 命令行参数）

命令行参数优先级更高，会覆盖配置文件中的相应设置：

```bash
python -m reviewer.repo_scanner --config config.yaml --worker-model qwen2.5-coder:14b
```

### 3. 原有命令行方式（仍然支持）

```bash
python -m reviewer.repo_scanner \
    --repo /home/ubuntu/dify/api \
    --prompt "重点检查安全性、逻辑错误、边界情况、安全漏洞、性能问题" \
    --planner-model gpt-oss:20b \
    --worker-model qwen3-coder:30b \
    --embedding-model nomic-embed-text:latest \
    --categories Functionality,Design \
    --output-dir ./reports \
    --log-file ./scan.log \
    --log-level INFO
```

## 配置文件示例

### YAML格式 (config.example.yaml)

```yaml
# 基本配置
repo: "/home/ubuntu/dify/api"
prompt: "重点检查安全性、逻辑错误、边界情况、安全漏洞、性能问题"

# 模型配置
ollama_host: "http://localhost:11434"
planner_model: "gpt-oss:20b"
worker_model: "qwen3-coder:30b"
embedding_model: "nomic-embed-text:latest"

# 扫描配置
files:
  - "src/**/*.py"
  - "api/**/*.py"
  - "models/**/*.py"

categories:
  - "Functionality"
  - "Design"
  - "Performance"
  - "Security"

# 输出配置
format: "markdown"
output_dir: "./reports"

# 日志配置
log_file: "./scan.log"
log_level: "INFO"

# 索引配置（可选，不指定则使用默认配置）
ignore_extensions:
  - ".jpg"
  - ".jpeg"
  - ".png"
  - ".gif"
  - ".pdf"
  - ".zip"
  - ".tar"
  - ".gz"
  - ".pyc"
  - ".log"

ignored_dirs:
  - ".git"
  - ".chroma"
  - ".venv"
  - "node_modules"
  - "__pycache__"
  - ".pytest_cache"
  - "test_*"              # 忽略所有以test_开头的目录
  - "*/temp"              # 忽略任何路径下的temp目录
  - "build/*"             # 忽略build目录下的所有内容
  - "docs/*/generated"    # 忽略docs下任何子目录中的generated目录

# 其他选项
reindex: false
```

### JSON格式 (config.example.json)

```json
{
  "repo": "/home/ubuntu/dify/api",
  "prompt": "重点检查安全性、逻辑错误、边界情况、安全漏洞、性能问题",
  
  "ollama_host": "http://localhost:11434",
  "planner_model": "gpt-oss:20b",
  "worker_model": "qwen3-coder:30b",
  "embedding_model": "nomic-embed-text:latest",
  
  "files": [
    "src/**/*.py",
    "api/**/*.py", 
    "models/**/*.py"
  ],
  
  "categories": [
    "Functionality",
    "Design",
    "Performance",
    "Security"
  ],
  
  "format": "markdown",
  "output_dir": "./reports",
  
  "log_file": "./scan.log", 
  "log_level": "INFO",
  
  "ignore_extensions": [
    ".jpg",
    ".jpeg", 
    ".png",
    ".gif",
    ".pdf",
    ".zip",
    ".tar",
    ".gz",
    ".pyc",
    ".log"
  ],
  
  "ignored_dirs": [
    ".git",
    ".chroma",
    ".venv",
    "node_modules",
    "__pycache__",
    ".pytest_cache",
    "test_*",
    "*/temp",
    "build/*",
    "docs/*/generated"
  ],
  
  "reindex": false
}
```

## 配置参数说明

| 参数 | 类型 | 说明 | 默认值 |
|------|------|------|--------|
| `repo` | string | 仓库根目录路径 | `"./"` |
| `files` | list/string | 要扫描的文件列表 | 所有文件 |
| `categories` | list/string | 审查类别 | 所有类别 |
| `prompt` | string | 系统提示词 | 默认提示词 |
| `ollama_host` | string | Ollama API主机地址 | `"http://localhost:11434"` |
| `planner_model` | string | 规划和摘要模型 | `"gpt-oss:20b"` |
| `worker_model` | string | 工作器模型 | `"qwen2.5-coder:7b"` |
| `embedding_model` | string | 嵌入模型 | `"nomic-embed-text:latest"` |
| `reindex` | boolean | 强制重新索引 | `false` |
| `format` | string | 输出格式 | `"markdown"` |
| `log_file` | string | 日志文件路径 | 无 |
| `log_level` | string | 日志级别 | `"INFO"` |
| `output_dir` | string | 输出目录 | `"./out"` |
| `ignore_extensions` | list/string | 忽略的文件扩展名 | 默认忽略列表 |
| `ignored_dirs` | list/string | 忽略的目录名（支持glob模式） | 默认忽略列表 |

## 优先级规则

1. **命令行参数** > **配置文件** > **默认值**
2. 只有明确提供的命令行参数才会覆盖配置文件中的设置
3. 如果命令行参数是默认值，则不会覆盖配置文件中的设置

## 实用技巧

### 1. 为不同项目创建专门的配置文件

```bash
# 为项目A创建配置
cp config.example.yaml project-a-config.yaml
# 编辑project-a-config.yaml...

# 为项目B创建配置  
cp config.example.yaml project-b-config.yaml
# 编辑project-b-config.yaml...

# 使用专门配置扫描不同项目
python -m reviewer.repo_scanner --config project-a-config.yaml
python -m reviewer.repo_scanner --config project-b-config.yaml
```

### 2. 临时覆盖配置文件中的设置

```bash
# 使用配置文件，但临时更换模型
python -m reviewer.repo_scanner --config config.yaml --worker-model llama3.1:8b

# 使用配置文件，但临时更改输出目录
python -m reviewer.repo_scanner --config config.yaml --output-dir ./temp-reports
```

### 3. 数组格式的两种写法

YAML格式支持两种数组写法：

```yaml
# 方式1: 列表格式（推荐）
files:
  - "src/**/*.py"
  - "api/**/*.py"
  
# 方式2: 字符串格式
files: "src/**/*.py,api/**/*.py"
```

JSON格式只支持数组格式：

```json
{
  "files": ["src/**/*.py", "api/**/*.py"]
}
```

### 4. 自定义索引忽略规则

```yaml
# 只忽略特定类型的图片文件
ignore_extensions:
  - ".jpg"
  - ".png"
  
# 忽略特定的构建目录
ignored_dirs:
  - "dist"
  - "build"
  - "target"
```

### 5. 目录模式匹配（Glob Pattern）

`ignored_dirs` 支持glob模式匹配，可以使用通配符来匹配多个目录：

```yaml
ignored_dirs:
  # 精确匹配
  - "node_modules"        # 匹配名为node_modules的目录
  
  # 通配符匹配
  - "test_*"             # 匹配所有以test_开头的目录 (如: test_unit, test_integration)
  - "*_backup"           # 匹配所有以_backup结尾的目录
  - "temp?"              # 匹配temp加一个字符 (如: temp1, tempa)
  
  # 路径匹配
  - "*/temp"             # 匹配任何路径下的temp目录 (如: src/temp, lib/temp)
  - "build/*"            # 匹配build目录下的所有内容
  - "docs/*/generated"   # 匹配docs下任何子目录中的generated目录
  - "src/*/test*"        # 匹配src下子目录中以test开头的目录
```

**支持的通配符：**
- `*` : 匹配任意数量的字符（不包括路径分隔符）
- `?` : 匹配单个字符
- `[seq]` : 匹配seq中的任意字符
- `[!seq]` : 匹配不在seq中的任意字符

### 6. 针对不同语言项目的配置

```yaml
# Python项目配置
ignore_extensions:
  - ".pyc"
  - ".pyo" 
  - ".pyd"
ignored_dirs:
  - "__pycache__"
  - ".pytest_cache"
  - "venv"
  - ".venv"
  - "test_*"             # 忽略所有测试目录
  - "*/migrations"       # 忽略Django迁移文件

# JavaScript/Node.js项目配置  
ignore_extensions:
  - ".min.js"
  - ".map"
ignored_dirs:
  - "node_modules"
  - "dist" 
  - ".next"
  - "coverage"
  - "build/*"            # 忽略build目录下的所有内容
  - "*/test"             # 忽略所有test目录

# 多语言混合项目配置
ignored_dirs:
  - "vendor"             # PHP composer
  - "node_modules"       # Node.js
  - "__pycache__"        # Python
  - "target"             # Java/Rust
  - "bin/*"              # 忽略所有bin目录下的内容
  - "*/temp*"            # 忽略所有临时目录
```
