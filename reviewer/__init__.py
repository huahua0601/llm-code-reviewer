"""
LLM Code Reviewer Package
"""
import os
import sys

# 添加根目录到路径
_root_dir = os.path.dirname(os.path.dirname(__file__))
if _root_dir not in sys.path:
    sys.path.insert(0, _root_dir)

# 立即应用终极修复
try:
    from chromadb_telemetry_fix import apply_ultimate_fix
    apply_ultimate_fix()
except ImportError:
    # 备用方案：直接设置环境变量
    os.environ["ANONYMIZED_TELEMETRY"] = "False"
    os.environ["CHROMA_TELEMETRY"] = "False"
    os.environ["CHROMA_TELEMETRY_IMPL"] = "none"
    os.environ["CHROMA_DISABLE_TELEMETRY"] = "1"
    os.environ["POSTHOG_DISABLED"] = "1"
