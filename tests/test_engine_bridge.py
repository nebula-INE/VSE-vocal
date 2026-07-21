from unittest.mock import MagicMock, patch
import ctypes
from modules.gui.core_manager import VoseCoreManager

def test_core_manager_fallback():
    """"""
    with patch('ctypes.CDLL', side_effect=OSError("DLL not found")):
        manager = VoseCoreManager()
        # _init_engine 
        manager._initialized = False 
        manager._init_engine()
        
        assert manager.get_lib() is None
        assert "DLL not found" in manager._disabled_reason
