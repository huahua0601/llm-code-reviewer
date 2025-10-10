"""
完整仓库代码扫描器
扫描整个代码仓库而不是仅基于git diff文件
"""
import os
import sys
import click
import logging
import yaml
import json
from datetime import datetime
from typing import List, Dict, Any, Optional
from rich.console import Console
from rich.panel import Panel
from rich.progress import Progress, TaskID
from rich.logging import RichHandler

# 禁用ChromaDB遥测
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
try:
    from disable_chromadb_telemetry import disable_chromadb_telemetry
    disable_chromadb_telemetry()
except ImportError:
    # 备用方案：直接设置环境变量
    os.environ["ANONYMIZED_TELEMETRY"] = "False"
    os.environ["CHROMA_TELEMETRY"] = "False"
    os.environ["CHROMA_TELEMETRY_IMPL"] = "none"
    os.environ["CHROMA_DISABLE_TELEMETRY"] = "1"
    os.environ["POSTHOG_DISABLED"] = "1"

from .formatter import format_review
from .indexer import CodeIndexer
from .models import CodeReviewRequest, CodeReviewCategory
from .ollama_client import OllamaClient
from .planner import CodeReviewer
from .worker import CodeReviewWorker

DEFAULT_PLANNER_MODEL = "gpt-oss:20b"
DEFAULT_WORKER_MODEL = "qwen2.5-coder:7b"
DEFAULT_EMBEDDING_MODEL = "nomic-embed-text:latest"
DEFAULT_OLLAMA_HOST = "http://localhost:11434"
DEFAULT_REPO_PATH = "./"
DEFAULT_OUTPUT_PATH = "./out"

console = Console()

def load_config_file(config_path: str) -> Dict[str, Any]:
    """
    加载配置文件（支持YAML和JSON格式）
    
    Args:
        config_path: 配置文件路径
        
    Returns:
        配置字典
        
    Raises:
        FileNotFoundError: 配置文件不存在
        ValueError: 配置文件格式错误
    """
    if not os.path.exists(config_path):
        raise FileNotFoundError(f"配置文件不存在: {config_path}")
    
    try:
        with open(config_path, 'r', encoding='utf-8') as f:
            content = f.read()
        
        # 根据文件扩展名确定格式
        _, ext = os.path.splitext(config_path.lower())
        
        if ext in ['.yaml', '.yml']:
            config = yaml.safe_load(content)
        elif ext == '.json':
            config = json.loads(content)
        else:
            # 尝试自动检测格式
            try:
                config = yaml.safe_load(content)
            except yaml.YAMLError:
                try:
                    config = json.loads(content)
                except json.JSONDecodeError:
                    raise ValueError(f"无法解析配置文件格式: {config_path}")
        
        if config is None:
            config = {}
        
        console.print(f"[green]成功加载配置文件: {config_path}")
        return config
        
    except Exception as e:
        raise ValueError(f"加载配置文件失败 {config_path}: {str(e)}")


def merge_config_with_args(config: Dict[str, Any], **kwargs) -> Dict[str, Any]:
    """
    合并配置文件参数和命令行参数
    命令行参数优先级更高
    
    Args:
        config: 从配置文件加载的配置
        **kwargs: 命令行参数
        
    Returns:
        合并后的配置字典
    """
    # 参数映射表（配置文件键名 -> 命令行参数名）
    param_mapping = {
        'repo': 'repo',
        'files': 'files',
        'categories': 'categories',
        'prompt': 'prompt',
        'ollama_host': 'ollama_host',
        'planner_model': 'planner_model',
        'worker_model': 'worker_model',
        'embedding_model': 'embedding_model',
        'reindex': 'reindex',
        'format': 'format_type',
        'log_file': 'log_file',
        'log_level': 'log_level',
        'output_dir': 'output_dir',
        'ignore_extensions': 'ignore_extensions',
        'ignored_dirs': 'ignored_dirs'
    }
    
    merged_config = {}
    
    # 首先使用配置文件中的值
    for config_key, cli_key in param_mapping.items():
        if config_key in config:
            merged_config[cli_key] = config[config_key]
    
    # 然后使用命令行参数覆盖（如果提供了的话）
    for key, value in kwargs.items():
        if value is not None:  # 只覆盖非None值
            merged_config[key] = value
    
    return merged_config


