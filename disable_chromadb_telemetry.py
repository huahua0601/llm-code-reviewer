"""
ChromaDB遥测完全禁用模块
此模块提供完整的ChromaDB遥测禁用功能，确保不会出现任何遥测相关错误
"""
import os
import sys


def disable_chromadb_telemetry():
    """
    完全禁用ChromaDB遥测功能
    必须在导入chromadb之前调用
    """
    # 设置所有已知的ChromaDB遥测环境变量
    telemetry_env_vars = {
        "ANONYMIZED_TELEMETRY": "False",
        "CHROMA_TELEMETRY": "False", 
        "CHROMA_TELEMETRY_IMPL": "none",
        "CHROMA_DISABLE_TELEMETRY": "1",
        "POSTHOG_DISABLED": "1",
        "POSTHOG_CAPTURE": "False",
        "CHROMA_TELEMETRY_ENABLED": "False",
        # 为了保险起见，添加更多可能的变量
        "DISABLE_TELEMETRY": "1",
        "NO_TELEMETRY": "1",
    }
    
    for key, value in telemetry_env_vars.items():
        os.environ[key] = value
    
    # 尝试预先导入并修补posthog
    try:
        import posthog
        # 替换capture方法为无操作函数
        posthog.capture = lambda *args, **kwargs: None
        posthog.identify = lambda *args, **kwargs: None
        posthog.alias = lambda *args, **kwargs: None
        posthog.set = lambda *args, **kwargs: None
        posthog.group_identify = lambda *args, **kwargs: None
        if hasattr(posthog, 'Posthog'):
            posthog.Posthog.capture = lambda self, *args, **kwargs: None
            posthog.Posthog.identify = lambda self, *args, **kwargs: None
            posthog.Posthog.alias = lambda self, *args, **kwargs: None
            posthog.Posthog.set = lambda self, *args, **kwargs: None
            posthog.Posthog.group_identify = lambda self, *args, **kwargs: None
    except ImportError:
        pass
    
    # 创建假的posthog模块以防止导入错误
    if 'posthog' not in sys.modules:
        class FakePosthog:
            @staticmethod
            def capture(*args, **kwargs):
                return None
            
            @staticmethod
            def identify(*args, **kwargs):
                return None
                
            @staticmethod
            def alias(*args, **kwargs):
                return None
                
            @staticmethod
            def set(*args, **kwargs):
                return None
                
            @staticmethod
            def group_identify(*args, **kwargs):
                return None
            
            @staticmethod
            def __getattr__(name):
                return lambda *args, **kwargs: None
        
        sys.modules['posthog'] = FakePosthog()
    
    # 修补ChromaDB的遥测模块（如果已经导入）
    if 'chromadb' in sys.modules:
        try:
            import chromadb.telemetry.posthog as chroma_posthog
            
            class NoOpPosthog:
                def __init__(self, *args, **kwargs):
                    pass
                def capture(self, *args, **kwargs):
                    return None
                def identify(self, *args, **kwargs):
                    return None
                def alias(self, *args, **kwargs):
                    return None
                def set(self, *args, **kwargs):
                    return None
                def group_identify(self, *args, **kwargs):
                    return None
                def __getattr__(self, name):
                    return lambda *args, **kwargs: None
            
            chroma_posthog.Posthog = NoOpPosthog
            
        except ImportError:
            pass
        
        try:
            import chromadb.telemetry.product as product_telemetry
            
            class NoOpProductTelemetryClient:
                def __init__(self, *args, **kwargs):
                    pass
                def capture(self, *args, **kwargs):
                    return None
                def __getattr__(self, name):
                    return lambda *args, **kwargs: None
            
            product_telemetry.ProductTelemetryClient = NoOpProductTelemetryClient
            
        except ImportError:
            pass


# 自动执行禁用
disable_chromadb_telemetry()
