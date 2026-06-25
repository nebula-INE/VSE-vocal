# main.py
import sys
import os
import platform
import ctypes
import ctypes.util
import json

import importlib
from importlib.util import find_spec

# main.py の上部、既存のインポート文の下に追加してください

def global_exception_handler(exctype, value, tb):
    """
    アプリ全体の未キャッチ例外をすべて捕捉し、無言クラッシュを防ぐグローバルハンドラー。
    予期せぬエラー発生時、ユーザーにダイアログで原因を明示します。
    """
    import traceback
    error_message = "".join(traceback.format_exception(exctype, value, tb))
    print(f"[Fatal Crash] {error_message}", file=sys.stderr)
    
    try:
        # GUIがすでに起動している場合は、QMessageBoxでエラーを視覚的に表示
        from PySide6.QtWidgets import QApplication, QMessageBox
        if QApplication.instance():
            QMessageBox.critical(
                None,
                "致命的なエラー (VO-SE Pro)",
                f"アプリケーション内で予期しないエラーが発生しました。\n"
                f"開発者にこのエラーを報告してください。\n\n"
                f"【エラー内容】\n{value}\n\n"
                f"※詳細はコンソールログ、またはログファイルを確認してください。"
            )
    except Exception as e:
        print(f"Failed to show crash dialog: {e}", file=sys.stderr)
        
    sys.exit(1)

# グローバル例外ハンドラーをシステムに登録
sys.excepthook = global_exception_handler


# --- [1] リソースパス解決関数 (PyInstaller対応) ---
def get_resource_path(relative_path):
    if getattr(sys, 'frozen', False):
        base_path = getattr(sys, '_MEIPASS', os.path.dirname(os.path.abspath(__file__)))
    else:
        base_path = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(base_path, relative_path)


def get_engine_library_path():
    system = platform.system()
    if system == "Windows":
        lib_names = ("vose_core.dll",)
    elif system == "Darwin":
        lib_names = ("libvose_core.dylib", "vose_core.dylib")
    else:
        lib_names = ("libvose_core.so", "vose_core.so")

    for lib_name in lib_names:
        dll_path = get_resource_path(os.path.join("bin", lib_name))
        if os.path.exists(dll_path):
            return dll_path

    return get_resource_path(os.path.join("bin", lib_names[0]))


