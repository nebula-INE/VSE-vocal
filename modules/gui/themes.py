import os
from PySide6.QtCore import QFile, QTextStream
from PySide6.QtWidgets import QApplication

# プロジェクト構造に応じたQSSファイルの相対パス
# (themes.py と同じ階層に themes フォルダがある前提)
BASE_DIR = os.path.dirname(os.path.abspath(__file__))

THEME_FILES = {
    "dark": os.path.join(BASE_DIR, "themes", "dark.qss"),
    "light": os.path.join(BASE_DIR, "themes", "light.qss")
}

def apply_theme(theme_name: str) -> bool:
    """
    アプリケーション全体に指定されたテーマ（QSS）を適用する。
    
    Args:
        theme_name (str): "dark" または "light"
        
    Returns:
        bool: 適用に成功した場合は True、失敗した場合は False
    """
    app = QApplication.instance()
    if not app:
        print("[Theme Error] QApplication instance does not exist.")
        return False

    # 指定されたテーマがない場合は "dark" をデフォルトにする
    file_path = THEME_FILES.get(theme_name)
    if not file_path:
        print(f"[Theme Warning] Theme '{theme_name}' not found. Falling back to 'dark'.")
        file_path = THEME_FILES["dark"]
    
    # QSSファイルの存在チェック
    if not os.path.exists(file_path):
        print(f"[Theme Error] QSS file not found at: {file_path}")
        return False

    # ファイルの読み込みと適用
    file = QFile(file_path)
    if file.open(QFile.ReadOnly | QFile.Text):
        try:
            stream = QTextStream(file)
            # 文字コードの問題を防ぐためUTF-8を明示（必要に応じて）
            stream.setEncoding(QTextStream.Encoding.Utf8) 
            qss = stream.readAll()
            
            # アプリケーション全体にスタイルシートを適用
            app.setStyleSheet(qss)
            return True
        except Exception as e:
            print(f"[Theme Error] Failed to read or apply QSS: {e}")
            return False
        finally:
            file.close()
    else:
        print(f"[Theme Error] Could not open file: {file_path}")
        return False
