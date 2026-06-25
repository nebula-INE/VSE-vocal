#app_main.py

import sys
import os
import ctypes
import logging

from PySide6.QtWidgets import QApplication
from PySide6.QtGui import QFont

# --- 自作モジュールのインポート ---
# フォルダ構成に合わせてパスを調整（絶対インポートを推奨）
from modules.gui.main_window import MainWindow

# PyInstallerのスプラッシュスクリーン制御
try:
    import pyi_splash
except ImportError:
    pyi_splash = None

def main():
    # 1. 環境設定
    # Windows/Macでの高DPIスケーリングを有効化
    os.environ["QT_ENABLE_HIGHDPI_SCALING"] = "1"
    os.environ["QT_AUTOSCREEN_SCALE_FACTOR"] = "1"
    
    # ロギング設定
    logging.basicConfig(level=logging.INFO, format='%(levelname)s: %(message)s')

    app = QApplication(sys.argv)
    app.setApplicationName("VO-SE Pro")
    app.setOrganizationName("VO-SE Project")
    app.setStyle("Fusion")
    app.setFont(QFont("Segoe UI", 10))

    # --- 2. スタイルシート（ダークモード・モダンUI） ---
    app.setStyleSheet("""
        QMainWindow {
            background-color: #171a20;
            color: #e8ecf2;
        }

        QWidget {
            font-family: 'Segoe UI', 'Hiragino Kaku Gothic ProN', sans-serif;
            font-size: 10pt;
        }
        QLabel {
            color: #d8dee9;
        }

        QScrollArea, QListView, QTreeView, QTableView {
            border: 1px solid #2f3542;
            background-color: #1f2430;
            border-radius: 6px;
        }

        QPushButton {
            background-color: #2f81f7;
            border: 1px solid #2f81f7;
            color: #ffffff;
            padding: 8px 14px;
            border-radius: 6px;
            font-weight: 600;
        }
        QPushButton:hover { background-color: #3b8cff; }
        QPushButton:pressed { background-color: #2166cc; }
        QPushButton:disabled {
            background-color: #30363d;
            border-color: #30363d;
            color: #8b949e;
        }

        QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox, QTextEdit, QPlainTextEdit {
            background-color: #222833;
            border: 1px solid #3a4353;
            color: #f0f4fa;
            padding: 6px;
            border-radius: 6px;
            selection-background-color: #2f81f7;
        }
        QLineEdit:focus, QComboBox:focus, QSpinBox:focus, QDoubleSpinBox:focus, QTextEdit:focus, QPlainTextEdit:focus {
            border: 1px solid #5aa2ff;
        }

        QGroupBox {
            border: 1px solid #313845;
            border-radius: 8px;
            margin-top: 12px;
            padding-top: 12px;
        }
        QGroupBox::title {
            subcontrol-origin: margin;
            left: 10px;
            padding: 0 4px;
            color: #9fb7d3;
        }

        QSplitter::handle { background-color: #2a3040; }
        QSplitter::handle:horizontal { width: 5px; }
        QSplitter::handle:vertical { height: 5px; }

        QScrollBar:vertical {
            background: #151922;
            width: 10px;
            margin: 0;
            border-radius: 5px;
        }
        QScrollBar::handle:vertical {
            background: #3a4458;
            min-height: 24px;
            border-radius: 5px;
        }
        QScrollBar::handle:vertical:hover { background: #4a5670; }
        QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0px; }

        QToolTip {
            background-color: #11151c;
            color: #e8ecf2;
            border: 1px solid #3a4353;
            padding: 6px;
        }
        

    """)

    # --- 3. バックエンドの初期化（スプラッシュ表示中に実行） ---
    try:
        import importlib
        if pyi_splash:
            pyi_splash.update_text("音声エンジンをロード中...")
        
        VO_SE_Engine = importlib.import_module("modules.audio.vo_se_engine").VO_SE_Engine  # type: ignore[attr-defined]
        AIManager = importlib.import_module("modules.ai.ai_manager").AIManager  # type: ignore[attr-defined]
        AudioOutput = importlib.import_module("modules.audio.audio_output").AudioOutput  # type: ignore[attr-defined]

        # 低遅延オーディオ出力の初期化
        audio_device = AudioOutput(sample_rate=44100, block_size=256)
        
        # C言語エンジンのロード
        engine = VO_SE_Engine() 

        if pyi_splash:
            pyi_splash.update_text("AI推論モデルを最適化中...")

        # AIマネージャーの初期化
        ai = AIManager()
        ai.init_model()

        # 4. メインウィンドウの作成と依存注入(Dependency Injection)
        if pyi_splash:
            pyi_splash.update_text("UIを構築中...")
            
        window = MainWindow(engine=engine, ai=ai)
        window.audio_output = audio_device

        # --- 5. セットアップ完了、表示 ---
        if pyi_splash:
            pyi_splash.close()

        window.show()
        
    except Exception as e:
        logging.critical(f"アプリケーションの起動に失敗しました: {e}")
        if pyi_splash:
            pyi_splash.close()
        return

    sys.exit(app.exec())

# Windowsのタスクバーアイコン個別認識用（PyInstallerで必須）
if platform_system := os.name == 'nt':
    try:
        myappid = 'vose.pro.editor.v1'
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(myappid)
    except Exception:
        pass

if __name__ == "__main__":
    main()
