import click
from rich.console import Console
from rich.panel import Panel

from .formatter import format_review
from .indexer import CodeIndexer
from .models import CodeReviewRequest
from .ollama_client import OllamaClient
from .planner import CodeReviewer

DEFAULT_PLANNER_MODEL   = "llama3.1:latest"
DEFAULT_WORKER_MODEL    = "qwen2.5-coder:7b-instruct-q8_0"
DEFAULT_EMBEDDING_MODEL = "nomic-embed-text:latest"
DEFAULT_OLLAMA_HOST     = "http://localhost:11434"
DEFAULT_REPO_PATH       = "./"
OUTPUT_PATH = "./out"
OUTPUT_FILE = f"{OUTPUT_PATH}/test_results.md"

console = Console()

def review_code_with_rag(
    system_prompt: str,
    git_diff_path: str,
    repo_path: str = DEFAULT_REPO_PATH,
    ollama_host: str = DEFAULT_OLLAMA_HOST,
    planner_model: str = DEFAULT_PLANNER_MODEL,
    worker_model: str = DEFAULT_WORKER_MODEL,
    embedding_model: str = DEFAULT_EMBEDDING_MODEL,
    reindex: bool = False
) -> str:
    """
    Main function to run the code review process with RAG.
    Simplified synchronous version.
    
    Args:
        system_prompt: Instructions for the code review
        git_diff_path: Path to file containing git diff output
        repo_path: Path to the repository root
        ollama_host: Host of the Ollama API
        planner_model: Model to use for planning and summarization
        worker_model: Model to use for worker agents
        embedding_model: Model to use for embeddings
        reindex: Force reindexing of the codebase
        
    Returns:
        Formatted code review
    """
    with open(git_diff_path, 'r') as f:
        git_diff = f.read()
    
    request = CodeReviewRequest(
        system_prompt=system_prompt,
        git_diff=git_diff,
        repo_path=repo_path,
        models={
            "planner": planner_model,
            "worker": worker_model,
            "embedding": embedding_model
        },
        reindex=reindex
    )
    
    ollama_client = OllamaClient(host=ollama_host)
    
    code_indexer = CodeIndexer(
        repo_path=request.repo_path,
        embedding_model=request.models.get("embedding", embedding_model),
        ollama_host=ollama_host
    )

    console.print(f"[green]:arrows_counterclockwise: Indexing repository at {request.repo_path}")
    code_indexer.index_repository(force_reindex=request.reindex)
    
    planner = CodeReviewer(
        ollama_client=ollama_client,
        code_indexer=code_indexer,
        planner_model=planner_model,
        worker_model=worker_model
    )

    console.print("[green]:arrows_counterclockwise: Running Code Review")
    response = planner.plan_and_execute(request)
    
    return response, git_diff


@click.command(help="RAG-Enhanced Code Review Agent using Ollama (Simplified Version)")
@click.option("--diff", required=False, help="Path to git diff file")
@click.option("--repo", default="./", help="Path to repository root")
@click.option("--scan-repo", is_flag=True, help="Scan entire repository instead of diff")
@click.option("--files", help="Comma-separated list of files to scan (only when --scan-repo is used)")
@click.option("--prompt", default="Perform a thorough code review focusing on best practices and code quality.", 
              help="System prompt for the code review")
@click.option("--ollama-host", default="http://localhost:11434", help="Host of the Ollama API")
@click.option("--planner-model", default=DEFAULT_PLANNER_MODEL, help="Model to use for planning and summarization")
@click.option("--worker-model", default=DEFAULT_WORKER_MODEL, help="Model to use for worker agents")
@click.option("--embedding-model", default=DEFAULT_EMBEDDING_MODEL, help="Model to use for embeddings")
@click.option("--reindex", is_flag=True, help="Force reindexing of the codebase")
@click.option("--format", "format_type", type=click.Choice(["markdown"]), default="markdown",
              help="Output format (only markdown is supported)")
def main(diff, repo, scan_repo, files, prompt, ollama_host, planner_model, worker_model, embedding_model, reindex, format_type):
    if not diff and not scan_repo:
        console.print("[red]错误: 必须指定 --diff 或 --scan-repo 选项之一[/red]")
        return
    
    if scan_repo:
        # 使用仓库扫描模式
        from .repo_scanner import RepoScanner
        
        files_to_scan = None
        if files:
            files_to_scan = [f.strip() for f in files.split(",")]
        
        config_info = (
            f"[bold]扫描模式:[/bold] 全仓库扫描\n"
            f"[bold]仓库路径:[/bold] {repo}\n"
            f"[bold]扫描文件:[/bold] {'所有文件' if not files_to_scan else ', '.join(files_to_scan[:3]) + ('...' if len(files_to_scan) > 3 else '')}\n"
            f"[bold]Ollama主机:[/bold] {ollama_host}\n"
            f"[bold]规划模型:[/bold] {planner_model}\n"
            f"[bold]工作器模型:[/bold] {worker_model}\n"
            f"[bold]嵌入模型:[/bold] {embedding_model}"
        )
        
        console.print(Panel(config_info, title="运行RAG增强的代码仓库扫描", border_style="bold green"))
        
        # 创建扫描器并执行扫描
        scanner = RepoScanner(
            repo_path=repo,
            ollama_host=ollama_host,
            planner_model=planner_model,
            worker_model=worker_model,
            embedding_model=embedding_model
        )
        
        result = scanner.scan_repository(
            system_prompt=prompt,
            files_to_scan=files_to_scan,
            reindex=reindex
        )
        
        # 创建虚拟diff用于格式化
        virtual_diff = scanner.create_virtual_diff(files_to_scan)
        formatted_result = format_review(virtual_diff, result, format_type, repo)
        
    else:
        # 使用传统的diff模式
        config_info = (
            f"[bold]扫描模式:[/bold] Diff文件扫描\n"
            f"[bold]Diff来源:[/bold] {diff}\n"
            f"[bold]仓库路径:[/bold] {repo}\n"
            f"[bold]Ollama主机:[/bold] {ollama_host}\n"
            f"[bold]规划模型:[/bold] {planner_model}\n"
            f"[bold]工作器模型:[/bold] {worker_model}\n"
            f"[bold]嵌入模型:[/bold] {embedding_model}"
        )

        console.print(Panel(config_info, title="运行RAG增强的代码审查", border_style="bold green"))

        result, git_diff = review_code_with_rag(
            system_prompt=prompt,
            git_diff_path=diff,
            repo_path=repo,
            ollama_host=ollama_host,
            planner_model=planner_model,
            worker_model=worker_model,
            embedding_model=embedding_model,
            reindex=reindex
        )
        
        formatted_result = format_review(git_diff, result, format_type, repo)

    # 显示结果
    console.print("代码审查结果", style="bold blue underline")
    console.print(formatted_result)


if __name__ == "__main__":
    main()