def setup_logging(log_file: str = None, log_level: str = "INFO"):
    """设置日志配置"""
    # 创建日志目录
    if log_file:
        log_dir = os.path.dirname(log_file)
        if log_dir and not os.path.exists(log_dir):
            os.makedirs(log_dir, exist_ok=True)
    
    # 配置日志格式
    log_format = "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
    
    # 获取根日志器
    logger = logging.getLogger()
    logger.setLevel(getattr(logging, log_level.upper()))
    
    # 清除现有处理器
    for handler in logger.handlers[:]:
        logger.removeHandler(handler)
    
    # 添加控制台处理器（使用Rich）
    console_handler = RichHandler(console=console, show_time=False, show_path=False)
    console_handler.setLevel(getattr(logging, log_level.upper()))
    logger.addHandler(console_handler)
    
    # 添加文件处理器（如果指定了日志文件）
    if log_file:
        file_handler = logging.FileHandler(log_file, encoding='utf-8')
        file_handler.setLevel(getattr(logging, log_level.upper()))
        file_formatter = logging.Formatter(log_format)
        file_handler.setFormatter(file_formatter)
        logger.addHandler(file_handler)
        
        console.print(f"[green]日志将保存到: {log_file}")
    
    return logger


class RepoScanner:
    """完整代码仓库扫描器"""
    
    def __init__(
        self,
        repo_path: str,
        ollama_host: str = DEFAULT_OLLAMA_HOST,
        planner_model: str = DEFAULT_PLANNER_MODEL,
        worker_model: str = DEFAULT_WORKER_MODEL,
        embedding_model: str = DEFAULT_EMBEDDING_MODEL,
        ignore_extensions: set = None,
        ignored_dirs: set = None
    ):
        self.repo_path = os.path.abspath(repo_path)
        self.ollama_client = OllamaClient(host=ollama_host)
        self.planner_model = planner_model
        self.worker_model = worker_model
        self.embedding_model = embedding_model
        self.logger = logging.getLogger(__name__)
        
        # 初始化代码索引器
        self.code_indexer = CodeIndexer(
            repo_path=self.repo_path,
            embedding_model=self.embedding_model,
            ollama_host=ollama_host,
            collection_name=os.path.basename(self.repo_path),
            ignore_extensions=ignore_extensions,
            ignored_dirs=ignored_dirs
        )
    
    def get_all_code_files(self) -> List[str]:
        """获取仓库中所有可扫描的代码文件"""
        code_files = []
        
        try:
            indexable_files = self.code_indexer._get_indexable_files()
            for abs_path, rel_path in indexable_files:
                code_files.append(rel_path)
        except Exception as e:
            # 如果indexer出错，使用备用的文件发现方法
            console.print(f"[yellow]警告: indexer出错 ({str(e)})，使用备用文件发现方法")
            code_files = self._get_files_fallback()
        
        return code_files
    
    def _get_files_fallback(self) -> List[str]:
        """备用的文件发现方法，不依赖indexer"""
        import fnmatch
        
        # 定义基本的忽略规则
        DEFAULT_IGNORE_EXTENSIONS = {
            '.jpg', '.jpeg', '.png', '.gif', '.bmp', '.svg', '.ico',
            '.pdf', '.doc', '.docx', '.ppt', '.pptx', '.xls', '.xlsx',
            '.zip', '.tar', '.gz', '.rar', '.7z',
            '.pyc', '.class', '.o', '.so', '.dll', '.exe',
            '.lock', '.log',
            '.chroma', '.DS_Store'
        }
        
        DEFAULT_IGNORED_DIRS = {'.git', '.chroma', '.venv', '__pycache__', 'node_modules'}
        
        # 获取配置的忽略规则（如果有的话）
        ignore_extensions = DEFAULT_IGNORE_EXTENSIONS.copy()
        ignored_dirs = DEFAULT_IGNORED_DIRS.copy()
        
        # 尝试从indexer获取配置的忽略规则
        try:
            if hasattr(self.code_indexer, 'ignore_extensions') and self.code_indexer.ignore_extensions:
                ignore_extensions.update(self.code_indexer.ignore_extensions)
            if hasattr(self.code_indexer, 'ignored_dirs') and self.code_indexer.ignored_dirs:
                ignored_dirs.update(self.code_indexer.ignored_dirs)
        except:
            pass
        
        code_files = []
        
        # 优先查找Python文件，因为这是代码审查工具
        for root, dirs, files in os.walk(self.repo_path):
            # 过滤目录（简化版本）
            dirs[:] = [d for d in dirs if d not in ignored_dirs and not d.startswith('.')]
            
            for file in files:
                # 优先包含代码文件
                _, ext = os.path.splitext(file)
                
                # 跳过明确要忽略的扩展名
                if ext.lower() in ignore_extensions:
                    continue
                
                file_path = os.path.join(root, file)
                relative_path = os.path.relpath(file_path, self.repo_path)
                
                # 简单检查是否在忽略目录中
                skip_file = False
                for ignored_dir in ignored_dirs:
                    if ignored_dir in relative_path or relative_path.startswith(ignored_dir + '/'):
                        skip_file = True
                        break
                
                # 特别包含常见的代码文件
                is_code_file = (
                    ext.lower() in {'.py', '.js', '.ts', '.java', '.cpp', '.c', '.h', '.go', '.rs', '.rb', '.php'} or
                    file in {'Dockerfile', 'Makefile', 'README.md', 'requirements.txt', 'package.json', 'pyproject.toml'}
                )
                
                if not skip_file and (is_code_file or ext.lower() in {'.txt', '.md', '.yml', '.yaml', '.json', '.toml'}):
                    code_files.append(relative_path)
        
        console.print(f"[blue]备用方法发现了 {len(code_files)} 个文件")
        
        # 如果还是很少，显示一些调试信息
        if len(code_files) < 50:
            console.print(f"[yellow]备用方法发现的文件较少，显示前10个:")
            for i, f in enumerate(code_files[:10]):
                console.print(f"[dim]  {i+1}. {f}")
        
        return code_files
    
    def create_virtual_diff(self, files_to_scan: List[str] = None) -> tuple[str, dict]:
        """
        创建一个虚拟的diff，包含指定文件的所有内容
        如果files_to_scan为None，则扫描所有文件
        
        Returns:
            tuple: (virtual_diff_content, stats_dict)
        """
        if files_to_scan is None:
            files_to_scan = self.get_all_code_files()
        
        virtual_diff = ""
        total_lines = 0
        processed_files = 0
        skipped_files = 0
        
        console.print(f"[blue]开始处理 {len(files_to_scan)} 个文件...")
        
        for file_path in files_to_scan:
            full_path = os.path.join(self.repo_path, file_path)
            
            if not os.path.exists(full_path):
                skipped_files += 1
                continue
                
            try:
                with open(full_path, 'r', encoding='utf-8', errors='ignore') as f:
                    content = f.read()
                
                # 跳过空文件
                if not content.strip():
                    skipped_files += 1
                    continue
                
                lines = content.splitlines()
                if not lines:
                    skipped_files += 1
                    continue
                
                # 统计信息
                file_line_count = len(lines)
                total_lines += file_line_count
                processed_files += 1
                
                # 每处理10个文件显示一次进度
                if processed_files % 10 == 0:
                    console.print(f"[dim]├─ 已处理 {processed_files} 个文件，累计 {total_lines:,} 行代码")
                
                # 创建更标准的git diff格式
                virtual_diff += f"diff --git a/{file_path} b/{file_path}\n"
                virtual_diff += f"new file mode 100644\n"
                virtual_diff += f"index 0000000..{'a' * 7}\n"
                virtual_diff += f"--- /dev/null\n"
                virtual_diff += f"+++ b/{file_path}\n"
                virtual_diff += f"@@ -0,0 +1,{file_line_count} @@\n"
                
                # 将每一行标记为新增行
                for line in lines:
                    virtual_diff += f"+{line}\n"
                    
            except Exception as e:
                console.print(f"[yellow]警告: 无法读取文件 {file_path}: {e}")
                skipped_files += 1
                continue
        
        # 统计信息
        stats = {
            'total_files_found': len(files_to_scan),
            'processed_files': processed_files,
            'skipped_files': skipped_files,
            'total_lines': total_lines
        }
        
        # 输出最终统计
        console.print(f"[green]✓ 文件处理完成:")
        console.print(f"[green]  ├─ 发现文件: {len(files_to_scan):,} 个")
        console.print(f"[green]  ├─ 成功处理: {processed_files:,} 个文件")
        console.print(f"[green]  ├─ 跳过文件: {skipped_files:,} 个")
        console.print(f"[green]  └─ 总代码行数: {total_lines:,} 行")
        
        return virtual_diff, stats
    
    def _create_simplified_diff(self, files_to_scan: List[str] = None) -> tuple[str, dict]:
        """
        创建简化的diff格式，避免unidiff解析问题
        
        Returns:
            tuple: (simplified_diff_content, stats_dict)
        """
        if files_to_scan is None:
            files_to_scan = self.get_all_code_files()
        
        simplified_diff = ""
        total_lines = 0
        processed_files = 0
        skipped_files = 0
        
        for file_path in files_to_scan:
            full_path = os.path.join(self.repo_path, file_path)
            
            if not os.path.exists(full_path):
                skipped_files += 1
                continue
                
            try:
                with open(full_path, 'r', encoding='utf-8', errors='ignore') as f:
                    content = f.read()
                
                # 跳过空文件
                if not content.strip():
                    skipped_files += 1
                    continue
                
                # 统计行数
                file_line_count = len(content.splitlines())
                total_lines += file_line_count
                processed_files += 1
                
                # 使用简单的文件标记格式
                simplified_diff += f"\n=== FILE: {file_path} ===\n"
                simplified_diff += content
                simplified_diff += f"\n=== END FILE: {file_path} ===\n"
                    
            except Exception as e:
                console.print(f"[yellow]警告: 无法读取文件 {file_path}: {e}")
                skipped_files += 1
                continue
        
        stats = {
            'total_files_found': len(files_to_scan),
            'processed_files': processed_files,
            'skipped_files': skipped_files,
            'total_lines': total_lines
        }
        
        return simplified_diff, stats
    
    def  scan_repository(
        self,
        system_prompt: str,
        files_to_scan: List[str] = None,
        categories: List[CodeReviewCategory] = None,
        reindex: bool = False
    ) -> str:
        """
        扫描整个代码仓库
        
        Args:
            system_prompt: 系统提示词
            files_to_scan: 要扫描的文件列表，如果为None则扫描所有文件
            categories: 要执行的审查类别，如果为None则执行所有类别
            reindex: 是否强制重新索引
            
        Returns:
            审查结果
        """
        # 获取要扫描的文件
        if files_to_scan is None:
            files_to_scan = self.get_all_code_files()
        
        console.print(f"[blue]发现 {len(files_to_scan)} 个文件需要扫描")
        # 创建虚拟diff
        console.print("[green]:arrows_counterclockwise: 正在创建虚拟diff文件...")
        virtual_diff, scan_stats = self.create_virtual_diff(files_to_scan)
        
        if not virtual_diff.strip():
            console.print("[yellow]没有找到可扫描的内容")
            # 返回空的CodeReviewResponse对象而不是字符串
            from .models import CodeReviewResponse
            return CodeReviewResponse(categories={}, summary="没有找到可扫描的内容")
        
        # 创建审查请求
        request = CodeReviewRequest(
            system_prompt=system_prompt,
            git_diff=virtual_diff,
            repo_path=self.repo_path,
            models={
                "planner": self.planner_model,
                "worker": self.worker_model,
                "embedding": self.embedding_model
            },
            reindex=reindex,
            is_repo_scan=True
        )
        
        # 执行代码审查
        console.print("[green]:arrows_counterclockwise: 正在执行代码审查...")
        
        if categories is None:
            categories = list(CodeReviewCategory)
        
        # 创建工作器
        workers = []
        for category in categories:
            worker = CodeReviewWorker(
                category=category,
                ollama_client=self.ollama_client,
                code_indexer=self.code_indexer,
                model=self.worker_model
            )
            workers.append(worker)
        
        # 验证虚拟diff格式
        try:
            from unidiff import PatchSet
            test_patch = PatchSet.from_string(virtual_diff)
            console.print(f"[green]虚拟diff格式验证通过，包含 {len(test_patch)} 个文件")
        except Exception as e:
            console.print(f"[red]虚拟diff格式错误: {str(e)}")
            console.print("[yellow]尝试使用简化的diff格式...")
            # 如果标准格式失败，使用简化格式
            virtual_diff, _ = self._create_simplified_diff(files_to_scan)

        # 执行审查
        worker_responses = []
        start_msg = f"开始执行代码审查，共 {len(workers)} 个审查类别"
        console.print(f"\n[bold green]{start_msg}[/bold green]")
        self.logger.info(start_msg)
        
        for i, worker in enumerate(workers, 1):
            try:
                # 显示当前审查进度
                progress_info = f"[{i}/{len(workers)}]"
                category_msg = f"{progress_info} 正在执行 {worker.category.value} 审查..."
                console.print(f"\n[bold blue]{category_msg}[/bold blue]")
                self.logger.info(f"开始 {worker.category.value} 审查 ({i}/{len(workers)})")
                
                console.print(f"[dim]├─ 审查类别: {worker.category.value}")
                console.print(f"[dim]├─ 使用模型: {self.worker_model}")
                console.print(f"[dim]└─ 进度: {i}/{len(workers)} ({i/len(workers)*100:.1f}%)")
                
                # 执行审查
                import time
                start_time = time.time()
                response = worker.review(virtual_diff, system_prompt, is_repo_scan=True)
                end_time = time.time()
                
                worker_responses.append(response)
                
                # 显示完成信息
                duration = end_time - start_time
                comments_count = len(response.comments) if response.comments else 0
                success_msg = f"{worker.category.value} 审查完成 (耗时: {duration:.1f}s, 发现: {comments_count} 条建议)"
                console.print(f"[green]✓ {success_msg}")
                self.logger.info(success_msg)
                
            except Exception as e:
                error_msg = f"工作器 {worker.category} 出错: {str(e)}"
                console.print(f"[red]✗ {error_msg}")
                self.logger.error(error_msg)
                
                # 添加更详细的错误信息
                if "Target without source" in str(e):
                    hint_msg = "提示: 这可能是diff格式问题，请检查虚拟diff生成逻辑"
                    console.print(f"[yellow]  {hint_msg}")
                    self.logger.warning(hint_msg)
                elif "list indices must be integers" in str(e):
                    hint_msg = "提示: 这可能是数据结构问题，请检查上下文检索逻辑"
                    console.print(f"[yellow]  {hint_msg}")
                    self.logger.warning(hint_msg)
        
        # 显示审查完成汇总
        console.print(f"\n[bold green]所有审查类别执行完成！[/bold green]")
        successful_reviews = len(worker_responses)
        total_comments = sum(len(response.comments) if response.comments else 0 for response in worker_responses)
        console.print(f"[dim]├─ 成功完成: {successful_reviews}/{len(workers)} 个审查类别")
        console.print(f"[dim]├─ 总计发现: {total_comments} 条审查建议")
        console.print(f"[dim]└─ 正在整理结果...")
        
        # 整理结果
        categories_dict = {}
        for response in worker_responses:
            categories_dict[response.category] = response.comments
        
        # 跳过摘要生成（仓库扫描模式下不需要）
        console.print(f"\n[bold blue]跳过摘要生成（仓库扫描模式）[/bold blue]")
        summary = "仓库扫描完成 - 已跳过摘要生成"
        
        from .models import CodeReviewResponse
        result = CodeReviewResponse(categories=categories_dict, summary=summary)
        
        return result


