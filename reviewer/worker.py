import json
import os
import re
import logging
from typing import List

from rich.console import Console

from .context_retriever import ContextRetriever
from .diff_parser import extract_added_lines
from .indexer import CodeIndexer
from .models import CodeReviewCategory, CodeReviewComment, WorkerResponse
from .ollama_client import OllamaClient
from .prompts import (
    CATEGORY_TO_SUBCATEGORIES,
    CODE_REVIEW_PROMPTS,
    TARGETED_REVIEW_PROMPTS,
    worker_system_prompt,
    worker_user_prompt,
)
from .reranker import ContextReranker

MAX_CODE_LINES = 100
MAX_CONTEXT_DOCS = 3
MAX_REPO_SCAN_LINES = 1000  # 仓库扫描模式下的最大行数限制

class CodeReviewWorker:
    def __init__(self, 
                category: CodeReviewCategory, 
                ollama_client: OllamaClient,
                code_indexer: CodeIndexer,
                model: str,
                context_retriever: ContextRetriever = None):
        self.category = category
        self.prompt_template = CODE_REVIEW_PROMPTS.get(category, "")
        self.ollama_client = ollama_client
        self.code_indexer = code_indexer
        self.model = model
        self.console = Console()
        self.logger = logging.getLogger(__name__)
        self.context_retriever = context_retriever or ContextRetriever(code_indexer)

    def get_sampled_code_lines(self, git_diff, is_repo_scan=None):
        """
        Format the files and code lines for the LLM
        
        Args:
            git_diff: Git diff content
            is_repo_scan: If True, include all code without sampling. If None, auto-detect.
        """
        # Auto-detect repo scan mode if not explicitly specified
        if is_repo_scan is None:
            # Check for repo scan indicators
            from .diff_parser import extract_modified_files
            modified_files = extract_modified_files(git_diff)
            is_repo_scan = len(modified_files) > 10 or "new file mode 100644" in git_diff
        
        # Format the files and code lines for the LLM
        formatted_code_lines = []
        for file_name, line_number, code_line in extract_added_lines(git_diff):
            prefix = '+ ' 
            # Ensure line_number is valid before formatting
            if line_number is not None and line_number > 0:
                 formatted_code_lines.append(f"{file_name}:{line_number}: {prefix}{code_line.rstrip()}")

        if is_repo_scan:
            # For repo scanning, include more code but still have a reasonable limit
           return formatted_code_lines
        else:
            # For regular diff review, apply sampling if needed
            if len(formatted_code_lines) > MAX_CODE_LINES:
                # Take a sample of lines around the diff to maintain context
                beginning = formatted_code_lines[:MAX_CODE_LINES//2]
                end = formatted_code_lines[-MAX_CODE_LINES//2:]
                middle_size = MAX_CODE_LINES - len(beginning) - len(end)

                if middle_size > 0 and len(formatted_code_lines) > (len(beginning) + len(end)):
                    middle_start = len(beginning)
                    middle_end = len(formatted_code_lines) - len(end)
                    step = (middle_end - middle_start) / (middle_size + 1)
                    
                    middle = []
                    for i in range(middle_size):
                        idx = int(middle_start + step * (i + 1))
                        if idx < middle_end:
                            middle.append(formatted_code_lines[idx])
                    
                    formatted_code_lines = beginning + middle + end
                else:
                    formatted_code_lines = beginning + end
                    
                formatted_code_lines.insert(len(beginning), f"... (sampled {MAX_CODE_LINES} lines from {len(formatted_code_lines)} total) ...")
                self.console.print(f"[dim]  ├─ 差异审查模式：采样了 {MAX_CODE_LINES} 行代码")
            else:
                self.console.print(f"[dim]  ├─ 差异审查模式：处理全部 {len(formatted_code_lines)} 行代码")
        
        return formatted_code_lines

    def format_context(self, context_docs):
        """
        Format the context for the LLM
        """
        if not context_docs:
            return ""
        
        context_text = "CONTEXT:\n\n"
        for i, doc in enumerate(context_docs):
            context_text += f"--- {doc['file_path']} (lines {doc['start_line']}-{doc['end_line']}) ---\n"
            context_text += doc['content'] + "\n\n"

        return context_text

    def get_prompt(self, code_to_review, context_text):
        subcategories = CATEGORY_TO_SUBCATEGORIES.get(self.category, [])
        # If no subcategories defined, fall back to the original prompt
        targeted_prompt = self.prompt_template.strip()

        if subcategories:
            # Combine the targeted prompts for relevant subcategories
            targeted_prompt = f"Focus specifically on these issues for {self.category}:\n\n"
            for subcategory in subcategories:
                if subcategory in TARGETED_REVIEW_PROMPTS:
                    targeted_prompt += TARGETED_REVIEW_PROMPTS[subcategory].strip() + "\n\n"
        
        return [
            {
                "role": "system",
                "content": worker_system_prompt(self.category, targeted_prompt)
            },
            {
                "role": "user",
                "content": worker_user_prompt(self.category, code_to_review, context_text)
            }
        ]
    
    def parse_comment(self, comment_data):
        comment_text = ""
        
        if "comment" in comment_data:
            comment_text = comment_data["comment"]
        elif "issue" in comment_data and "suggestion" in comment_data:
            comment_text = f"{comment_data['issue']} {comment_data['suggestion']}"
        elif "description" in comment_data and "improvement" in comment_data:
            comment_text = f"{comment_data['description']} {comment_data['improvement']}"
        elif "issue" in comment_data:
            comment_text = comment_data["issue"]
        elif "description" in comment_data:
            comment_text = comment_data["description"]
        elif "problem" in comment_data:
            comment_text = comment_data["problem"]
        elif "message" in comment_data:
            comment_text = comment_data["message"]
        else:
            # Skip if we couldn't extract a comment
            print(f"Warning: No comment text found in comment_data {comment_data}")
            return None

        file_name = None
        raw_line_number = None
        if "file_name" in comment_data:
            file_name = comment_data["file_name"]
        elif "file" in comment_data:
            file_name = comment_data["file"]
        elif "filename" in comment_data:
            file_name = comment_data["filename"]
        else:
            file_name = None

        # Normalize file path (remove b/ prefix if present)
        if file_name and file_name.startswith('b/'):
            file_name = file_name[2:]
        

        line_number = None
        if "line_number" in comment_data:
            raw_line_number = comment_data["line_number"]
        elif "line" in comment_data:
            raw_line_number = comment_data["line"]

        if raw_line_number is not None:
            try:
                line_number = int(raw_line_number)
            except (ValueError, TypeError):
                print(f"Warning: Could not convert line number '{raw_line_number}' to int for file {file_name}")
                line_number = None # Set to None if conversion fails

        # Parse severity level
        severity = None
        if "severity" in comment_data:
            from .models import SeverityLevel
            severity_str = comment_data["severity"]
            try:
                severity = SeverityLevel(severity_str)
            except ValueError:
                print(f"Warning: Invalid severity level '{severity_str}', defaulting to Medium")
                severity = SeverityLevel.MEDIUM
        else:
            # Default severity based on category if not specified
            from .models import SeverityLevel
            severity = self._get_default_severity_for_category()
            
        # 智能调整严重程度基于评论内容
        severity = self._adjust_severity_based_on_content(comment_text, severity)

        # 提取示例代码
        example_code = None
        if "example_code" in comment_data:
            example_code = comment_data["example_code"]
        elif "example" in comment_data:
            example_code = comment_data["example"]
        elif "suggested_code" in comment_data:
            example_code = comment_data["suggested_code"]

        # Return None if comment is invalid or lacks essential info
        if not comment_text or not file_name:
            print(f"Warning: Skipping comment due to missing text or file name: {comment_data}")
            return None

        return CodeReviewComment(
            category=self.category,
            file_name=file_name,
            line_number=line_number,
            comment=comment_text,
            severity=severity,
            example_code=example_code
        )
    
    def parse_llm_response(self, response) -> List[CodeReviewComment]:
        """Parse the LLM response to extract code review comments with enhanced error handling."""
        if not response or not str(response).strip():
            self.logger.warning("Empty LLM response received")
            from .models import SeverityLevel
            return [
                CodeReviewComment(
                    category=self.category,
                    comment="LLM returned empty response. Please try again.",
                    severity=SeverityLevel.MEDIUM
                )
            ]
        
        response_str = str(response).strip()
        self.console.print(f"[dim]  ├─ 解析LLM响应 ({len(response_str)} 字符)")
        
        # 尝试多种解析策略
        comments = []

        # 检查是否为空数组响应
        if self._is_empty_json_array(response_str):
            self.console.print(f"[dim]  ├─ 检测到空数组响应，无需解析")
            return comments
        
        # 策略1: 尝试提取JSON并解析
        try:
            json_match = re.search(r'\[\s*{.*}\s*\]', response_str, re.DOTALL)
            if json_match:
                json_str = json_match.group(0)
                self.console.print(f"[dim]  ├─ 找到JSON格式数据")
            else:
                json_str = response_str
            
            comments_data = json.loads(json_str)
            comments = [self.parse_comment(item) for item in comments_data]
            comments = [c for c in comments if c is not None]
            
            if comments:
                self.console.print(f"[dim]  ├─ JSON解析成功，提取到 {len(comments)} 条建议")
                return comments
                
        except json.JSONDecodeError as e:
            self.console.print(f"[dim]  ├─ JSON解析失败: {str(e)}")
            self.console.print(f"[dim]  ├─ 原始响应内容:")
            # 打印原始内容，限制长度避免过长输出
            display_content = json_str[:1000] + "..." if len(json_str) > 1000 else json_str
            self.console.print(f"[yellow]{display_content}[/yellow]")
            self.logger.debug(f"JSON parsing failed: {str(e)}")
            self.logger.debug(f"Raw response content: {json_str}")
        except Exception as e:
            self.logger.warning(f"Unexpected error during JSON parsing: {str(e)}")
        
        # 策略2: 智能文本解析 - 查找结构化内容
        try:
            comments = self._parse_text_response(response_str)
            if comments:
                self.console.print(f"[dim]  ├─ 文本解析成功，提取到 {len(comments)} 条建议")
                return comments
        except Exception as e:
            self.logger.warning(f"Text parsing failed: {str(e)}")
        
        # 策略3: 创建基于整体内容的单个评论
        try:
            comment = self._create_summary_comment(response_str)
            self.console.print(f"[dim]  ├─ 创建摘要评论")
            return [comment]
        except Exception as e:
            self.logger.error(f"Failed to create summary comment: {str(e)}")
        
        # 最后的fallback
        from .models import SeverityLevel
        return [
            CodeReviewComment(
                category=self.category,
                comment="Unable to parse LLM response. Please check the model output format.",
                severity=SeverityLevel.MEDIUM
            )
        ]
    
    def _parse_text_response(self, response_content):
        """尝试从自然语言响应中提取结构化建议"""
        comments = []
        from .models import SeverityLevel
        
        # 按段落分割
        paragraphs = [p.strip() for p in response_content.split('\n\n') if p.strip()]
        
        for paragraph in paragraphs:
            # 查找包含代码审查相关关键词的段落
            if any(keyword in paragraph.lower() for keyword in [
                'line', '行', 'file', '文件', 'function', '函数', 
                'error', '错误', 'issue', '问题', 'warning', '警告',
                'security', '安全', 'bug', 'performance', '性能'
            ]):
                # 提取可能的严重级别
                severity = SeverityLevel.MEDIUM
                if any(word in paragraph.lower() for word in ['critical', '严重', 'high', '高', 'security', '安全']):
                    severity = SeverityLevel.HIGH
                elif any(word in paragraph.lower() for word in ['low', '低', 'minor', '轻微', 'suggestion', '建议']):
                    severity = SeverityLevel.LOW
                
                # 限制评论长度
                comment_text = paragraph[:500] + "..." if len(paragraph) > 500 else paragraph
                
                comments.append(
                    CodeReviewComment(
                        category=self.category,
                        comment=comment_text,
                        severity=severity
                    )
                )
        
        return comments[:5]  # 最多返回5条建议
    
    def _create_summary_comment(self, response_content):
        """基于整体响应内容创建摘要评论"""
        from .models import SeverityLevel
        
        # 截取前1000个字符作为摘要
        summary = response_content[:1000] + "..." if len(response_content) > 1000 else response_content
        
        # 检测严重级别
        severity = SeverityLevel.LOW
        if any(word in response_content.lower() for word in [
            'error', 'critical', 'security', 'vulnerability', 
            '错误', '严重', '安全', '漏洞'
        ]):
            severity = SeverityLevel.HIGH
        elif any(word in response_content.lower() for word in [
            'warning', 'issue', 'problem', 'concern',
            '警告', '问题', '关注'
        ]):
            severity = SeverityLevel.MEDIUM
        
        return CodeReviewComment(
            category=self.category,
            comment=f"{self.category.value} 审查摘要: {summary}",
            severity=severity
        )
    
    def _is_empty_json_array(self, response_str: str) -> bool:
        """
        检查响应是否为空的JSON数组
        处理各种可能的格式：[], [ ], [\n], 等
        """
        if not response_str or not response_str.strip():
            return True
            
        # 移除所有空白字符进行检查
        cleaned = re.sub(r'\s+', '', response_str.strip())
        
        # 检查是否为空数组
        if cleaned == "[]":
            return True
        
        # 检查是否为包含在代码块中的空数组
        code_block_patterns = [
            r'```(?:json)?\s*\[\s*\]\s*```',  # ```json [] ``` 或 ``` [] ```
            r'`\[\s*\]`',  # `[]`
        ]
        
        for pattern in code_block_patterns:
            if re.match(pattern, response_str.strip(), re.DOTALL | re.IGNORECASE):
                return True
        
        # 尝试解析为JSON来验证
        try:
            import json
            parsed = json.loads(response_str.strip())
            return isinstance(parsed, list) and len(parsed) == 0
        except (json.JSONDecodeError, ValueError):
            pass
        
        # 检查是否只包含空数组和其他非内容文本（如解释性文字）
        # 提取所有可能的JSON数组
        json_arrays = re.findall(r'\[.*?\]', response_str, re.DOTALL)
        if len(json_arrays) == 1:
            try:
                import json
                parsed = json.loads(json_arrays[0])
                return isinstance(parsed, list) and len(parsed) == 0
            except (json.JSONDecodeError, ValueError):
                pass
        
        return False
    
    def _get_default_severity_for_category(self):
        """根据审查类别分配默认严重程度级别"""
        from .models import SeverityLevel, CodeReviewCategory
        
        # 基于类别的默认严重程度映射
        # 注意：LLM应该根据具体问题覆盖这些默认值
        severity_mapping = {
            CodeReviewCategory.FUNCTIONALITY: SeverityLevel.CRITICAL,
            CodeReviewCategory.ROBUSTNESS: SeverityLevel.HIGH,
            CodeReviewCategory.TESTS: SeverityLevel.HIGH,
            CodeReviewCategory.DESIGN: SeverityLevel.HIGH,
            CodeReviewCategory.ABSTRACTIONS: SeverityLevel.MEDIUM,
            CodeReviewCategory.CONSISTENCY: SeverityLevel.MEDIUM,
            CodeReviewCategory.READABILITY: SeverityLevel.LOW,      # 降低默认级别
            CodeReviewCategory.CODING_STYLE: SeverityLevel.LOW,     # 降低默认级别
            CodeReviewCategory.NAMING: SeverityLevel.LOW,
        }
        
        return severity_mapping.get(self.category, SeverityLevel.MEDIUM)
    
    def _adjust_severity_based_on_content(self, comment_text: str, default_severity):
        """根据评论内容智能调整严重程度"""
        from .models import SeverityLevel, CodeReviewCategory
        
        comment_lower = comment_text.lower()
        
        # 严重问题关键词 -> Critical
        critical_keywords = [
            'security', 'vulnerability', 'sql injection', 'xss', 'csrf',
            'memory leak', 'null pointer', 'crash', 'data loss', 'corruption'
        ]
        
        # 高优先级关键词 -> High  
        high_keywords = [
            'performance', 'bottleneck', 'slow', 'inefficient', 'resource leak',
            'missing error handling', 'exception not handled', 'major design flaw'
        ]
        
        # 低优先级关键词 -> Low (特别针对Naming和Readability)
        low_keywords = [
            'variable name', 'method name', 'rename', 'more descriptive',
            'consider renaming', 'cosmetic', 'minor', 'suggestion'
        ]
        
        # 检查严重问题
        if any(keyword in comment_lower for keyword in critical_keywords):
            return SeverityLevel.CRITICAL
            
        # 检查高优先级问题
        if any(keyword in comment_lower for keyword in high_keywords):
            return SeverityLevel.HIGH
            
        # 对于Naming和Readability类别，检查是否应该降级为Low
        if self.category in [CodeReviewCategory.NAMING, CodeReviewCategory.READABILITY]:
            if any(keyword in comment_lower for keyword in low_keywords):
                return SeverityLevel.LOW
            # 对于这些类别，最高也只是Medium，除非有特殊关键词
            if default_severity == SeverityLevel.HIGH:
                return SeverityLevel.MEDIUM
                
        return default_severity

    
    def review(self, git_diff: str, system_prompt: str, is_repo_scan: bool = False) -> WorkerResponse:
        """
        Perform the code review for a specific category using Ollama and RAG context.
        
        Args:
            git_diff: The git diff content to review
            system_prompt: The system prompt for the review
            is_repo_scan: Whether this is a repository scan mode (vs diff review mode)
        """
        if is_repo_scan:
            return self._review_repo_scan(git_diff, system_prompt)
        else:
            return self._review_diff(git_diff, system_prompt)
    
    def _review_diff(self, git_diff: str, system_prompt: str) -> WorkerResponse:
        """普通diff审查模式 - 保持原有逻辑"""
        self.console.print(f"[dim]  ├─ 正在提取代码行...")
        formatted_code_lines = self.get_sampled_code_lines(git_diff)
        code_to_review = "\n".join(formatted_code_lines)
        self.console.print(f"[dim]  ├─ 已提取 {len(formatted_code_lines)} 行代码")
        
        self.console.print(f"[dim]  ├─ 正在检索相关上下文...")
        context_text = self.context_retriever.get_diff_context(git_diff)
        self.console.print(f"[dim]  ├─ 找到 {len(context_text) if context_text else 0} 个上下文文档")
        
        self.console.print(f"[dim]  ├─ 正在重新排序上下文...")
        context_text = ContextReranker().rank(code_to_review, context_text)
        context_str = self.format_context(context_text)
        
        return self._perform_single_review(code_to_review, context_str)
    
    def _review_repo_scan(self, git_diff: str, system_prompt: str) -> WorkerResponse:
        """仓库扫描模式 - 按文件为单位进行扫描"""
        self.console.print(f"[dim]  ├─ 仓库扫描模式：按文件为单位进行扫描...")
        
        # 解析 git_diff 获取文件列表
        files_to_scan = self._extract_files_from_diff(git_diff)
        self.console.print(f"[dim]  ├─ 发现 {len(files_to_scan)} 个文件需要扫描")
        
        all_comments = []
        
        for i, file_path in enumerate(files_to_scan, 1):
            self.console.print(f"[dim]  ├─ 正在扫描文件 {i}/{len(files_to_scan)}: {file_path}")
            
            try:
                # 获取单个文件的内容
                file_content = self._get_file_content(file_path)
                if not file_content:
                    self.console.print(f"[yellow]  ├─ 跳过空文件: {file_path}")
                    continue
                
                # 为单个文件创建格式化的代码内容
                formatted_code = self._format_file_for_review(file_path, file_content)
                
                # 获取该文件的上下文（可选）
                context_str = self._get_file_context(file_path)
                
                # 对单个文件进行审查
                file_response = self._perform_single_review(
                    formatted_code, 
                    context_str, 
                    batch_info=f"文件 {i}/{len(files_to_scan)}: {file_path}"
                )
                
                # 为评论添加文件信息
                for comment in file_response.comments:
                    if not comment.file_name:
                        comment.file_name = file_path
                
                all_comments.extend(file_response.comments)
                self.console.print(f"[dim]  ├─ 文件 {file_path} 扫描完成，发现 {len(file_response.comments)} 条建议")
                
            except Exception as e:
                self.console.print(f"[yellow]  ├─ 文件 {file_path} 扫描失败: {str(e)}")
                self.logger.warning(f"File {file_path} scan failed: {str(e)}")
                continue
        
        self.console.print(f"[dim]  ├─ 所有文件扫描完成，总计 {len(all_comments)} 条建议")
        return WorkerResponse(category=self.category, comments=all_comments)
    
    def _extract_files_from_diff(self, git_diff: str) -> List[str]:
        """从 git diff 中提取文件路径列表"""
        files = []
        lines = git_diff.split('\n')
        
        for line in lines:
            # 匹配 diff 文件头，如 "diff --git a/path/to/file.py b/path/to/file.py"
            if line.startswith('diff --git'):
                # 提取文件路径
                parts = line.split()
                if len(parts) >= 4:
                    # 格式: diff --git a/path/to/file.py b/path/to/file.py
                    file_path = parts[2][2:]  # 去掉 "a/" 前缀
                    if file_path not in files:
                        files.append(file_path)
            # 匹配 +++ 行，如 "+++ b/path/to/file.py"
            elif line.startswith('+++ b/'):
                file_path = line[6:]  # 去掉 "+++ b/" 前缀
                if file_path not in files:
                    files.append(file_path)
        
        return files
    
    def _get_file_content(self, file_path: str) -> str:
        """获取指定文件的完整内容"""
        try:
            full_path = os.path.join(self.code_indexer.repo_path, file_path)
            if not os.path.exists(full_path):
                return ""
            
            with open(full_path, 'r', encoding='utf-8', errors='ignore') as f:
                return f.read()
        except Exception as e:
            self.logger.warning(f"Failed to read file {file_path}: {str(e)}")
            return ""
    
    def _format_file_for_review(self, file_path: str, file_content: str) -> str:
        """为单个文件创建格式化的代码内容用于审查"""
        lines = file_content.split('\n')
        formatted_lines = []
        
        for i, line in enumerate(lines, 1):
            # 添加行号和文件路径信息
            formatted_line = f"{file_path}:{i}|{line}"
            formatted_lines.append(formatted_line)
        
        return '\n'.join(formatted_lines)
    
    def _get_file_context(self, file_path: str) -> str:
        """获取指定文件的上下文信息"""
        # 在仓库扫描模式下，我们通常不需要额外的上下文
        # 因为每个文件都是完整的，主要关注文件内部的问题
        # 如果需要上下文，可以通过配置启用
        
        # 检查是否启用了上下文检索
        if not getattr(self, 'enable_context_retrieval', False):
            return ""
        
        try:
            # 使用现有的上下文检索器获取相关上下文
            if hasattr(self, 'context_retriever') and self.context_retriever:
                # 创建一个简单的查询来获取相关上下文
                query = f"Code from {file_path}"
                context_docs = self.context_retriever.retrieve_context(query, max_docs=3)
                if context_docs:
                    return self.format_context(context_docs)
        except Exception as e:
            self.logger.warning(f"Failed to get context for file {file_path}: {str(e)}")
        
        return ""
    
    def _perform_single_review(self, code_to_review: str, context_str: str, batch_info: str = "") -> WorkerResponse:
        """执行单次审查"""
        batch_prefix = f"({batch_info}) " if batch_info else ""
        
        self.console.print(f"[dim]  ├─ {batch_prefix}正在生成审查提示...")
        messages = self.get_prompt(code_to_review, context_str)
        comments = []

        try:
            self.console.print(f"[dim]  ├─ {batch_prefix}正在调用 {self.model} 模型进行审查（流式模式）...")
            import time
            llm_start = time.time()
            
            # 使用流式模式调用LLM
            response_content = ""
            try:
                response_stream = self.ollama_client.chat(
                    model=self.model, 
                    messages=messages, 
                    temperature=0.3,
                    stream=True
                )
                
                # 收集流式响应
                for chunk in response_stream:
                    if chunk.get("message", {}).get("content"):
                        response_content += chunk["message"]["content"]
                
                llm_end = time.time()
                self.console.print(f"[dim]  ├─ {batch_prefix}LLM 流式响应完成 (耗时: {llm_end - llm_start:.1f}s)")
                
            except Exception as stream_error:
                self.console.print(f"[yellow]  ├─ {batch_prefix}流式调用失败，尝试非流式模式: {str(stream_error)}")
                self.logger.warning(f"Stream mode failed, falling back to non-stream: {str(stream_error)}")
                
                # 回退到非流式模式
                response = self.ollama_client.chat(model=self.model, messages=messages, temperature=0.3)
                response_content = response.get("message", {}).get("content", "")
                
                llm_end = time.time()
                self.console.print(f"[dim]  ├─ {batch_prefix}LLM 非流式响应完成 (耗时: {llm_end - llm_start:.1f}s)")
            
            if not response_content.strip():
                raise ValueError("LLM returned empty response")
            
            self.console.print(f"[dim]  ├─ {batch_prefix}正在解析审查结果...")
            comments = self.parse_llm_response(response_content)
            self.console.print(f"[dim]  └─ {batch_prefix}解析完成，生成 {len(comments)} 条建议")
            
        except Exception as e:
            self.console.print(f"[red]  └─ {batch_prefix}审查过程中出错: {e}")
            self.logger.error(f"LLM call failed for category {self.category.value}: {str(e)}")
            import traceback
            self.logger.error(f"Full traceback: {traceback.format_exc()}")
            
            # Handle any errors during LLM generation
            from .models import SeverityLevel
            comments = [
                CodeReviewComment(
                    category=self.category,
                    comment=f"Error during {self.category.value} review: {str(e)}",
                    severity=SeverityLevel.HIGH
                )
            ]
        
        return WorkerResponse(category=self.category, comments=comments)
