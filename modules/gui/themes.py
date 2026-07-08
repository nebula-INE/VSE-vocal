import os
from PySide6.QtCore import QFile, QTextStream
from PySide6.QtWidgets import QApplication

# テーマ名とQSSファイルの対応付け
THEME_FILES = {
    "dark": "themes/dark.qss",
    "light": "themes/light.qss"
}

def apply_theme(theme_name: str):
    """テーマをアプリケーション全体に適用する"""
    app = QApplication.instance()
    if not app:
        return

    # 指定されたテーマがない場合は dark をデフォルトにする
    file_path = THEME_FILES.get(theme_name, THEME_FILES["dark"])
    
    # ファイルが存在しない場合のセーフティ
    if not os.path.exists(file_path):
        print(f"Warning: Theme file not found: {file_path}")
        return

    file = QFile(file_path)
    if file.open(QFile.ReadOnly | QFile.Text):
        stream = QTextStream(file)
        qss = stream.readAll()
        app.setStyleSheet(qss)
        file.close()