@click.command(help="""扫描整个代码仓库进行代码审查

支持通过配置文件指定参数，命令行参数优先级更高。

使用配置文件示例：
  python -m reviewer.repo_scanner --config config.yaml

混合使用示例：
  python -m reviewer.repo_scanner --config config.yaml --worker-model qwen2.5-coder:14b

配置文件格式请参考 config.example.yaml 或 config.example.json""")
@click.option("--config", help="配置文件路径（支持YAML和JSON格式），命令行参数优先级更高")
@click.option("--repo", default="./", help="仓库根目录路径")
@click.option("--files", help="要扫描的文件列表，用逗号分隔（可选，默认扫描所有文件）")
@click.option("--categories", help="要执行的审查类别，用逗号分隔（可选，默认执行所有类别）")
@click.option("--prompt", default="对这个代码仓库进行全面的代码审查，重点关注代码质量、最佳实践、潜在问题和改进建议。",
              help="系统提示词")
@click.option("--ollama-host", default=DEFAULT_OLLAMA_HOST, help="Ollama API主机地址")
@click.option("--planner-model", default=DEFAULT_PLANNER_MODEL, help="规划和摘要模型")
@click.option("--worker-model", default=DEFAULT_WORKER_MODEL, help="工作器模型")
@click.option("--embedding-model", default=DEFAULT_EMBEDDING_MODEL, help="嵌入模型")
@click.option("--reindex", is_flag=True, help="强制重新索引代码库")
@click.option("--format", "format_type", type=click.Choice(["markdown"]), 
              default="markdown", help="输出格式 (仅支持markdown)")
