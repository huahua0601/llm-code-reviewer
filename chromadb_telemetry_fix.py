"""
ChromaDB遥测错误的终极修复方案
此模块应该在任何ChromaDB相关导入之前被导入
"""
import os
import sys


def apply_ultimate_fix():
    """应用终极修复方案"""
    
    # 1. 设置所有可能的环境变量
    env_vars = {
        "ANONYMIZED_TELEMETRY": "False",
        "CHROMA_TELEMETRY": "False",
        "CHROMA_TELEMETRY_IMPL": "none", 
        "CHROMA_DISABLE_TELEMETRY": "1",
        "POSTHOG_DISABLED": "1",
        "POSTHOG_CAPTURE": "False"
    }
    
    for key, value in env_vars.items():
        os.environ[key] = value
    
    # 2. 预先导入并修补posthog
    try:
        import posthog
        # 完全替换capture方法
        def no_op_capture(*args, **kwargs):
            return None
        posthog.capture = no_op_capture
        if hasattr(posthog, 'Posthog'):
            posthog.Posthog.capture = lambda self, *args, **kwargs: None
    except ImportError:
        pass
    
    # 3. 创建假的posthog模块以防止导入错误
    class FakePosthog:
        @staticmethod
        def capture(*args, **kwargs):
            return None
        
        @staticmethod  
        def __getattr__(name):
            return lambda *args, **kwargs: None
    
    # 4. 在sys.modules中注册假模块
    if 'posthog' not in sys.modules:
        sys.modules['posthog'] = FakePosthog()
    
    # 5. 修补ChromaDB的遥测模块
    def patch_chromadb_telemetry():
        try:
            import chromadb.telemetry.posthog as chroma_posthog
            
            class NoOpPosthog:
                def __init__(self, *args, **kwargs):
                    pass
                def capture(self, *args, **kwargs):
                    return None
                def __getattr__(self, name):
                    return lambda *args, **kwargs: None
            
            chroma_posthog.Posthog = NoOpPosthog
            
        except ImportError:
            pass
        
        try:
            import chromadb.telemetry as telemetry
            
            class NoOpTelemetry:
                def capture(self, *args, **kwargs):
                    return None
                def __getattr__(self, name):
                    return lambda *args, **kwargs: None
            
            if hasattr(telemetry, 'telemetry'):
                telemetry.telemetry = NoOpTelemetry()
                
        except ImportError:
            pass
    
    # 6. 安装导入钩子
    class TelemetryFixHook:
        def find_spec(self, name, path, target=None):
            if name.startswith('chromadb'):
                patch_chromadb_telemetry()
            return None
    
    # 确保钩子只安装一次
    hook_installed = any(isinstance(hook, TelemetryFixHook) for hook in sys.meta_path)
    if not hook_installed:
        sys.meta_path.insert(0, TelemetryFixHook())


# 立即应用修复
apply_ultimate_fix()
