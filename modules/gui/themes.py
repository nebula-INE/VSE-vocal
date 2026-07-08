# layout_manager.py
from PySide6.QtCore import QSettings, QByteArray
from PySide6.QtWidgets import QMainWindow

def save_layout(window: QMainWindow):
    """ウィンドウの位置・サイズ・状態を保存します。"""
    settings = QSettings("VO-SE", "Pro")
    settings.setValue("geometry", window.saveGeometry())
    settings.setValue("windowState", window.saveState())

def restore_layout(window: QMainWindow):
    """ウィンドウの位置・サイズ・状態を安全に復元します。"""
    settings = QSettings("VO-SE", "Pro")
    
    # type=QByteArray を指定することで、OS依存の型変化を強制的に防ぎます
    geometry = settings.value("geometry", type=QByteArray)
    state = settings.value("windowState", type=QByteArray)
    
    # 完全に空（初回起動時など）でないか、より厳密にチェック
    if geometry and not geometry.isEmpty():
        window.restoreGeometry(geometry)
    if state and not state.isEmpty():
        window.restoreState(state)