@click.option("--log-file", help="日志文件路径（可选，不指定则只输出到控制台）")
@click.option("--log-level", type=click.Choice(["DEBUG", "INFO", "WARNING", "ERROR"]), 
              default="INFO", help="日志级别")
@click.option("--output-dir", default=DEFAULT_OUTPUT_PATH, help="输出目录路径")
def main(config, repo, files, categories, prompt, ollama_host, planner_model, worker_model, 
         embedding_model, reindex, format_type, log_file, log_level, output_dir):
    """主函数"""
    
    # 加载配置文件（如果提供）
    config_data = {}
    if config:
        try:
            config_data = load_config_file(config)
        except (FileNotFoundError, ValueError) as e:
            console.print(f"[red]配置文件错误: {str(e)}")
            sys.exit(1)
    
    # 合并配置文件参数和命令行参数
    merged_params = merge_config_with_args(
        config_data,
        repo=repo if repo != "./" else None,  # 只有非默认值才覆盖
        files=files,
        categories=categories,
        prompt=prompt if prompt != "对这个代码仓库进行全面的代码审查，重点关注代码质量、最佳实践、潜在问题和改进建议。" else None,
        ollama_host=ollama_host if ollama_host != DEFAULT_OLLAMA_HOST else None,
        planner_model=planner_model if planner_model != DEFAULT_PLANNER_MODEL else None,
        worker_model=worker_model if worker_model != DEFAULT_WORKER_MODEL else None,
        embedding_model=embedding_model if embedding_model != DEFAULT_EMBEDDING_MODEL else None,
        reindex=reindex if reindex else None,
        format_type=format_type if format_type != "markdown" else None,
        log_file=log_file,
        log_level=log_level if log_level != "INFO" else None,
        output_dir=output_dir if output_dir != DEFAULT_OUTPUT_PATH else None
    )
    
    # 从合并后的配置中获取参数
    repo = merged_params.get('repo', "./")
    files = merged_params.get('files')
    categories = merged_params.get('categories')
    prompt = merged_params.get('prompt', "对这个代码仓库进行全面的代码审查，重点关注代码质量、最佳实践、潜在问题和改进建议。")
    ollama_host = merged_params.get('ollama_host', DEFAULT_OLLAMA_HOST)
    planner_model = merged_params.get('planner_model', DEFAULT_PLANNER_MODEL)
    worker_model = merged_params.get('worker_model', DEFAULT_WORKER_MODEL)
    embedding_model = merged_params.get('embedding_model', DEFAULT_EMBEDDING_MODEL)
    reindex = merged_params.get('reindex', False)
    format_type = merged_params.get('format_type', "markdown")
    log_file = merged_params.get('log_file')
    log_level = merged_params.get('log_level', "INFO")
    output_dir = merged_params.get('output_dir', DEFAULT_OUTPUT_PATH)
    
    # 处理索引配置
    ignore_extensions_config = merged_params.get('ignore_extensions')
    ignored_dirs_config = merged_params.get('ignored_dirs')
    
    # 转换为set类型（如果配置了的话）
    ignore_extensions = None
    if ignore_extensions_config:
        if isinstance(ignore_extensions_config, list):
            ignore_extensions = set(ignore_extensions_config)
        elif isinstance(ignore_extensions_config, str):
            ignore_extensions = set(ext.strip() for ext in ignore_extensions_config.split(','))
    
    ignored_dirs = None
    if ignored_dirs_config:
        if isinstance(ignored_dirs_config, list):
            ignored_dirs = set(ignored_dirs_config)
        elif isinstance(ignored_dirs_config, str):
            ignored_dirs = set(dir_name.strip() for dir_name in ignored_dirs_config.split(','))
    
    # 设置日志
    logger = setup_logging(log_file, log_level)
    if config:
        logger.info(f"使用配置文件: {config}")
    if log_file:
        logger.info("开始代码仓库扫描")
    else:
        logger.info("开始代码仓库扫描（仅控制台输出）")
    
    # 解析文件列表
    files_to_scan = None
    if files:
        if isinstance(files, list):
            files_to_scan = files
        else:
            files_to_scan = [f.strip() for f in files.split(",")]
    
    # 解析审查类别
    categories_to_run = None
    if categories:
        if isinstance(categories, list):
            category_names = categories
        else:
            category_names = [c.strip() for c in categories.split(",")]
        categories_to_run = []
        for name in category_names:
            try:
                category = CodeReviewCategory(name)
                categories_to_run.append(category)
            except ValueError:
                console.print(f"[yellow]警告: 未知的审查类别 '{name}'")
    
    # 显示配置信息
    config_info = (
        f"[bold]配置文件:[/bold] {config if config else '无（使用命令行参数）'}\n"
        f"[bold]仓库路径:[/bold] {repo}\n"
        f"[bold]扫描文件:[/bold] {'所有文件' if not files_to_scan else ', '.join(files_to_scan[:5]) + ('...' if len(files_to_scan) > 5 else '')}\n"
        f"[bold]审查类别:[/bold] {'所有类别' if not categories_to_run else ', '.join([c.value for c in categories_to_run])}\n"
        f"[bold]输出目录:[/bold] {output_dir}\n"
        f"[bold]输出格式:[/bold] {format_type}\n"
        f"[bold]Ollama主机:[/bold] {ollama_host}\n"
        f"[bold]规划模型:[/bold] {planner_model}\n"
        f"[bold]工作器模型:[/bold] {worker_model}\n"
        f"[bold]嵌入模型:[/bold] {embedding_model}"
    )
    
    console.print(Panel(config_info, title="仓库代码扫描配置", border_style="bold green"))
    
    # 创建扫描器
    scanner = RepoScanner(
        repo_path=repo,
        ollama_host=ollama_host,
        planner_model=planner_model,
        worker_model=worker_model,
        embedding_model=embedding_model,
        ignore_extensions=ignore_extensions,
        ignored_dirs=ignored_dirs
    )
    
    try:
        # 执行扫描
        result = scanner.scan_repository(
            system_prompt=prompt,
            files_to_scan=files_to_scan,
            categories=categories_to_run,
            reindex=reindex
        )
        print(result)
        print("=================================================")
        # # 格式化结果
        virtual_diff, _ = scanner.create_virtual_diff(files_to_scan)
        formatted_result = format_review(virtual_diff, result, format_type, repo)
        
        # 确定输出文件路径和扩展名
        os.makedirs(output_dir, exist_ok=True)
        output_file = os.path.join(output_dir, "repo_scan_results.md")
        
        # 将格式化结果输出到文件中
        with open(output_file, 'w', encoding='utf-8') as f:
            f.write(formatted_result)
        
        console.print(f"[green]:white_check_mark: 扫描结果已保存到 [bold]{output_file}[/bold]")
        
        # 保存结果到文件
        with open(output_file, 'w', encoding='utf-8') as f:
            f.write(formatted_result)
        
        console.print(f"[green]:white_check_mark: 扫描结果已保存到 [bold]{output_file}[/bold]")
        console.print(f"\n[dim]完整结果请查看文件: {output_file}[/dim]")
            
    except Exception as e:
        console.print(f"[red]扫描过程中出错: {str(e)}")
        import traceback
        console.print(f"[red]{traceback.format_exc()}")


if __name__ == "__main__":
    main()
