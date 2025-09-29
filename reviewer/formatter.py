import re
import os
from typing import Dict, List, Optional, Tuple
from .models import CodeReviewResponse, CodeReviewComment, CodeReviewCategory, SeverityLevel

class CodeReviewFormatter:
    """Base class for formatting code reviews"""
    def __init__(self, git_diff: str, response: CodeReviewResponse):
        self.git_diff = git_diff
        self.response = response
        self.comments_by_file = self._organize_comments_by_file()
    
    def _organize_comments_by_file(self) -> Dict[str, List[Tuple[int, CodeReviewComment]]]:
        """Group comments by file and line number for easier processing"""
        comments_by_file = {}
        
        for category, comments in self.response.categories.items():
            for comment in comments:
                # Skip "No X issues detected" comments
                if "no issues detected" in comment.comment.lower():
                    continue
                
                file_name = comment.file_name
                line_number = comment.line_number
                
                if file_name:
                    if file_name not in comments_by_file:
                        comments_by_file[file_name] = []
                    
                    if line_number:
                        comments_by_file[file_name].append((line_number, comment))
                    else:
                        # For file-level comments (no specific line), use line 0
                        comments_by_file[file_name].append((0, comment))
        
        return comments_by_file
    
    def format(self) -> str:
        """Format the code review (to be implemented by subclasses)"""
        raise NotImplementedError("Subclasses must implement this method")


class MarkdownFormatter(CodeReviewFormatter):
    """Format code reviews as Markdown"""
    
    def _get_severity_emoji(self, severity: Optional[SeverityLevel]) -> str:
        """获取严重程度对应的emoji"""
        if not severity:
            return "🟡"
        
        emoji_map = {
            SeverityLevel.CRITICAL: "🔴",
            SeverityLevel.HIGH: "🟠", 
            SeverityLevel.MEDIUM: "🟡",
            SeverityLevel.LOW: "🟢"
        }
        return emoji_map.get(severity, "🟡")
    
    def format(self) -> str:
        md_output = [f"# Code Review"]
        
        # 只有在摘要不是跳过消息时才显示 Changelog 部分
        if not ("已跳过摘要生成" in self.response.summary):
            md_output.append("\n## Changelog\n")
            md_output.append(f"{self.response.summary}\n")

        # md_output.append("## Review Comments\n")
        # md_output.append("| File | Line | Category | Severity | Comment |")
        # md_output.append("|------|------|----------|----------|---------|")
        
        # Sort comments by severity (Critical > High > Medium > Low)
        severity_order = {
            SeverityLevel.CRITICAL: 0,
            SeverityLevel.HIGH: 1,
            SeverityLevel.MEDIUM: 2,
            SeverityLevel.LOW: 3
        }
        
        all_comments = []
        for category, comments in self.response.categories.items():
            for comment in comments:
                if "no issues detected" in comment.comment.lower():
                    continue
                all_comments.append(comment)
        
        # # Sort by severity, then by category
        # all_comments.sort(key=lambda c: (
        #     severity_order.get(c.severity, 999),
        #     c.category.value if c.category else ""
        # ))
        
        # for comment in all_comments:
        #     file_name = comment.file_name if comment.file_name else "-"
        #     line_no = str(comment.line_number) if comment.line_number else "-"
        #     severity = comment.severity.value if comment.severity else "Medium"
        #     severity_emoji = self._get_severity_emoji(comment.severity)
        #     md_output.append(f"| {file_name} | {line_no} | {comment.category.value} | {severity_emoji} {severity} | {comment.comment} |")
        
        # Add detailed sections for each file with comments
        md_output.append("\n## Details by File\n")
        
        for file_name, comments in self.comments_by_file.items():
            md_output.append(f"### {file_name}\n")
            
            # Sort comments by line number
            sorted_comments = sorted(comments, key=lambda x: x[0])
            
            for line_number, comment in sorted_comments:
                line_info = f"Line {line_number}" if line_number > 0 else "File-level comment"
                severity_emoji = self._get_severity_emoji(comment.severity)
                severity_text = comment.severity.value if comment.severity else "Medium"
                md_output.append(f"- **{line_info}** ({comment.category.value}) {severity_emoji} **{severity_text}**: {comment.comment}")
            
            md_output.append("")  # Empty line between files
        
        return "\n".join(md_output)



def format_review(git_diff: str, response: CodeReviewResponse, format_type: str = "markdown", repo="./") -> str:
    """
    Format a code review response in the specified format.
    
    Args:
        git_diff: The original git diff
        response: The code review response
        format_type: The output format (only "markdown" is supported)
        repo_path: Optional path to the repository root (unused, kept for compatibility).

    Returns:
        Formatted review as a string
    """
    formatter = MarkdownFormatter(git_diff, response)
    return formatter.format()
