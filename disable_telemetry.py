"""
ChromaDB遥测禁用模块
用于彻底禁用ChromaDB的遥测功能，解决capture方法兼容性问题
"""
import os
import sys


def disable_chromadb_telemetry():
    """
    彻底禁用ChromaDB遥测功能
    必须在导入chromadb之前调用
    """
    # 设置所有相关的环境变量
    os.environ["ANONYMIZED_TELEMETRY"] = "False"
    os.environ["CHROMA_TELEMETRY"] = "False" 
    os.environ["CHROMA_TELEMETRY_IMPL"] = "none"
    os.environ["CHROMA_DISABLE_TELEMETRY"] = "1"
    
    # 尝试禁用posthog
    try:
        import posthog
        posthog.disabled = True
    except ImportError:
        pass
    
    # 猴子补丁修复capture方法
    try:
        import chromadb.telemetry.posthog as chroma_posthog
        
        # 创建一个不执行任何操作的capture方法
        def dummy_capture(*args, **kwargs):
            pass
            
        # 如果存在Posthog类，替换其capture方法
        if hasattr(chroma_posthog, 'Posthog'):
            chroma_posthog.Posthog.capture = dummy_capture
            
    except ImportError:
        pass
    
    # 尝试直接修补chromadb的遥测模块
    try:
        import chromadb.telemetry as telemetry_module
        
        # 创建一个空的遥测类
        class NoOpTelemetry:
            def capture(self, *args, **kwargs):
                pass
            def __getattr__(self, name):
                return lambda *args, **kwargs: None
                
        # 替换遥测实例
        if hasattr(telemetry_module, 'telemetry'):
            telemetry_module.telemetry = NoOpTelemetry()
            
    except ImportError:
        pass


# 在模块导入时自动禁用遥测
disable_chromadb_telemetry()