# --- [2] 設定管理クラス (ConfigHandler) ---
class ConfigHandler:
    def __init__(self, config_path="temp/config.json"):
        self.config_path = config_path
        self.default_config = {
            "last_save_dir": os.path.expanduser("~"),
            "default_voice": "mei_normal",
            "volume": 0.8
        }

    def load_config(self):
        if not os.path.exists(self.config_path):
            os.makedirs(os.path.dirname(self.config_path), exist_ok=True)
            self.save_config(self.default_config)
            return self.default_config
        try:
            with open(self.config_path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return self.default_config

    def save_config(self, config_dict):
        try:
            with open(self.config_path, "w", encoding="utf-8") as f:
                json.dump(config_dict, f, indent=4, ensure_ascii=False)
        except Exception as e:
            print(f"Config save error: {e}")


# --- [3] エンジンクラス (VoSeEngine) ---
class VoSeEngine:
    def __init__(self):
        self.os_name = platform.system()
        self.c_engine = None
        self._load_c_engine()

    def _load_c_engine(self):
        """
        OSに応じたライブラリ（DLL/dylib）を最適なパスからロードします。
        型チェックエラー（_MEIPASS）を回避し、Mac実機構造に対応した完全版です。
        """
        # 1. 基本的なリソースパス
        dll_path = get_engine_library_path()

        # 2. Mac特有のフォールバック処理
        if self.os_name == "Darwin":
            if not os.path.exists(dll_path):
                meipass = getattr(sys, '_MEIPASS', None)
                if meipass:
                    bundle_dir = os.path.dirname(os.path.dirname(meipass))
                    alt_path = os.path.join(bundle_dir, "Frameworks", "bin", os.path.basename(dll_path))
                    if os.path.exists(alt_path):
                        dll_path = alt_path
                        print(f"[Info] Mac Frameworks path used: {dll_path}")

        # 3. 最終的なロード実行
        if os.path.exists(dll_path):
            try:
                abs_dll_path = os.path.abspath(dll_path)

                if self.os_name == "Windows":
                    self.c_engine = ctypes.CDLL(abs_dll_path)
                else:
                    self.c_engine = ctypes.CDLL(abs_dll_path, mode=10)  # RTLD_GLOBAL

                # --- C関数の型定義 (歌唱・読み上げの両方に対応) ---
                if hasattr(self.c_engine, 'process_voice'):
                    self.c_engine.process_voice.argtypes = [
                        ctypes.POINTER(ctypes.c_float),
                        ctypes.c_int,
                        ctypes.POINTER(ctypes.c_float)
                    ]
                    self.c_engine.process_voice.restype = None

                print(f"[Success] C-Engine loaded: {abs_dll_path}")
            except Exception as e:
                print(f"[Error] Failed to load C-Engine: {e}")
                if hasattr(sys, 'stderr'):
                    import traceback
                    traceback.print_exc()
                self.c_engine = None  # 失敗時は明示的に None を保証
        else:
            # [FIX-1] 警告のみで続行せず、理由を明記したうえで None を確定する
            # main() 側で QMessageBox.warning を表示するため、ここでは print のみ
            print(f"[Warning] C-Engine file not found at: {dll_path}")
            self.c_engine = None  # 明示的に None を保証（後続クラッシュ防止）

    def analyze_intonation(self, text):
        """【読み上げ用】音韻解析"""
        print(f"\n--- 読み上げ解析実行: '{text}' ---")
        try:
            pyopenjtalk = importlib.import_module("pyopenjtalk")
            labels = pyopenjtalk.extract_fullcontext(text)
            return labels
        except Exception as e:
            return [f"Analysis failed: {str(e)}"]

    def analyze_singing_pitch(self, notes, sample_rate=48000, frame_period_ms=5.0):
        """
        【歌唱用・WORLDエンジン連携】ノート列からF0カーブ（フレーム単位の周波数配列）を生成する。
        notes: [{'note_number': 60, 'duration': 0.5}, ...] を想定。

        ■ 変更・最適化ポイント:
        - 複数スレッドからの想定外の操作や非同期エディット時、空要素や None が混入した際の例外耐性を強化。
        - 毎回 `importlib.import_module("numpy")` を叩くコストを削減（一度インポートされたらキャッシュ）。
        - 補間カーブ（Hanning窓）計算時、データ長が極端に短い（ノート切り替え直後など）ケースでの境界バグを排除。
        """
        print("--- WORLD歌唱ピッチ解析実行 ---")
        try:
            # numpyを安全かつ低コストでロード
            import sys
            if "numpy" in sys.modules:
                np = sys.modules["numpy"]
            else:
                import importlib
                np = importlib.import_module("numpy")
     
            if not notes:
                return np.zeros(1, dtype=np.float32)

            frame_sec = max(frame_period_ms / 1000.0, 1e-4)
            min_hz = 20.0
            max_hz = 5000.0

            def _read_note_value(note_obj, key, default=None):
                if isinstance(note_obj, dict):
                    return note_obj.get(key, default)
                return getattr(note_obj, key, default)

            hz_segments = []
            for note in notes:
                # [堅牢化] スレッド競合やエディタの非同期更新により、配列内に None や不正オブジェクトが混入するのをガード
                if note is None:
                    continue
                
                try:
                    duration = float(_read_note_value(note, "duration", 0.0) or 0.0)
                except (TypeError, ValueError):
                    duration = 0.0

                if duration <= 0:
                    continue

                midi_note = _read_note_value(
                    note,
                    "note_number",
                    _read_note_value(note, "pitch", _read_note_value(note, "note", 69))
                )
                try:
                    midi_value = float(midi_note)
                except (TypeError, ValueError):
                    midi_value = 69.0  # デフォルト（A4）

                # MIDIノート番号から周波数(Hz)への変換
                hz = 440.0 * (2.0 ** ((midi_value - 69.0) / 12.0))
                hz = min(max(hz, min_hz), max_hz)

                # フレーム数の算出
                frame_count = max(1, int(round(duration / frame_sec)))
                hz_segments.append(np.full(frame_count, hz, dtype=np.float32))

            if not hz_segments:
                return np.zeros(1, dtype=np.float32)

            f0_curve = np.concatenate(hz_segments)

            # ノート境界を滑らかに補間（簡易ポルタメント）
            smooth_window = max(1, int(round(0.03 / frame_sec)))  # 約30ms
            
            # [堅牢化] 総フレーム数が窓長より短い場合の境界エラーを徹底防止
            if smooth_window > 1 and len(f0_curve) > smooth_window:
                kernel = np.hanning(smooth_window)
                kernel_sum = float(kernel.sum())
                if kernel_sum > 0:
                    kernel /= kernel_sum
                    f0_curve = np.convolve(f0_curve, kernel, mode='same').astype(np.float32)
                    
            return f0_curve

        except Exception as e:
            print(f"[Error] analyze_singing_pitch failed: {e}")
            # 万が一の予期せぬ不具合時も、後続のC++処理（process_with_c）でクラッシュを起こさないよう安全なゼロ配列を保証
            try:
                import sys
                if "numpy" in sys.modules:
                    np = sys.modules["numpy"]
                else:
                    import numpy as np
                return np.zeros(1, dtype=np.float32)
            except Exception:
                # [修正] numpyのインポートすら完全に破綻している最悪のケースのフォールバック
                # Pyrightのエラーを完全に回避しつつ、後続の process_with_c が安全に処理できる None を返します。
                return None

    def process_with_c(self, data_array, f0_array=None):
        """
        【共通処理】波形データとピッチデータ（WORLD F0カーブなど）をC++エンジンに送り込みます。
        
        ■ 変更・最適化ポイント:
        - 再生スレッド、UIメーター描画、エディットスレッド等から同時にC++コアへ
          アクセスした際のメモリ競合・破壊（Segfault）を防ぐため、Pythonの Lock (Mutex) を導入。
        - numpyモジュールのロードにおいて、キャッシュ（sys.modules）を優先参照してリアルタイム処理時の
          オーバーヘッドを極限まで削減。
        """
        if not self.c_engine or not hasattr(self.c_engine, 'process_voice'):
            print("[Warning] C-Engine not available, skipping processing")
            return data_array

        # [スレッド安全の確保] スレッド間の競合を防ぐロックオブジェクトを動的に確保
        if not hasattr(self, '_lock'):
            import threading
            self._lock = threading.Lock()

        try:
            # numpyを安全かつ低コストでロード（毎回の探索オーバーヘッドを排除）
            import sys
            if "numpy" in sys.modules:
                np = sys.modules["numpy"]
            else:
                import importlib
                np = importlib.import_module("numpy")

            # 同時実行を防ぐため、ロックを獲得してC++コアへ突入
            with self._lock:
                # 波形データの準備
                wav_float = np.ascontiguousarray(data_array, dtype=np.float32)
                wav_ptr = wav_float.ctypes.data_as(ctypes.POINTER(ctypes.c_float))
                length = len(wav_float)

                # [FIX-2] f0_float を明示的に保持し、GCによるダングリングポインタを防ぐ
                f0_float = None
                f0_ptr = None
                if f0_array is not None:
                    f0_float = np.ascontiguousarray(f0_array, dtype=np.float32)
                    f0_ptr = f0_float.ctypes.data_as(ctypes.POINTER(ctypes.c_float))

                # C++エンジンの呼び出し（WORLD等のコア処理）
                # ロック内かつスコープ内のため、wav_float・f0_float のメモリは100%安全に保護されます
                self.c_engine.process_voice(wav_ptr, length, f0_ptr)

            # [FIX-3] wav_float を返すことで GC による早期解放を防ぐ
            return wav_float

        except Exception as e:
            print(f"C-Process error: {e}")
            return data_array

PYTHON_RUNTIME_PACKAGES = (
    "numpy",
    "pyopenjtalk",
    "PySide6",
    "sounddevice",
    "soundfile",
)

OS_RUNTIME_LIBRARIES = {
    "Linux": (
        ("libGL.so.1", "GL", "libgl1"),
        ("libEGL.so.1", "EGL", "libegl1"),
        ("libxkbcommon.so.0", "xkbcommon", "libxkbcommon0"),
        ("libxkbcommon-x11.so.0", "xkbcommon-x11", "libxkbcommon-x11-0"),
        ("libdbus-1.so.3", "dbus-1", "libdbus-1-3"),
        ("libxcb-cursor.so.0", "xcb-cursor", "libxcb-cursor0"),
        ("libXrender.so.1", "Xrender", "libxrender1"),
        ("libXi.so.6", "Xi", "libxi6"),
        ("libSM.so.6", "SM", "libsm6"),
        ("libXext.so.6", "Xext", "libxext6"),
        ("libfontconfig.so.1", "fontconfig", "libfontconfig1"),
        ("libpulse.so.0", "pulse", "libpulse0"),
        ("libasound.so.2", "asound", "libasound2t64/libasound2"),
        ("libsndfile.so.1", "sndfile", "libsndfile1"),
        ("libportaudio.so.2", "portaudio", "portaudio19-dev"),
    ),
    "Darwin": (
        ("libportaudio.dylib", "portaudio", "portaudio"),
        ("libsndfile.dylib", "sndfile", "libsndfile"),
    ),
}

OS_DEPENDENCY_INSTALL_HINTS = {
    "Linux": (
        "Ubuntu/Debian: sudo apt-get install -y "
        "libgl1 libegl1 libxkbcommon0 libxkbcommon-x11-0 libdbus-1-3 "
        "libxcb-cursor0 libxrender1 libxi6 libsm6 libxext6 libfontconfig1 "
        "libpulse0 libasound2t64 libsndfile1 portaudio19-dev"
    ),
    "Darwin": "macOS: brew install portaudio libsndfile",
}


def _is_os_library_loadable(library_lookup_name):
    library_path = ctypes.util.find_library(library_lookup_name)
    # [修正] library_path が None の場合は、ctypes.CDLL に渡さず即座に False を返す
    if library_path is None:
        return False

    try:
        ctypes.CDLL(library_path)
    except OSError:
        return False

    return True


def _check_runtime_requirements():
    """
    起動前に実行環境をチェックし、足りない要件をユーザーへ明示する。
    """
    missing = []

    for module_name in PYTHON_RUNTIME_PACKAGES:
        if find_spec(module_name) is None:
            missing.append(f"Python package: {module_name}")
    

    system_name = platform.system()
    check_os_libraries = not (getattr(sys, "frozen", False) and system_name == "Darwin")
    if check_os_libraries:
        for display_name, lookup_name, package_name in OS_RUNTIME_LIBRARIES.get(system_name, ()):
            if not _is_os_library_loadable(lookup_name):
                missing.append(f"OS library: {display_name} ({package_name})")

    return missing


# --- [4] メイン実行処理 ---
def main():
    missing = _check_runtime_requirements()
    if missing:
        print("[Fatal] 起動に必要な依存関係が不足しています。")
        for item in missing:
            print(f"  - {item}")
        print("requirements.txt と OS 依存ライブラリをインストールして再実行してください。")
        install_hint = OS_DEPENDENCY_INSTALL_HINTS.get(platform.system())
        if install_hint:
            print(f"例: {install_hint}")
        sys.exit(1)

    # Linuxヘッドレス環境（DISPLAYなし）では offscreen を既定にして起動を継続
    if platform.system() == "Linux":
        if not os.environ.get("DISPLAY") and not os.environ.get("WAYLAND_DISPLAY"):
            os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
            print("[Info] Linux headless mode detected. QT_QPA_PLATFORM=offscreen を使用します。")

    try:
        QtWidgets = importlib.import_module("PySide6.QtWidgets")
        QtGui = importlib.import_module("PySide6.QtGui")
        QtCore = importlib.import_module("PySide6.QtCore")
    except Exception as e:
        print(f"[Fatal] GUI モジュールの読み込みに失敗しました: {e}")
        sys.exit(1)

    try:
        MainWindow = importlib.import_module("modules.gui.main_window").MainWindow
    except Exception as e:
        print(f"[Fatal] メインウィンドウの読み込みに失敗しました: {e}")
        if hasattr(sys, "stderr"):
            import traceback
            traceback.print_exc()
        sys.exit(1)

    QApplication = QtWidgets.QApplication
    QMessageBox = QtWidgets.QMessageBox
    QIcon = QtGui.QIcon
    QTimer = QtCore.QTimer
    Qt = QtCore.Qt

    app = QApplication(sys.argv)
    app.setApplicationName("VO-SE Pro")

    for icon_rel in ("assets/icon.png", "assets/icon.icns", "assets/icon.ico"):
        icon_path = get_resource_path(icon_rel)
        if os.path.exists(icon_path):
            app.setWindowIcon(QIcon(icon_path))
            break
    # [FIX-1] DLL未検出時は warning ダイアログで明示。エラーではなく warning に留め、
    #         アプリは起動継続（合成・歌唱機能のみ無効化）とする。
    dll_path = get_engine_library_path()
    if not os.path.exists(dll_path):
        QMessageBox.warning(
            None,
            "コアエンジン未検出",
            f"VO-SE Core Engine が見つかりません。\n"
            f"音声合成・歌声合成機能は利用できません。\n\n"
            f"期待されるパス:\n{dll_path}"
        )

    config_handler = ConfigHandler()
    config = config_handler.load_config()
    engine = VoSeEngine()

    try:
        window = MainWindow()
    except Exception as e:
        QMessageBox.critical(None, "起動エラー", f"メイン画面の初期化でエラーが発生しました。\n{e}")
        if hasattr(sys, "stderr"):
            import traceback
            traceback.print_exc()
        sys.exit(1)

    window.vo_se_engine = engine
    window.config = config

    # ステータスバーの状態を動的に更新（MainWindowにupdate_statusメソッドがあると仮定）
    if engine.c_engine:
        window.statusBar().showMessage("VO-SE Core Engine: Ready")
    else:
        window.statusBar().showMessage("VO-SE Core Engine: Not Found (Offline Mode)")
    if os.environ.get("VOSE_STARTUP_SMOKE_TEST") == "1":
        print("[SmokeTest] VO-SE Pro initialized successfully.")
        config_handler.save_config(config)
        app.quit()
        return 0


    def show_main_window():
        if window.isMinimized():
            window.showNormal()
        else:
            window.show()
        window.raise_()
        window.activateWindow()
        window_handle = window.windowHandle()
        if window_handle is not None:
            window_handle.requestActivate()

    def release_startup_frontmost_hint():
        window.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, False)
        show_main_window()

    window.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, True)
    show_main_window()
    QTimer.singleShot(0, show_main_window)
    QTimer.singleShot(300, show_main_window)
    QTimer.singleShot(1200, release_startup_frontmost_hint)

    result = app.exec()
    config_handler.save_config(config)
    return result





if __name__ == "__main__":
    sys.exit(main())
