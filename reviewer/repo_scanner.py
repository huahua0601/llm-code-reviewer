"""
完整仓库代码扫描器
扫描整个代码仓库而不是仅基于git diff文件
"""
import os
import click
import logging
from datetime import datetime
from typing import List, Dict, Any
from rich.console import Console
from rich.panel import Panel
from rich.progress import Progress, TaskID
from rich.logging import RichHandler

from .formatter import format_review
from .indexer import CodeIndexer
from .models import CodeReviewRequest, CodeReviewCategory
from .ollama_client import OllamaClient
from .planner import CodeReviewer
from .worker import CodeReviewWorker

DEFAULT_PLANNER_MODEL = "llama3.1:latest"
DEFAULT_WORKER_MODEL = "qwen2.5-coder:7b-instruct-q8_0"
DEFAULT_EMBEDDING_MODEL = "nomic-embed-text:latest"
DEFAULT_OLLAMA_HOST = "http://localhost:11434"
DEFAULT_REPO_PATH = "./"
DEFAULT_OUTPUT_PATH = "./out"

console = Console()

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
        embedding_model: str = DEFAULT_EMBEDDING_MODEL
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
            ollama_host=ollama_host
        )
    
    def get_all_code_files(self) -> List[str]:
        """获取仓库中所有可扫描的代码文件"""
        code_files = []
        indexable_files = self.code_indexer._get_indexable_files()
        
        for abs_path, rel_path in indexable_files:
            code_files.append(rel_path)
        
        return code_files
    
    def create_virtual_diff(self, files_to_scan: List[str] = None) -> str:
        """
        创建一个虚拟的diff，包含指定文件的所有内容
        如果files_to_scan为None，则扫描所有文件
        """
        if files_to_scan is None:
            files_to_scan = self.get_all_code_files()
        
        virtual_diff = ""
        
        for file_path in files_to_scan:
            full_path = os.path.join(self.repo_path, file_path)
            
            if not os.path.exists(full_path):
                continue
                
            try:
                with open(full_path, 'r', encoding='utf-8', errors='ignore') as f:
                    content = f.read()
                
                # 跳过空文件
                if not content.strip():
                    continue
                
                lines = content.splitlines()
                if not lines:
                    continue
                
                # 创建更标准的git diff格式
                virtual_diff += f"diff --git a/{file_path} b/{file_path}\n"
                virtual_diff += f"new file mode 100644\n"
                virtual_diff += f"index 0000000..{'a' * 7}\n"
                virtual_diff += f"--- /dev/null\n"
                virtual_diff += f"+++ b/{file_path}\n"
                virtual_diff += f"@@ -0,0 +1,{len(lines)} @@\n"
                
                # 将每一行标记为新增行
                for line in lines:
                    virtual_diff += f"+{line}\n"
                    
            except Exception as e:
                console.print(f"[yellow]警告: 无法读取文件 {file_path}: {e}")
                continue
        
        return virtual_diff
    
    def _create_simplified_diff(self, files_to_scan: List[str] = None) -> str:
        """
        创建简化的diff格式，避免unidiff解析问题
        """
        if files_to_scan is None:
            files_to_scan = self.get_all_code_files()
        
        simplified_diff = ""
        
        for file_path in files_to_scan:
            full_path = os.path.join(self.repo_path, file_path)
            
            if not os.path.exists(full_path):
                continue
                
            try:
                with open(full_path, 'r', encoding='utf-8', errors='ignore') as f:
                    content = f.read()
                
                # 跳过空文件
                if not content.strip():
                    continue
                
                # 使用简单的文件标记格式
                simplified_diff += f"\n=== FILE: {file_path} ===\n"
                simplified_diff += content
                simplified_diff += f"\n=== END FILE: {file_path} ===\n"
                    
            except Exception as e:
                console.print(f"[yellow]警告: 无法读取文件 {file_path}: {e}")
                continue
        
        return simplified_diff
    
    def scan_repository(
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
        # 索引仓库
        console.print(f"[green]:arrows_counterclockwise: 正在索引仓库 {self.repo_path}")
        self.code_indexer.index_repository(force_reindex=reindex)
        
        # 获取要扫描的文件
        if files_to_scan is None:
            files_to_scan = self.get_all_code_files()
        
        console.print(f"[blue]发现 {len(files_to_scan)} 个文件需要扫描")
        
        # 创建虚拟diff
        console.print("[green]:arrows_counterclockwise: 正在创建虚拟diff文件...")
        virtual_diff = self.create_virtual_diff(files_to_scan)
        
        if not virtual_diff.strip():
            console.print("[yellow]没有找到可扫描的内容")
            return "没有找到可扫描的内容"
        
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
            reindex=reindex
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
            virtual_diff = self._create_simplified_diff(files_to_scan)

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
                response = worker.review(virtual_diff, system_prompt)
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
        
        # 生成摘要
        console.print(f"\n[bold blue]正在生成审查摘要...[/bold blue]")
        console.print(f"[dim]├─ 使用模型: {self.planner_model}")
        try:
            import time
            summary_start = time.time()
            
            planner = CodeReviewer(
                ollama_client=self.ollama_client,
                code_indexer=self.code_indexer,
                planner_model=self.planner_model,
                worker_model=self.worker_model
            )
            summary = planner._generate_changelog(virtual_diff, request)
            
            summary_end = time.time()
            console.print(f"[green]✓ 摘要生成完成 (耗时: {summary_end - summary_start:.1f}s)")
        except Exception as e:
            console.print(f"[red]✗ 生成摘要时出错: {str(e)}")
            summary = "无法生成摘要"
        
        from .models import CodeReviewResponse
        result = CodeReviewResponse(categories=categories_dict, summary=summary)
        
        return result


@click.command(help="扫描整个代码仓库进行代码审查")
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
@click.option("--format", "format_type", type=click.Choice(["markdown", "html", "comprehensive_html"]), 
              default="markdown", help="输出格式")
@click.option("--log-file", help="日志文件路径（可选，不指定则只输出到控制台）")
@click.option("--log-level", type=click.Choice(["DEBUG", "INFO", "WARNING", "ERROR"]), 
              default="INFO", help="日志级别")
@click.option("--output-dir", default=DEFAULT_OUTPUT_PATH, help="输出目录路径")
def main(repo, files, categories, prompt, ollama_host, planner_model, worker_model, 
         embedding_model, reindex, format_type, log_file, log_level, output_dir):
    """主函数"""
    
    # 设置日志
    logger = setup_logging(log_file, log_level)
    if log_file:
        logger.info("开始代码仓库扫描")
    else:
        logger.info("开始代码仓库扫描（仅控制台输出）")
    
    # 解析文件列表
    files_to_scan = None
    if files:
        files_to_scan = [f.strip() for f in files.split(",")]
    
    # 解析审查类别
    categories_to_run = None
    if categories:
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
        embedding_model=embedding_model
    )
    
    try:
        # 执行扫描
        result = scanner.scan_repository(
            system_prompt=prompt,
            files_to_scan=files_to_scan,
            categories=categories_to_run,
            reindex=reindex
        )
        
        # 格式化结果
        virtual_diff = scanner.create_virtual_diff(files_to_scan)
        formatted_result = format_review(virtual_diff, result, format_type, repo)
        
        # 确定输出文件路径和扩展名
        os.makedirs(output_dir, exist_ok=True)
        
        # 根据格式确定文件扩展名
        if format_type == "markdown":
            output_file = os.path.join(output_dir, "repo_scan_results.md")
        elif format_type == "html":
            output_file = os.path.join(output_dir, "repo_scan_results.html")
        else:  # comprehensive_html
            output_file = os.path.join(output_dir, "repo_scan_results.html")
        
        # 保存结果到文件
        with open(output_file, 'w', encoding='utf-8') as f:
            f.write(formatted_result)
        
        console.print(f"[green]:white_check_mark: 扫描结果已保存到 [bold]{output_file}[/bold]")
        
        # 如果是markdown格式，同时在控制台显示（可选）
        if format_type == "markdown":
            console.print("\n" + "="*50)
            console.print("仓库扫描结果预览", style="bold blue underline")
            console.print("="*50)
            # 只显示前1000个字符作为预览
            preview = formatted_result[:1000] + "..." if len(formatted_result) > 1000 else formatted_result
            console.print(preview)
            console.print(f"\n[dim]完整结果请查看文件: {output_file}[/dim]")
            
    except Exception as e:
        console.print(f"[red]扫描过程中出错: {str(e)}")
        import traceback
        console.print(f"[red]{traceback.format_exc()}")


if __name__ == "__main__":
    main()
