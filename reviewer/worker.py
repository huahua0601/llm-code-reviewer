import json
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

    def get_sampled_code_lines(self, git_diff):
        # Format the files and code lines for the LLM
        formatted_code_lines = []
        for file_name, line_number, code_line in extract_added_lines(git_diff):
            prefix = '+ ' 
            # Ensure line_number is valid before formatting
            if line_number is not None and line_number > 0:
                 formatted_code_lines.append(f"{file_name}:{line_number}: {prefix}{code_line.rstrip()}")

        # Limit the amount of code sent to avoid overwhelming the (small) model
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

        # Return None if comment is invalid or lacks essential info
        if not comment_text or not file_name:
            print(f"Warning: Skipping comment due to missing text or file name: {comment_data}")
            return None

        return CodeReviewComment(
            category=self.category,
            file_name=file_name,
            line_number=line_number,
            comment=comment_text,
            severity=severity
        )
    
    def parse_llm_response(self, response) -> List[CodeReviewComment]:
        # Extract JSON from response (in case the LLM includes extra text)
        json_match = re.search(r'\[\s*{.*}\s*\]', response, re.DOTALL)
        if json_match:
            json_str = json_match.group(0)
        else:
            json_str = response

        print("Trying to parse LLM response:")
        self.console.print(json_str)
        
        try:
            comments_data = json.loads(json_str)
            comments = [self.parse_comment(item) for item in comments_data]
            comments = [c for c in comments if c is not None]

            if not comments:
                from .models import SeverityLevel
                comments.append(
                    CodeReviewComment(
                        category=self.category,
                        comment=f"No {self.category.value.lower()} issues detected.",
                        severity=SeverityLevel.LOW
                    )
                )
        except json.JSONDecodeError:
            print("JSON parsing error")
            import traceback
            print(traceback.format_exc())
            # If JSON parsing fails, create a fallback comment
            from .models import SeverityLevel
            comments = [
                CodeReviewComment(
                    category=self.category,
                    comment="Unable to parse LLM response. Please check the model output format.",
                    severity=SeverityLevel.MEDIUM
                )
            ]
        return comments
    
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

    
    def review(self, git_diff: str, system_prompt: str) -> WorkerResponse:
        """
        Perform the code review for a specific category using Ollama and RAG context.
        """
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
        
        self.console.print(f"[dim]  ├─ 正在生成审查提示...")
        messages = self.get_prompt(code_to_review, context_str)
        comments = []

        try:
            self.console.print(f"[dim]  ├─ 正在调用 {self.model} 模型进行审查...")
            import time
            llm_start = time.time()
            
            response = self.ollama_client.chat(model=self.model, messages=messages, temperature=0.3)
            
            llm_end = time.time()
            self.console.print(f"[dim]  ├─ LLM 响应完成 (耗时: {llm_end - llm_start:.1f}s)")
            
            self.console.print(f"[dim]  ├─ 正在解析审查结果...")
            comments = self.parse_llm_response(response)
            self.console.print(f"[dim]  └─ 解析完成，生成 {len(comments)} 条建议")
            
        except Exception as e:
            self.console.print(f"[red]  └─ 审查过程中出错: {e}")
            import traceback
            print(traceback.format_exc())
            # Handle any errors during LLM generation
            comments = [
                CodeReviewComment(
                    category=self.category,
                    comment=f"Error during {self.category.value} review: {str(e)}"
                )
            ]
        
        return WorkerResponse(category=self.category, comments=comments)
