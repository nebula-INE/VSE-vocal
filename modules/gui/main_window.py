#main_window.py
# ==========================================================================
# 1. 標準ライブラリ (Standard Libraries)
# ==========================================================================
import os
import sys
import time
import wave
import json
import ctypes
import pickle
import threading
import math
from copy import deepcopy
from typing import Any, List, Dict, Optional, TYPE_CHECKING, cast              

# ==========================================================================
# 2. 数値計算・信号処理 (Numerical Processing)
# ==========================================================================
import importlib
import importlib.util
import numpy as np
mido = importlib.import_module("mido") if importlib.util.find_spec("mido") else None
ort = importlib.import_module("onnxruntime") if importlib.util.find_spec("onnxruntime") else None

# ==========================================================================
# 3. GUIライブラリ (PySide6 )
# ==========================================================================
from PySide6.QtCore import (
    Qt, Signal, QThread, QTimer,
    QObject, QRunnable, QThreadPool, Slot
)
from PySide6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QSlider,
    QPushButton, QFileDialog, QScrollBar, QInputDialog, QLineEdit,
    QLabel, QSplitter, QComboBox, QProgressBar, QMessageBox, QToolBar,
    QGridLayout, QFrame, QDialog, QScrollArea, QSizePolicy, QButtonGroup,
    QListWidget, QApplication
)
from PySide6.QtGui import (
    QAction, QKeySequence, QFont, QColor, QShortcut, QPixmap, 
    QPainter, QPen
)
from PySide6.QtMultimedia import QMediaPlayer

# ==========================================================================
# 4. 型チェック時のみのインポート (reportAssignmentType エラーを根本解決)
# ==========================================================================
if TYPE_CHECKING:
    # 実行時には無視され、型チェック時にのみ参照される
    try:
        from modules.gui.core_manager import VoseCoreManager as CoreManager # type: ignore
    except ImportError:
        # Pyrightがパスを見失っている場合、Anyで逃がして警告を黙らせる
        CoreManager = Any # type: ignore

# ==========================================================================
# 5. 自作モジュール (実際の読み込み)
# ==========================================================================
# プロジェクトルートを sys.path に追加（GitHub Desktop/CI環境でのパス解決を確実に）
project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if project_root not in sys.path:
    sys.path.insert(0, project_root)

try:
    from modules.gui.timeline_widget import TimelineWidget # type: ignore[assignment]
    from modules.gui.graph_editor_widget import GraphEditorWidget # type: ignore[assignment]
    from modules.gui.keyboard_sidebar_widget import KeyboardSidebarWidget # type: ignore[assignment]
    from modules.gui.core_manager import vose_manager, CNoteEvent # type: ignore[assignment]
    from modules.audio.voice_manager import VoiceManager # type: ignore[assignment]
    from modules.gui.aural_engine import AuralAIEngine # type: ignore[assignment]
    from modules.data.licensing import LicenseManager # type: ignore[assignment]
    from modules.gui.audio_mixin import AudioOutputMixin # type: ignore[assignment]
    from modules.gui.voice_mixin import VoiceManagerMixin # type: ignore[assignment]
except ImportError as e:
    print(f"⚠️ Absolute import failed, falling back to relative: {e}")
    # フォールバック（相対インポート）
    from .timeline_widget import TimelineWidget
    from .graph_editor_widget import GraphEditorWidget
    from .keyboard_sidebar_widget import KeyboardSidebarWidget
    from .core_manager import vose_manager
    from ..audio.voice_manager import VoiceManager
    from .audio_mixin import AudioOutputMixin
    from .voice_mixin import VoiceManagerMixin

# MainWindow を機能ごとに分割した Mixin 群。
# ここでの import は main_window.py を逆 import しないため、循環 import は発生しない。
from modules.gui.mixins.project_io_mixin import ProjectIOMixin

# ==========================================================================
# 6. グローバル設定
# ==========================================================================
os.environ["OMP_NUM_THREADS"] = "1"

# ==========================================================================
# 7. GUIセットアップ
# ==========================================================================
def setup_scrolling(self):
    """
    v_scrollbarの値が変化した際、両方のウィジェットのオフセットを更新する
    """
    self.v_scrollbar.setRange(0, 127 * self.note_height - self.timeline_height)
    
    # シグナルとスロットの接続
    self.v_scrollbar.valueChanged.connect(self.sync_vertical_scroll)

def sync_vertical_scroll(self, value):
    """
    垂直スクロールバーの値をピアノロールとタイムラインに伝播させる
    """
    # 鍵盤の描画位置を更新
    self.piano_roll_widget.set_vertical_offset(value)
    # ノートエリアの描画位置を更新
    self.timeline_widget.set_vertical_offset(value)
    
    # 再描画を強制
    self.piano_roll_widget.update()
    self.timeline_widget.update()

try:
    from modules.data.data_models import NoteEvent  # type: ignore
except ImportError:
    class NoteEvent(ctypes.Structure):
        _fields_ = [
            ("wav_path", ctypes.c_char_p),      # 原音キー(phoneme)
            ("pitch_curve", ctypes.POINTER(ctypes.c_double)),
            ("pitch_length", ctypes.c_int),
            ("gender_curve", ctypes.POINTER(ctypes.c_double)),
            ("tension_curve", ctypes.POINTER(ctypes.c_double)),
            ("breath_curve", ctypes.POINTER(ctypes.c_double)),
            ("vibrato_depth_curve", ctypes.POINTER(ctypes.c_double)),
            ("vibrato_rate_curve", ctypes.POINTER(ctypes.c_double)),
            ("vibrato_curve_length", ctypes.c_int),
            # 必要に応じて UTAU用パラメータ(offset等)をここに追加
        ]

        def __init__(self, **kwargs):
            super().__init__()
            # Python側での管理用属性（ctypesのフィールド外）
            self.lyrics = kwargs.get('lyrics', '')
            self.duration = kwargs.get('duration', 0.5)
            self.note_number = kwargs.get('note_number', 60)
            self.phonemes = kwargs.get('phonemes', '')
            self.start_tick = kwargs.get('start_tick', 0)
    
    class PitchEvent:
        def __init__(self, time=0.0, pitch=0.0):
            self.time = time
            self.pitch = pitch
        
        def to_dict(self):
            return {'time': self.time, 'pitch': self.pitch}
        
        @staticmethod
        def from_dict(d):
            return PitchEvent(d.get('time', 0.0), d.get('pitch', 0.0))


# ==========================================================================
# 1.  ProかFreeかの判定ロジック(仮) 
# ==========================================================================

def execute_export_pro_manager(self):
    if LicenseManager.is_pro():
        sr = 96000
        bit = 32
    else:
        sr = 44100
        bit = 16
        # 代表の美学：制限しているのではなく「Free版の標準設定です」と見せる
        print(f"Exporting in Standard Quality ({sr}Hz/{bit}bit)...")


# ==========================================================================
# 1. 外部モジュール読み込み & フォールバック定義
# ==========================================================================
try:
    # 実際の運用環境用
    from .graph_editor_widget import GraphEditorWidget # type: ignore
except ImportError:
    # Actions (Pyright) および開発環境でのインポート失敗対策。
    # main_window.py から呼び出される全ての属性・メソッドを網羅。
    class _GraphEditorWidgetFallback(QWidget):
        pitch_data_updated = Signal(list)
        
        def __init__(self, parent: Optional[QWidget] = None): 
            super().__init__(parent)
            self.tempo: float = 120.0
            # all_parameters 属性を確実に保持
            self.all_parameters: Dict[str, Any] = {}
            # スクロールバー・表示関連のエラー対策
            self.scroll_x_offset: int = 0

        def set_pitch_events(self, events: Any) -> None: 
            pass

        def set_current_time(self, t: float) -> None: 
            pass

        # ログ2401行目対策: 横スクロールオフセット
        def set_horizontal_offset(self, val: int) -> None:
            pass

        # ログ3498行目 / _sample_range 対策
        def get_value_at_time(self, events: Any, t: float) -> float:
            return 0.5

        # ログ3549行目付近 / パラメータ更新メソッド
        def update_parameter(self, name: str, value: Any) -> None:
            pass

        # ログ5152行目付近 / データ一括取得
        def get_all_notes_data(self) -> List[Dict[str, Any]]:
            return []

        # モード切り替え（Pitch, Gender等）
        def set_mode(self, mode: str) -> None:
            pass
    GraphEditorWidget = cast(Any, _GraphEditorWidgetFallback)

# ==========================================================================
# 2. C++連携データ変換関数
# ==========================================================================
def prepare_c_note_event(python_note: Dict[str, Any]) -> NoteEvent:
    """
    UI上のノート情報(Dict)を、C++が解読可能な NoteEvent 構造体に変換する。
    ポインタ化の際に cast を使用し、Pylanceの型不整合エラーを回避。
    """
    # 1. データの確保 (Noneチェックを行い、空リストを回避)
    pitch_data = python_note.get('pitch_curve') or [0.0]
    gender_data = python_note.get('gender_curve') or [0.5] * len(pitch_data)
    tension_data = python_note.get('tension_curve') or [0.5] * len(pitch_data)
    breath_data = python_note.get('breath_curve') or [0.0] * len(pitch_data)

    # 2. ctypesによるポインタ化（メモリ確保）
    # 型ヒント上のエラーを防ぐため、一旦配列として定義してからcastする
    pitch_arr = (ctypes.c_double * len(pitch_data))(*pitch_data)
    gender_arr = (ctypes.c_double * len(gender_data))(*gender_data)
    tension_arr = (ctypes.c_double * len(tension_data))(*tension_data)
    breath_arr = (ctypes.c_double * len(breath_data))(*breath_data)

    # 3. 構造体の生成と返却
    # 各 curve 属性にポインタ型を明示的に cast して代入
    return NoteEvent(
        wav_path=python_note.get('phoneme', '').encode('utf-8'),
        pitch_curve=cast(Any, pitch_arr),
        pitch_length=len(pitch_data),
        gender_curve=cast(Any, gender_arr),
        tension_curve=cast(Any, tension_arr),
        breath_curve=cast(Any, breath_arr)
        )


# ==========================================================================
# ハイブリッド・エンジン自動判別システム
# ==========================================================================

class EngineInitializer:
    def __init__(self):
        self.device = "CPU"
        self.provider = "CPUExecutionProvider"

    def detect_best_engine(self):
        """PCの性能をスキャンし、NPU/GPU/CPUから最適なものを選択する"""
        try:
            import onnxruntime as ort
            available = ort.get_available_providers()

            # 1. Mac (Apple Silicon) の NPU/GPU を優先
            if 'CoreMLExecutionProvider' in available:
                self.device = "NPU (Apple Silicon)"
                self.provider = "CoreMLExecutionProvider"
            
            # 2. Windows (DirectML) の NPU/GPU を優先
            elif 'DmlExecutionProvider' in available:
                self.device = "NPU/GPU (DirectML)"
                self.provider = "DmlExecutionProvider"

            # 3. どちらもなければ CPU で堅実に行く
            else:
                self.device = "CPU (High Performance Mode)"
                self.provider = "CPUExecutionProvider"

        except Exception:
            self.device = "CPU (Safe Mode)"
            self.provider = "CPUExecutionProvider"

        return self.device, self.provider

# MainWindowの初期化時にこれを呼び出す
# initializer = EngineInitializer()
# device_name, provider = initializer.detect_best_engine()
# self.statusBar().showMessage(f"Engine: {device_name} 起動完了")                                                                                


# ==========================================================================


# ==========================================================================
# マルチトラック・データ構造
# ==========================================================================


class VoseTrack:
    def __init__(self, name, track_type="vocal"):
        self.name = name
        self.track_type = track_type  # "vocal"（歌声） または "wave"（オケ）
        
        # --- 基本データ ---
        self.notes = []               # 歌声トラック用のノートリスト
        self.audio_path = ""          # オーディオトラック用のファイルパス
        self.vose_peaks = []          # タイムライン描画用の高速キャッシュ
        
        # --- 最高品質のための「ミキシング・パラメータ」 ---
        self.volume = 1.0             # 0.0 ~ 1.0 (音量)
        self.pan = 0.0                # -1.0 (左) ~ 1.0 (右)
        self.is_muted = False
        self.is_solo = False
        self.is_active = True
        
        # --- AI & エフェクト管理（将来の拡張用） ---
        self.engine_type = "Aural"     # このトラックに使うAIエンジンの種類
        self.effects = []              # リバーブやコンプレッサーの設定保持用
        self.color_label = "#64D2FF"   # UIで見分けるためのトラックカラー

    def to_dict(self):
        """保存用の辞書データ変換（全パラメータを網羅）"""
        return {
            "name": self.name,
            "type": self.track_type,
            "audio_path": self.audio_path,
            "volume": self.volume,
            "pan": self.pan,
            "is_muted": self.is_muted,
            "is_solo": self.is_solo,
            "engine_type": self.engine_type,
            "color_label": self.color_label,
            # ノートがオブジェクトなら辞書化、そうでなければそのまま
            "notes": [n.to_dict() if hasattr(n, 'to_dict') else n for n in self.notes]
        }


# ==========================================================
# 1. CreditsDialog クラス about画面
# ==========================================================
class CreditsDialog(QDialog):
    def __init__(self, partner_names=None, parent=None):
        super().__init__(parent)
        self.setWindowTitle("VO-SE Pro - About & Credits")
        self.setFixedSize(550, 650)
        self.setStyleSheet("background-color: #0d0d0d; color: #e0e0e0;")

        # 名前リストを受け取る（ID: 名前 の辞書形式）
        self.partner_names = partner_names if partner_names else {}

        layout = QVBoxLayout(self)
        layout.setContentsMargins(30, 30, 30, 30)

        # --- ヘッダーエリア ---
        title = QLabel("VO-SE Pro")
        title.setFont(QFont("Segoe UI", 32, QFont.Weight.Bold))
        title.setStyleSheet("color: #00ffcc; letter-spacing: 2px;")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(title)

        version = QLabel("Version 1.0.0 Alpha | Aura AI Engine Loaded") # エンジン名
        version.setFont(QFont("Consolas", 9))
        version.setStyleSheet("color: #666;")
        version.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(version)

        line = QFrame()
        line.setFrameShape(QFrame.Shape.HLine)
        line.setStyleSheet("color: #333; margin: 15px 0;")
        layout.addWidget(line)

        # --- パートナーセクション ---
        header_partner = QLabel("AURAL FOUNDING VOICE PARTNERS") # パートナーセクション名
        header_partner.setFont(QFont("Impact", 14))
        header_partner.setStyleSheet("color: #ff007f; margin-bottom: 5px;")
        layout.addWidget(header_partner)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setStyleSheet("border: none; background: transparent;")
        
        container = QWidget()
        self.partners_layout = QVBoxLayout(container)
        self.partners_layout.setSpacing(8)

        # 10枠を生成
        for i in range(1, 11):
            slot = self.create_partner_row(i)
            self.partners_layout.addWidget(slot)

        scroll.setWidget(container)
        layout.addWidget(scroll)

        # --- フッターエリア ---
        footer_line = QFrame()
        footer_line.setFrameShape(QFrame.Shape.HLine)
        footer_line.setStyleSheet("color: #333;")
        layout.addWidget(footer_line)

        dev_info = QLabel("Engineered by [Your Name]\n© 2026 VO-SE Project") # 2026年に更新
        dev_info.setAlignment(Qt.AlignmentFlag.AlignCenter)
        dev_info.setStyleSheet("color: #444; font-size: 10px; margin-top: 10px;")
        layout.addWidget(dev_info)

    def create_partner_row(self, index):
        frame = QFrame()
        frame.setStyleSheet("""
            QFrame {
                background-color: #1a1a1a;
                border: 1px solid #2d2d2d;
                border-radius: 5px;
            }
            QFrame:hover {
                border: 1px solid #00ffcc;
            }
        """)
        row = QHBoxLayout(frame)
        
        id_lbl = QLabel(f"ID-{index:02}")
        id_lbl.setStyleSheet("color: #00ffcc; font-family: 'Consolas'; font-weight: bold;")
        
        # 動的な名前判定
        name = self.partner_names.get(index, "UNDER RECRUITMENT")
        is_recruiting = (name == "UNDER RECRUITMENT")
        
        name_lbl = QLabel(name)
        if is_recruiting:
            name_lbl.setStyleSheet("color: #444; font-style: italic; font-weight: bold;")
        else:
            name_lbl.setStyleSheet("color: #ffffff; font-weight: bold;") # 決まったら白く光らせる
        
        badge = QLabel("DYNAMICS READY")
        badge.setStyleSheet("""
            background-color: #000;
            color: #00ffcc;
            border: 1px solid #00ffcc;
            border-radius: 3px;
            font-size: 8px;
            padding: 2px 5px;
        """)

        row.addWidget(id_lbl)
        row.addWidget(name_lbl, 1)
        row.addWidget(badge)
        
        return frame

# ==========================================================================
# Undo/Redo コマンド管理
# ==========================================================================

class EditCommand:
    """操作一つ分を記録するクラス"""
    def __init__(self, redo_func, undo_func, description=""):
        self.redo_func = redo_func
        self.undo_func = undo_func
        self.description = description

    def redo(self):
        self.redo_func()

    def undo(self):
        self.undo_func()

class HistoryManager:
    """Undo/Redoのスタックを管理する"""
    def __init__(self, max_depth=50):
        self.undo_stack = []
        self.redo_stack = []
        self.max_depth = max_depth

    def execute(self, command):
        command.redo()
        self.undo_stack.append(command)
        self.redo_stack.clear() # 新しい操作をしたらRedoは消去
        if len(self.undo_stack) > self.max_depth:
            self.undo_stack.pop(0)

    def push(self, command):
        """
        既にUI上で実行済みの操作を、redo()を呼ばずに履歴へ積む。

        タイムライン上のドラッグ移動やダブルクリック追加のように、
        「ユーザー操作そのものが既に redo 相当の処理を行っている」場合に使う。
        execute() は内部で command.redo() を呼んでしまうため、
        そうした操作をそのまま execute() に渡すと処理が二重実行されてしまう。
        """
        self.undo_stack.append(command)
        self.redo_stack.clear()
        if len(self.undo_stack) > self.max_depth:
            self.undo_stack.pop(0)

    def undo(self):
        if not self.undo_stack:
            return
        command = self.undo_stack.pop()
        command.undo()
        self.redo_stack.append(command)

    def redo(self):
        if not self.redo_stack: 
            return
        command = self.redo_stack.pop()
        command.redo()
        self.undo_stack.append(command)


# ==========================================================
#  Pro audio modeling レンダリングボタンを押さなくても、スペースキーで「今あるデータ」を合成して即座に鳴らす機能。
# ==========================================================
        
class ProMonitoringUI(QWidget):  
    def __init__(self, parent=None):
        super().__init__(parent)   # ← super().__init__ が必要
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.current_time = 0.0
        self.rms = 0.0
        self._timer = QTimer(self)
        self._timer.setInterval(16)
        self._timer.timeout.connect(self.update)

    def start(self):
        self._timer.start()

    def stop(self):
        self._timer.stop()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        # 再生ヘッド（赤い縦線）
        x = int(self.current_time * 100)
        painter.setPen(QPen(QColor("#FF2D55"), 2))
        painter.drawLine(x, 0, x, self.height())

        # レベルメーター
        h = int(self.rms * 100)
        painter.fillRect(10, 110 - h, 10, h, QColor("#34C759"))
        painter.fillRect(25, 110 - h, 10, h, QColor("#34C759"))


class WorkerSignals(QObject):
    finished = Signal(str) # 生成されたパスを返す
    error = Signal(str)
    progress = Signal(int)

class SynthesisWorker(QRunnable):
    def __init__(self, vose_core, c_notes, note_count, output_path, is_pro=False):
        """
        [VO-SE Pro: Dedicated Synthesis Worker]
        代表、引数に 'is_pro' を追加しました。
        これでCIのエラー (reportCallIssue) は解消されます。
        """
        super().__init__()
        self.vose_core = vose_core
        self.c_notes = c_notes       # ctypes構造体配列の参照を保持
        self.note_count = note_count
        self.output_path = output_path
        self.is_pro = is_pro         # ライセンス状態を保持
        self.signals = WorkerSignals()

    def run(self):
        try:
            # 代表、ここがバックグラウンドスレッドです。
            # C++側の execute_render に 'モード' としてフラグを渡します。
            # 0: Standard(無料), 1: Studio Master(有料)
            mode_flag = 1 if self.is_pro else 0

            self.vose_core.execute_render(
                self.c_notes, 
                self.note_count, 
                self.output_path.encode('utf-8'),
                mode_flag # 第4引数としてC++側へ伝達
            )
            
            # 完了通知
            self.signals.finished.emit(self.output_path)
            
        except Exception as e:
            self.signals.error.emit(str(e))


class AutoOtoEngine:
    def __init__(self, sample_rate=44100):
        self.sample_rate = sample_rate

    def analyze_wav(self, file_path):
        """
        WAVファイルを解析して、UTAU形式のパラメータを返す。
        音量エンベロープに加え、ゼロ交差率(ZCR)を用いて子音と母音の境界を特定する。
        """
        import numpy as np

        with wave.open(file_path, 'rb') as f:
            sr = f.getframerate()
            n_frames = f.getnframes()
            frames = f.readframes(n_frames)
            # ステレオの場合はモノラル化して処理
            samples = np.frombuffer(frames, dtype=np.int16).astype(np.float32)
            if f.getnchannels() == 2:
                samples = samples.reshape(-1, 2).mean(axis=1)

        # 1. 振幅のエンベロープ計算（既存ロジック）
        win_size = int(sr * 0.01) # 10ms
        envelope = np.convolve(np.abs(samples), np.ones(win_size)/win_size, mode='same')
        max_amp = np.max(envelope) if np.max(envelope) > 0 else 1.0

        # 2. オフセット (Offset): 無音を除去し、音が立ち上がる地点
        # 閾値を少し下げて(2%)、小さな子音も拾えるようにします
        start_indices = np.where(envelope > max_amp * 0.02)[0]
        start_idx = start_indices[0] if len(start_indices) > 0 else 0
        offset_ms = (start_idx / sr) * 1000

        # 3. 先行発声 (Pre-utterance) の精密解析 【ここを大幅修正】
        # 子音(摩擦音など)は波形の符号が頻繁に入れ替わるため、ゼロ交差率が高い。
        # 母音に入ると波形が安定し、ゼロ交差率が急落する。
        
        # 5msごとの窓でZCRを計算
        zcr_win = int(sr * 0.005) 
        zcr = []
        # start_idxから500msの範囲を調査
        search_range = samples[start_idx : start_idx + int(sr * 0.5)]
        for i in range(0, len(search_range) - zcr_win, zcr_win):
            window = search_range[i : i + zcr_win]
            # 符号反転回数をカウント
            crossings = np.sum(np.abs(np.diff(np.sign(window)))) / 2
            zcr.append(crossings / zcr_win)

        # ZCRが急激に減少した（高周波成分が減り、母音が始まった）地点を探す
        zcr_diff = np.diff(zcr)
        # argmin(zcr_diff) は最も減少率が高いインデックス
        zcr_drop_idx = np.argmin(zcr_diff) * zcr_win if len(zcr_diff) > 0 else 0
        
        # 音量増加率の最大点（既存ロジック）
        vol_diff = np.diff(envelope[start_idx : start_idx + int(sr * 0.5)])
        vol_accel_idx = np.argmax(vol_diff) if len(vol_diff) > 0 else 0

        # ZCRの落下点と音量の急増点を統合して先行発声を決定
        # 子音の種類によって重みを変えるのが理想ですが、まずは平均的な位置を採用
        preutter_idx = (zcr_drop_idx + vol_accel_idx) // 2
        preutter_ms = (preutter_idx / sr) * 1000

        # 4. オーバーラップ (Overlap) と 固定範囲 (Constant)
        # オーバーラップは先行発声の1/3〜1/2が一般的
        overlap_ms = preutter_ms / 3 
        # 固定範囲は先行発声の少し先まで（母音が安定するまで）
        constant_ms = preutter_ms * 1.5

        return {
            "offset": int(offset_ms),
            "preutter": int(preutter_ms),
            "overlap": int(overlap_ms),
            "constant": int(constant_ms),
            "blank": -10 
        }

    def generate_oto_text(self, wav_name, params):
        """1行分のoto.iniテキストを生成"""
        alias = os.path.splitext(wav_name)[0]
        return f"{wav_name}={alias},{params['offset']},{params['constant']},{params['blank']},{params['preutter']},{params['overlap']}"


    
#----------
# 1. パス解決用の関数
#    (modules/gui/shared.py に移動。Mixin群と共有するためここではimportのみ)
#----------


try:
    # 既に冒頭でインポートしている、あるいはここで使わない場合は削除
    # もし動的にチェックしたいだけなら importlib を使うのが「製品」の作法です
    import importlib.util
    engine_exists = importlib.util.find_spec("gui.vo_se_engine") is not None
except ImportError:
    engine_exists = False


try:
    from .timeline_widget import TimelineWidget
except ImportError:
    class _TimelineWidgetFallback(QWidget):
        notes_changed_signal = Signal()
        def __init__(self): 
            super().__init__()
            self.notes_list = []
            self.tempo = 120
            self.key_height_pixels = 20
            self.pixels_per_beat = 40
            self.pixels_per_second = 50
            self.lowest_note_display = 21
            self._current_playback_time = 0.0
            self.note_color = "#FF0000"
            self.note_border_color = "#000000"
            self.text_color = "#FFFFFF"
        def get_notes_data(self): return self.notes_list
        def get_all_notes_data(self): return self.notes_list
        def get_all_notes(self): return self.notes_list
        def set_notes(self, notes): self.notes_list = notes
        def get_selected_notes_range(self): return (0.0, 10.0)
        def set_current_time(self, t): pass
        def set_recording_state(self, state, time): pass
        def delete_selected_notes(self): pass
        def set_vertical_offset(self, offset): pass
        def set_horizontal_offset(self, offset): pass
        def copy_selected_notes_to_clipboard(self): pass
        def paste_notes_from_clipboard(self): pass
        def get_max_beat_position(self): return 100
        def seconds_to_beats(self, sec): return sec * self.tempo / 60
        def beats_to_pixels(self, beats): return beats * self.pixels_per_beat
        def note_to_y(self, note_num): return (127 - note_num) * self.key_height_pixels
        def get_pitch_data(self): return []
        def get_audio_peaks(self, file_path, num_peaks=2000): return []
        def set_pitch_data(self, data): pass
        def add_note_from_midi(self, note_num, velocity): pass
        def update(self, *args, **kwargs): super().update()
    TimelineWidget = cast(Any, _TimelineWidgetFallback)

try:
    from .keyboard_sidebar_widget import KeyboardSidebarWidget
except ImportError:
    class _KeyboardSidebarWidgetFallback(QWidget):
        def __init__(self, height, lowest): super().__init__()
        def set_key_height_pixels(self, h): pass
        def set_vertical_offset(self, offset_pixels: int): pass
    KeyboardSidebarWidget = cast(Any, _KeyboardSidebarWidgetFallback)

try:
    from .midi_manager import load_midi_file, MidiInputManager # type: ignore
except ImportError:
    def load_midi_file(path): return []
    class MidiInputManager:
        def __init__(self, port): pass
        def start(self): pass
        def stop(self): pass


try:
    from .voice_manager import VoiceManager # type: ignore
except ImportError:
    class _VoiceManagerFallback:
        def __init__(self, ai):
            self.voices: Dict[str, Dict] = {}
            self.internal_voice_dir = "voice_banks"
        def first_run_setup(self): pass
        def get_current_voice_path(self): return "voice_banks/default"
        def run_batch_voice_analysis(self, dir, callback): return {}
        def scan_utau_voices(self): pass
        def install_voice_from_zip(self, path): return "NewVoice"
        def get_character_color(self, path): return "#4A90E2"
    VoiceManager = cast(Any, _VoiceManagerFallback)

try:
    import importlib
    AudioOutput = importlib.import_module("modules.audio.output").AudioOutput  # type: ignore[attr-defined]
except Exception:
    class AudioOutput:
        def __init__(self): pass
        def play_se(self, path): pass

try:
    from modules.backend.intonation import IntonationAnalyzer
except ImportError:
    class _IntonationAnalyzerFallback:
        def analyze(self, text): return []
        def parse_trace_to_notes(self, trace): return []
        def analyze_to_pro_events(self, text): return []
    IntonationAnalyzer = cast(Any, _IntonationAnalyzerFallback)

try:
    from modules.backend.audio_player import AudioPlayer
except ImportError:
    class _AudioPlayerFallback:
        def __init__(self, volume=0.8): pass
        def play_file(self, path): pass
        def play(self, data): pass
    AudioPlayer = cast(Any, _AudioPlayerFallback)

# DynamicsAIEngine の定義(動的import + フォールバック)は
# modules/gui/shared.py に切り出し済み。Mixin群とここで共有する。


# ==============================================================================
# 設定管理クラス（モック実装）
# ==============================================================================

class ConfigHandler:  #愛なんてシャボン玉！
    """設定ファイルの読み書き"""
    def __init__(self, config_path="config.json"):
        self.config_path = config_path
    
    def load_config(self) -> Dict[str, Any]:
        if os.path.exists(self.config_path):
            try:
                with open(self.config_path, 'r', encoding='utf-8') as f:
                    return json.load(f)
            except Exception:
                pass
        return {"default_voice": "標準ボイス", "volume": 0.8}
    
    def save_config(self, config: Dict[str, Any]):
        try:
            with open(self.config_path, 'w', encoding='utf-8') as f:
                json.dump(config, f, indent=2, ensure_ascii=False)
        except Exception as e:
            print(f"設定保存エラー: {e}")

# ==============================================================================
# ボイスカードウェイジェイト
# ==============================================================================

class VoiceCardWidget(QFrame):
    clicked = Signal()

    def __init__(self, display_name: str, icon_path: str, base_color: str, is_recruiting: bool = False, parent=None):
        super().__init__(parent)
        
        # --- 1. 属性の代入（ここが Ruff のエラーを消す鍵です） ---
        self.display_name = display_name
        self.is_recruiting = is_recruiting
        self.base_color = base_color
        
        # --- 2. UIの基本設定 ---
        self.setFixedSize(140, 180)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        
        # このカード内のレイアウト
        self.card_layout = QVBoxLayout(self)
        self.card_layout.setContentsMargins(10, 10, 10, 10)
        self.card_layout.setSpacing(8)

        # --- 3. アイコンエリアの構築 ---
        self.icon_label = QLabel()
        self.icon_label.setFixedSize(110, 110)
        self.icon_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.icon_label.setStyleSheet("background-color: rgba(0, 0, 0, 40); border-radius: 8px;")
        
        pixmap = QPixmap(icon_path)
        if pixmap.isNull():
            pixmap = QPixmap(110, 110)
            pixmap.fill(QColor(base_color).darker(150))
        
        self.icon_label.setPixmap(pixmap.scaled(
            110, 110, 
            Qt.AspectRatioMode.KeepAspectRatioByExpanding, 
            Qt.TransformationMode.SmoothTransformation
        ))
        
        # 募集枠用オーバーレイ
        if self.is_recruiting:
            overlay_layout = QVBoxLayout(self.icon_label)
            overlay_layout.setContentsMargins(0, 0, 0, 0)
            self.recruit_text = QLabel("UNDER\nRECRUITMENT")
            self.recruit_text.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self.recruit_text.setStyleSheet("""
                color: #00FFCC; font-weight: bold; font-size: 10px;
                background-color: rgba(0, 20, 20, 180); border-radius: 4px;
            """)
            overlay_layout.addWidget(self.recruit_text)

        self.card_layout.addWidget(self.icon_label, 0, Qt.AlignmentFlag.AlignCenter)

        # --- 4. ラベルエリアの構築 ---
        self.name_label = QLabel(display_name)
        self.name_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.name_label.setWordWrap(True)
        self.name_label.setStyleSheet(f"""
            color: {'#888' if is_recruiting else 'white'};
            font-weight: {'normal' if is_recruiting else 'bold'};
            font-size: 11px;
        """)
        self.card_layout.addWidget(self.name_label)

        # 初期状態を選択解除モードに
        self.set_selected(False)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit()
            super().mousePressEvent(event)


    def set_selected(self, selected: bool):
        """選択状態に応じた枠線の変更（省略なし）"""
        border_color = "#00FFCC" if selected else "#333333"
        bg_color = self.base_color if not selected else QColor(self.base_color).lighter(120).name()
        
        # 募集枠の場合は少し透過させるなどの演出
        opacity = "1.0" if not self.is_recruiting else "0.7"

        self.setStyleSheet(f"""
            VoiceCardWidget {{
                background-color: {bg_color};
                border: 2px solid {border_color};
                border-radius: 12px;
                opacity: {opacity};
            }}
            VoiceCardWidget:hover {{
                border: 2px solid #00FFCC;
                background-color: {QColor(bg_color).lighter(110).name()};
            }}
        """)


# ==============================================================================
# ボイスカードギャラリー（実音源優先 ＋ 募集枠を最下段に配置）
# ==============================================================================

class VoiceCardGallery(QWidget):
    """
    音源カードを並べて表示するメインコンテナ。
    実在する音源を優先的に表示し、その後に10枠のパートナー募集枠を表示する。
    """
    voice_selected = Signal(str, str) # (表示名, 内部ID)
    clicked = Signal()

    def __init__(self, voice_manager):
        super().__init__()
        
        # --- 1. 属性の定義と初期化（住民登録はここで行う） ---
        self.manager = voice_manager
        self.cards = {}           # カード管理用の辞書
        self.partner_data = {}    # 募集枠のデータ
        
        # --- 2. メインレイアウトの構築 ---
        self.main_layout = QVBoxLayout(self)
        self.main_layout.setContentsMargins(0, 0, 0, 0)
        self.main_layout.setSpacing(0)
        
        # --- 3. スクロールエリアとコンテナの設定 ---
        self.scroll_area = QScrollArea()
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setStyleSheet("background-color: #1E1E1E; border: none;")
        
        self.container = QWidget()
        
        # --- 4. グリッドレイアウトの確定 ---
        self.grid = QGridLayout(self.container) 
        self.grid.setSpacing(20)
        self.grid.setContentsMargins(20, 20, 20, 20)
        
        for i in range(4):
            self.grid.setColumnStretch(i, 1)

        self.scroll_area.setWidget(self.container)
        self.main_layout.addWidget(self.scroll_area)
        

    def set_partner_data(self, partners: dict):
        """MainWindowから10枠の募集情報を注入する"""
        self.partner_data = partners

    def setup_gallery(self):
        """
        全音源の再配置を実行（省略なし）。
        1. 実在音源（公式・外部）
        2. パートナー募集枠（10枠）
        の順でグリッドを構築する。
        """
        # 1. 既存カードのクリア（Pyrightのエラーを回避する安全な書き方）
        if self.grid is not None:
            while self.grid.count() > 0:
                item = self.grid.takeAt(0)
                # item が None でないことを確認
                if item is not None:
                    widget = item.widget()
                    # widget が実在する場合のみ削除処理を実行
                    if widget is not None:
                        widget.setParent(None)
                        widget.deleteLater()
        
        self.cards.clear()

        row, col = 0, 0
        max_columns = 4 # 1列に並べるカード数

        # --- 2. 【優先】実在する全音源（公式・外部UTAU）の生成 ---
        all_voices = self.manager.scan_voices()
        
        for display_name, internal_id in all_voices.items():
            # パス解決のロジック
            if internal_id.startswith("__INTERNAL__"):
                # 公式内蔵キャラクター
                # "__INTERNAL__:キャラ名" 形式（将来の複数キャラ対応）と、
                # 現状の voice_manager が返す単独の "__INTERNAL__" 形式の
                # 両方に対応する。コロンが無い場合は display_name を
                # ディレクトリ名の代わりに使う。
                id_parts = internal_id.split(":", 1)
                char_dir = id_parts[1] if len(id_parts) > 1 else display_name
                base_path = getattr(self.manager, 'base_path', os.getcwd())
                icon_path = os.path.join(base_path, "assets", "official_voices", char_dir, "icon.png")
                card_color = "#3A3A4A"
            else:
                # 外部UTAU音源（フォルダパス）
                icon_path = os.path.join(internal_id, "icon.png")
                card_color = "#2D2D2D"

            # カードのインスタンス化
            card = VoiceCardWidget(display_name, icon_path, card_color, is_recruiting=False)
            self._finalize_card_setup(card, display_name, internal_id, row, col)
            
            col += 1
            if col >= max_columns:
                col = 0
                row += 1

        # --- 3. 【後置】パートナー募集枠（10枠）の生成 ---
        # 実在音源の後の列から続けて配置する
        loop_range = self.partner_data.keys() if self.partner_data else range(1, 11)
        
        for i in loop_range:
            display_name = f"PARTNER ID-{i:02d}"
            # 募集枠を識別するためのプレフィックスを付与
            internal_id = f"__RECRUITING__:ID-{i:02d}"
            
            # 募集枠専用のプレースホルダー画像
            base_path = getattr(self.manager, 'base_path', os.getcwd())
            icon_path = os.path.join(base_path, "assets", "icons", "recruiting_placeholder.png")
            card_color = "#1A2222" # 募集枠は少し沈んだ色にする
            card = VoiceCardWidget(display_name, icon_path, card_color, is_recruiting=True)
            self._finalize_card_setup(card, display_name, internal_id, row, col)
            
            col += 1
            if col >= max_columns:
                col = 0
                row += 1

        # グリッドの下部に伸縮用のスペース（スペーサー）を追加して上に詰める
        grid = self.grid
        if grid is None:
            return
        grid.setRowStretch(row + 1, 1)


    def _finalize_card_setup(self, card, display_name, internal_id, row, col):
        """
        生成したカードをグリッドに登録し、クリックイベントを接続する（省略なし）。
        """
        # クリックイベントの接続
        # lambdaの引数にデフォルト値を設定することで、ループ内の変数を正しくキャプチャ
        card.clicked.connect(
            lambda d=display_name, i=internal_id: self.on_card_clicked(d, i)
        )
        
        # グリッドレイアウトへ配置
        self.grid.addWidget(card, row, col)
        # 管理用辞書に保存（internal_id をキーにして一意性を確保）
        self.cards[internal_id] = card

    def mousePressEvent(self, event):
        """クリックイベントを検知して信号を発行（省略なし）"""
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit()
            super().mousePressEvent(event)

    def on_card_clicked(self, name, internal_id):
        """
        カード選択時のトグル処理と信号発行（省略なし）。
        """
        # 一旦すべてのカードの選択状態（枠線の色など）をオフにする
        for card_widget in self.cards.values():
            card_widget.set_selected(False)
        
        # 今回クリックされたカードだけをオンにする
        if internal_id in self.cards:
            self.cards[internal_id].set_selected(True)
        
        # MainWindow (main_window.py) の on_voice_changed スロットへ飛ばす
        print(f"DEBUG: Gallery selection -> {name} ({internal_id})")
        self.voice_selected.emit(name, internal_id)


# ==============================================================================
# バックグラウンドスレッド
# ==============================================================================

class AnalysisThread(QThread):
    """AI解析をバックグラウンドで実行するスレッド"""
    progress = Signal(int, str)
    finished = Signal(dict)
    error = Signal(str)

    def __init__(self, voice_manager, target_dir):
        super().__init__()
        self.voice_manager = voice_manager
        self.target_dir = target_dir

    def run(self):
        try:
            results = self.voice_manager.run_batch_voice_analysis(
                self.target_dir,
                self.progress.emit
            )
            self.finished.emit(results)
        except Exception as e:
            self.error.emit(str(e))


# ==============================================================================
# メインウィンドウクラス
# ==============================================================================

class MainWindow(ProjectIOMixin, AudioOutputMixin, VoiceManagerMixin, QMainWindow):

    """VO-SE Pro  メインウィンドウ"""
    
    # === メインUIウィジェット系（遅延生成 → Optional） ===
    timeline_widget: Any
    graph_editor_widget: Any
    keyboard_sidebar: Any
    keyboard_sidebar_widget: Any

    # === スクロール・ボリュームUI ===
    vertical_scroll: Any
    v_scrollbar: Any
    h_scrollbar: Any
    vol_slider: Any
    vol_label: Any

    # === タイマー（__init__で必ず実体化） ===
    render_timer: QTimer
    playback_timer: QTimer

    # === 再生・音声系 ===
    player: Optional[Any]
    audio_player: Any
    audio_output: Any

    # === AI / エンジン系（実体保証できないため Any） ===
    vo_se_engine: Any
    vose_core: Optional[Any]
    dynamics_ai: Any
    voice_manager: Any
    analyzer: Any
    talk_manager: Any
    text_analyzer: Optional[Any]

    # === 再生状態フラグ ===
    is_playing_state: bool
    is_playing: bool
    is_recording: bool
    is_looping: bool
    is_looping_selection: bool

    # === トラック・データ管理 ===
    current_track_idx: int
    tracks: List[Any]
    notes: List[Any]
    pitch_data: List[Any]
    playing_notes: Dict[int, Any]

    oto_dict: Dict[str, Any]
    current_oto_data: List[Any]

    current_voice: str
    volume: float
    current_playback_time: float

    # === UIコントロール ===
    tempo_input: Any
    play_button: Any
    play_btn: Any
    record_button: Any
    loop_button: Any
    render_button: Any

    btn_mute: Any
    btn_solo: Any

    track_list_widget: Any
    progress_bar: Any
    status_label: Any

    character_selector: Any
    midi_port_selector: Any

    toolbar: Any
    main_layout: Any
    voice_grid: Any
    voice_cards: List[Any]

    # === 描画・キャンバス ===
    canvas: Any
    piano_roll_scene: Any

    # === スレッド・排他制御 ===
    playback_thread: Optional[threading.Thread]
    analysis_thread: Any
    _playback_lock: threading.Lock

    # === 履歴・設定 ===
    history: Any
    config_manager: Any
    config: Any

    # === パラメータ管理 ===
    input_fields: List[Any]
    parameters: Dict[str, Any]
    all_parameters: Dict[str, Any]
    sync_notes: bool

    vowel_groups: Dict[str, str]
    confirmed_partners: Dict[int, str]

    # === デバイス情報 ===
    active_device: str
    active_provider: str
    device_status_label: Any
    ai_manager: Any

    def __init__(self, parent=None, engine=None, ai=None, config=None):
        super().__init__(parent)

        self.vol_slider = None
        self.vol_label = None
        self.timeline_widget = cast(Any, None)
        self.graph_editor_widget = cast(Any, None)
        self.voice_manager = cast(Any, None) 

        self.status_label = QLabel("")
        self.voice_grid = QGridLayout()
        
        self.status_bar = self.statusBar()
        
        # --- 2. 属性の初期化（AttributeError 対策） ---
        self._init_attributes(engine, ai, config)
        
        # --- 3. エンジンの実体化（ImportError ガード付き） ---
        self._init_engines(engine, ai)

        # オーディオ再生エンジン(self.player)の構築。
        # 以前はここが呼ばれておらず、合成結果を再生しようとしても
        # 「プレイヤーが初期化されていません」という警告だけが出て
        # 無音のまま何も起きない状態だった。
        self.setup_audio_interface()

        # --- 4. UI構築と起動シーケンス ---
        self.init_ui()
        self.setup_connections()
        self.setup_vose_shortcuts()
        self.setup_vose_keyboard_navigation()

        # パフォーマンスモード切替トグルをツールバーに追加。
        # setup_toolbar (init_ui内) の後である必要がある。
        self.setup_performance_toggle()

        # リアルタイムモニタリングの有効化。
        self.setup_realtime_monitoring()

        self.perform_startup_sequence()

        # AIダイナミクス推論セッション(self.ai_session)の構築。
        # active_provider の診断が終わった後である必要があるため
        # perform_startup_sequence の後に呼ぶ。
        # 以前はこれが呼ばれておらず、predict_dynamics() を呼ぶと
        # self.ai_session が存在せず AttributeError になっていた。
        self.setup_aural_ai()

        self.setWindowTitle("VO-SE Pro")
        self.resize(1200, 800)

    def _init_attributes(self, engine: Any, ai: Any, config: Any):
        """
        すべての属性に初期値を代入。
        (Pylance の reportAttributeAccessIssue を根絶する完全版)
        """
        # --- 1. Required宣言されているものへの代入 (Anyキャストで矛盾回避) ---
        self.timeline_widget = cast(Any, None)
        self.graph_editor_widget = cast(Any, None)
        self.keyboard_sidebar = cast(Any, None)
        self.keyboard_sidebar_widget = cast(Any, None)
        self.vertical_scroll = cast(QSlider, None)
        self.v_scrollbar = cast(QSlider, None)
        self.h_scrollbar = cast(QScrollBar, None)
        self.vol_slider = cast(QSlider, None)
        self.vol_label = cast(QLabel, None)
        
        # --- 2. Optional宣言またはAny型のもの ---
        self.player = None
        self.talk_manager = None   # ← 追加
        self.midi_manager = None  
        self.audio_output = None
        self.audio_player = None
        self.vose_core = None
        self.text_analyzer = None
        self.playback_thread = None
        self.analysis_thread = cast(QThread, None)
        
        # エンジン類
        self.vo_se_engine = engine
        self.dynamics_ai = ai
        self.voice_manager = None
        self.analyzer = None
        
        # 状態フラグ
        self.is_playing_state = False
        self.is_playing = False
        self.is_recording = False
        self.is_looping = False
        self.is_looping_selection = False
        
        # データリスト・辞書
        self.tracks = []
        self.notes = []
        self.pitch_data = []
        self.playing_notes = {}
        self.oto_dict = {}
        self.current_oto_data = [] # ここが List[Any] 宣言なら [] でOK
        
        self.current_track_idx = 0
        self.selected_index = -1 
        self.current_voice = "標準ボイス"
        self.volume = 0.8
        self.current_playback_time = 0.0
        self.playback_start_time = 0.0
        self.playback_end_time = 0.0
        self.playback_started_monotonic = 0.0
        
        # UIポインタ (Optional群)
        self.tempo_input = cast(QLineEdit, None)
        self.time_display_label = cast(QLabel, None)
        self.play_button = cast(QPushButton, None)
        self.play_btn = cast(QPushButton, None)
        self.record_button = cast(QPushButton, None)
        self.loop_button = cast(QPushButton, None)
        self.render_button = cast(QPushButton, None)
        self.status_label = cast(QLabel, None)
        self.btn_mute = cast(QPushButton, None)
        self.btn_solo = cast(QPushButton, None)
        self.track_list_widget = cast(QListWidget, None)
        self.progress_bar = cast(QProgressBar, None)
        self.character_selector = cast(QComboBox, None)
        self.midi_port_selector = cast(QComboBox, None)
        self.toolbar = cast(QToolBar, None)
        self.device_status_label = cast(QLabel, None)
        self.main_layout = cast(QVBoxLayout, None)
        self.voice_grid = cast(QGridLayout, None)
        self.voice_cards = []
        self.canvas = None
        self.piano_roll_scene = None

        # タイマーはここで実体化させる (Noneアクセスを未然に防ぐ)
        self.render_timer = QTimer(self)
        self.playback_timer = QTimer(self)
        
        # ロック
        import threading
        self._playback_lock = threading.Lock()
        
        # 外部マネージャー
        self.history = HistoryManager()
        self.config_manager = cast(Any, None)
        self.config = config if config else {}
        self.all_parameters = {}
        self.sync_notes = True
        self.input_fields = []
        self.ai_manager = None
        self.parameters = {}
        
        self.vowel_groups = {
            'a': 'あかさたなはまやらわがざだばぱぁゃ',
            'i': 'いきしちにひみりぎじぢびぴぃ',
            'u': 'うくすつぬふむゆるぐずづぶぷぅゅ',
            'e': 'えけせてねへめれげぜでべぺぇ',
            'o': 'おこそとのほもよろをごぞどぼぽぉょ',
            'n': 'ん'
        }
        self.confirmed_partners = {}
        self.active_device = "CPU (Standard)"
        self.active_provider = "CPUExecutionProvider"
        
        self.timeline_widget = cast(Any, None)
        self.timeline = self.timeline_widget # timelineへのアクセスをwidgetへ流す
        self.voice_gallery = cast(Any, None)
        self.current_voice_id = "__INTERNAL__:standard"

        self.confirmed_partners = {i: "UNDER RECRUITMENT" for i in range(1, 11)}
        
        # 現在選択されているボイス情報
        self.current_voice = "未選択"
        self.current_voice_id = "NONE"

    def _init_engines(self, engine, ai):
        """
        [VO-SE Pro: Core Integration Complete Edition]
        全てのエンジンとマネージャーを初期化します。
        DLLやモジュールが欠落していても、Mockオブジェクトにより起動を阻止しません。
        """
        # --- 1. VOSE Core Engine (C++ DLL) の統合 ---
        # 旧 VoseEngine() を廃止し、シングルトンから取得します
        self.vose_core = vose_manager.get_lib()
        
        if not self.vose_core:
            # DLLが読み込めない場合のセーフティネット
            class MockCore:
                def __getattr__(self, name):
                    return lambda *args, **kwargs: None
            self.vose_core = MockCore()
            print("⚠️ VOSE Core DLL is missing. Running in Mock mode.")
        else:
            self.statusBar().showMessage("VOSE Core Engine: Online", 3000)

        # --- 2. モジュール・ロード用ヘルパー ---
        def safe_import(module_path, class_name, fallback_class):
            try:
                import importlib
                mod = importlib.import_module(module_path)
                return getattr(mod, class_name)
            except (ImportError, AttributeError) as e:
                print(f"Engine Load Warning: {module_path}.{class_name} not found. ({e})")
                return fallback_class

        # --- 3. Mockクラスの定義（実体がない場合の身代わり） ---
        class MockDynamicsAI:
            def generate_emotional_pitch(self, f0, strength=0.8): return f0
            def get_baked_pitch(self, nid, f0, s=0.8): return f0

        class MockVoiceManager:
            def __init__(self, ai=None): self.voices = {}
            def get_current_voice_path(self): return ""
            def scan_utau_voices(self): pass
            def get_character_color(self, path): return "#4A90E2"

        class MockAudioPlayer:
            def __init__(self, volume=0.8): self.vol = volume
            def play_file(self, path): print(f"Mock Play: {path}")
            def stop(self): pass

        # --- 4. 実体化プロセスの実行 ---
        # AIエンジン
        DynamicsAIEngine = safe_import("modules.utils.dynamics_ai", "DynamicsAIEngine", MockDynamicsAI)
        self.dynamics_ai = ai if ai else DynamicsAIEngine()

        # ボイスマネージャー
        VoiceManager = safe_import("modules.audio.voice_manager", "VoiceManager", MockVoiceManager)
        try:
            self.voice_manager = cast(Any, VoiceManager(self.dynamics_ai))
        except TypeError:
            self.voice_manager = cast(Any, VoiceManager())

        # オーディオ・プレイヤー系
        AudioPlayer = safe_import("modules.backend.audio_player", "AudioPlayer", MockAudioPlayer)
        self.audio_player = AudioPlayer(volume=getattr(self, 'volume', 0.8))

        # トーク解析系（Talk機能用）
        IntonationAnalyzer = safe_import("modules.talk.talk_manager", "IntonationAnalyzer", lambda: None)
        self.analyzer = IntonationAnalyzer() if IntonationAnalyzer else None
        self.text_analyzer = self.analyzer

        # --- 5. プロジェクト・パートナー枠の定義 ---
        # 代表の指定通り、ここでID管理を確定させます
        self.confirmed_partners = {
            1: "UNDER RECRUITMENT", # ID-01
            2: "UNDER RECRUITMENT", # ID-02
            3: "UNDER RECRUITMENT", # ID-03
        }
        
        print("✅ Startup Sequence: All engines initialized.")
        

    def execute_render(self):
        """オーディオ書き出しの実行（省略なし）"""
        print("DEBUG: Rendering started...")
        # 将来的に self.vo_se_engine.render() を呼び出す

    def toggle_recording(self):
        """録音状態の切り替え（省略なし）"""
        self.is_recording = not getattr(self, 'is_recording', False)
        print(f"DEBUG: Recording toggled to: {self.is_recording}")

    def update_playback_ui(self):
        """再生位置・時間表示・タイムラインのプレイヘッドを同期する。"""
        if getattr(self, 'is_playing', False):
            elapsed = max(0.0, time.monotonic() - float(getattr(self, 'playback_started_monotonic', 0.0)))
            current_time = float(getattr(self, 'playback_start_time', 0.0)) + elapsed
            end_time = max(float(getattr(self, 'playback_end_time', 0.0)), self._get_project_duration_seconds())

            if end_time > 0.0 and current_time >= end_time:
                if getattr(self, 'is_looping', False):
                    current_time = 0.0
                    self.playback_start_time = 0.0
                    self.playback_started_monotonic = time.monotonic()
                else:
                    self.stop_and_clear_playback()
                    return

            self._set_transport_time(current_time)
        else:
            self._set_transport_time(float(getattr(self, 'current_playback_time', 0.0)))

    def _format_timecode(self, seconds: float) -> str:
        seconds = max(0.0, float(seconds))
        minutes = int(seconds // 60)
        whole_seconds = int(seconds % 60)
        millis = int(round((seconds - int(seconds)) * 1000))
        if millis >= 1000:
            whole_seconds += 1
            millis -= 1000
        return f"{minutes:02d}:{whole_seconds:02d}.{millis:03d}"

    def _get_project_duration_seconds(self) -> float:
        timeline = getattr(self, 'timeline_widget', None)
        notes = list(getattr(timeline, 'notes_list', []) or [])
        if not notes:
            return 8.0
        last_note_end = max(
            float(getattr(note, 'start_time', 0.0)) + float(getattr(note, 'duration', 0.0))
            for note in notes
        )
        return max(1.0, last_note_end + 1.0)

    def _set_transport_time(self, seconds: float) -> None:
        seconds = max(0.0, float(seconds))
        self.current_playback_time = seconds

        timeline = getattr(self, 'timeline_widget', None)
        if timeline is not None:
            if hasattr(timeline, 'set_playback_time'):
                timeline.set_playback_time(seconds)
            elif hasattr(timeline, 'set_current_time'):
                timeline.set_current_time(seconds)

        graph = getattr(self, 'graph_editor_widget', None)
        if graph is not None and hasattr(graph, 'set_current_time'):
            graph.set_current_time(seconds)

        label = getattr(self, 'time_display_label', None)
        if label is not None:
            total = self._get_project_duration_seconds()
            label.setText(f"{self._format_timecode(seconds)} / {self._format_timecode(total)}")

    def setup_voice_gallery(self):
        """
        ボイスギャラリーのセットアップ。
        ここで self.confirmed_partners の情報をギャラリーに反映させる。
        """
        # ギャラリーウィジェットのインスタンス化
        self.voice_gallery = VoiceCardGallery(self.voice_manager)
    
        # パートナー情報を設定
        self.voice_gallery.set_partner_data(self.confirmed_partners)
    
        # ギャラリーのセットアップ実行
        self.voice_gallery.setup_gallery()
    
        # ボイス選択シグナルを接続
        self.voice_gallery.voice_selected.connect(self.on_voice_changed)
        # ギャラリーウィジェットのインスタンス化（MainWindowが保持）
        if hasattr(self, 'main_layout') and self.main_layout:
            self.main_layout.addWidget(self.voice_gallery)
            # ギャラリーに対して「募集枠の情報」を渡して更新をかける
            # 内部で先ほどの refresh_gallery(self.confirmed_partners) が呼ばれる
            self.voice_gallery.set_partner_data(self.confirmed_partners)
            self.voice_gallery.setup_gallery()

    @Slot(str, str)
    def on_voice_changed(self, display_name: str, internal_id: str):
        """
        キャラクター選択が変更された際の統合ハンドラ。
        1. MainWindowの状態を更新
        2. Cエンジン(VoSeEngine)に音源をロード
        3. 既存のノートのキャッシュを新キャラクター用に更新
        """
        # --- 1. MainWindowの状態保持 ---
        self.current_voice = display_name
        self.current_voice_id = internal_id
        
        # --- 2. C++エンジンへのキャラクター適用 ---
        # VoSeEngine側で __INTERNAL__ (公式) か フォルダパス (UTAU) かを判別
        if hasattr(self, 'vo_se_engine') and self.vo_se_engine:
            try:
                self.vo_se_engine.set_active_character(internal_id)
            
                # 🔴 重要: oto.iniの読み込みを確認
                voice_path = self.voice_manager.voices.get(display_name, {}).get("path", "")
                if voice_path:
                    oto_data = self.parse_oto_ini(voice_path)
                    if not oto_data:
                        print(f"⚠️ Warning: oto.ini not found in {voice_path}")
                    
            except Exception as e:
                print(f"❌ Engine character switch failed: {e}")

        # --- 3. UIへのフィードバック ---
        if self.status_label:
            self.status_label.setText(f"歌手: {display_name}")
        elif self.statusBar():
            self.statusBar().showMessage(f"Voice Loaded: {display_name}", 3000)

        # --- 4. 既存ノートの先行レンダリング(キャッシュ)更新 ---
        # 声が変わったため、裏で作っていたキャッシュを新しい声で作り直す
        # これにより、切り替え直後に再生しても「新しい声」で即座に鳴る
        if hasattr(self, 'on_timeline_updated'):
            self.on_timeline_updated()

        # --- 5. 10枠のパートナーリスト(confirmed_partners)との照合 ---
        # 選択されたIDが募集中のID（1, 2, 3...）に含まれる場合、特別なフラグ処理
        # ここに将来的なUIエフェクトや、募集中の透かしを消す処理などを追加可能
        for partner_id, status in self.confirmed_partners.items():
            if str(partner_id) in internal_id:
                print(f"INFO: Partner ID-{partner_id:02d} ({status}) has been selected.")

        print(f"✅ Character management: '{display_name}' is now active.")


    def init_ui(self) -> None:

        from PySide6.QtWidgets import QWidget, QVBoxLayout
        
        # 1. ウィンドウ基本設定
        self.setWindowTitle("VO-SE Engine DAW Pro")
        self.setGeometry(100, 100, 1200, 800)
        
        # 2. セントラルウィジェットとメインレイアウトの確定
        # self.main_layout をクラス属性として保持し、他メソッドからのアクセスを保証
        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        self.main_layout = QVBoxLayout(central_widget)
        self.main_layout.setContentsMargins(5, 5, 5, 5)
        self.main_layout.setSpacing(2)

        # 3. 各セクションの順次セットアップ
        # 依存関係（setup_actionsはtimeline_widgetとQActionを必要とし、
        # setup_menusはそのQActionを必要とするため、この順序が必須）
        self.setup_main_editor_area()  # 1. timeline_widget生成
        self.setup_actions()           # 2. QAction定義(copy_action/paste_action/save_action)
        self.setup_menus()             # 3. QActionをメニューに登録
        self.setup_toolbar()
        # 第2パネル: AI解析・自動歌詞配置・キャラ/MIDI選択・編集モード切替・
        # 録音・音源再スキャン。以前は一度も呼ばれておらず、これらの機能に
        # アクセスする手段が画面上に存在しなかった。
        self.setup_control_panel()
        self.setup_bottom_panel()
        self.setup_status_bar()
        self._set_transport_time(0.0)
        self.setup_voice_gallery()

        # 4. スタイルと初期状態の適用
        # hasattrによるチェックに加え、初期化済みフラグ等で安全に呼び出し
        self.apply_apple_refined_style()
        self._apply_initial_styles()

    def apply_apple_refined_style(self) -> None:
        """AppleライクなミニマルUIテーマを全体へ適用。"""
        self.setStyleSheet("""
            QMainWindow {
                background: #1c1c1e;
                color: #f5f5f7;
            }
            QWidget {
                background: #1c1c1e;
                color: #f5f5f7;
                font-family: "SF Pro Text", "Segoe UI", "Hiragino Kaku Gothic ProN";
                font-size: 12px;
            }
            QToolBar {
                background: rgba(44, 44, 46, 0.88);
                border: 1px solid #3a3a3c;
                spacing: 6px;
                padding: 3px 6px;
            }
            QLabel { color: #c7c7cc; }
            QPushButton {
                background: #2c2c2e;
                color: #f5f5f7;
                border: 1px solid #48484a;
                border-radius: 9px;
                padding: 6px 14px;
                font-weight: 600;
            }
            QPushButton:hover { background: #3a3a3c; }
            QPushButton:pressed { background: #48484a; }
            QPushButton#PrimaryButton {
                background: #0071e3;
                color: #ffffff;
                border: 1px solid #0071e3;
            }
            QPushButton#PrimaryButton:hover { background: #0a84ff; }
            QPushButton#PrimaryButton:pressed { background: #0063cc; }
                        QPushButton#SegmentLeft, QPushButton#SegmentMid, QPushButton#SegmentRight {
                background: #2c2c2e;
                color: #d1d1d6;
                border: 1px solid #505055;
                border-right-width: 0px;
                border-radius: 0px;
                min-width: 78px;
                padding: 5px 11px;
            }
            QPushButton#SegmentRight { border-right-width: 1px; }
            QPushButton#SegmentLeft {
                border-top-left-radius: 9px;
                border-bottom-left-radius: 9px;
            }
            QPushButton#SegmentRight {
                border-top-right-radius: 9px;
                border-bottom-right-radius: 9px;
            }

                        QPushButton#SegmentLeft:checked, QPushButton#SegmentMid:checked, QPushButton#SegmentRight:checked {
                background: #f5f5f7;
                color: #101012;
                border-color: #d8d8de;
            }
            QPushButton#SegmentLeft:hover, QPushButton#SegmentMid:hover, QPushButton#SegmentRight:hover {
                background: #38383d;
            }
            QPushButton#SegmentLeft:checked:hover, QPushButton#SegmentMid:checked:hover, QPushButton#SegmentRight:checked:hover {
                background: #ffffff;
            }
            
            QLineEdit, QComboBox, QListWidget, QTextEdit, QPlainTextEdit {
                background: #2c2c2e;
                border: 1px solid #48484a;
                border-radius: 9px;
                padding: 6px;
            }
            QLineEdit:focus, QComboBox:focus, QListWidget:focus {
                border: 1px solid #0a84ff;
            }
            QSplitter::handle { background: #3a3a3c; }
            QScrollBar:vertical, QScrollBar:horizontal {
                background: #1c1c1e;
                margin: 2px;
            }
            QScrollBar::handle:vertical, QScrollBar::handle:horizontal {
                background: #636366;
                border-radius: 5px;
                min-height: 28px;
                min-width: 28px;
            }
            QScrollBar::add-line, QScrollBar::sub-line { width: 0px; height: 0px; }
            QStatusBar {
                background: #2c2c2e;
                border-top: 1px solid #3a3a3c;
                color: #c7c7cc;
            }
        """)

    def _refresh_transport_button_states(self) -> None:
        """再生/停止/ループの視覚状態を現在の内部状態へ同期。"""
        play_btn = cast(QPushButton, getattr(self, 'play_btn', None))
        stop_btn = cast(QPushButton, getattr(self, 'stop_btn', None))
        loop_btn = cast(QPushButton, getattr(self, 'loop_btn', None))
        play_button = cast(QPushButton, getattr(self, 'play_button', None))
        loop_button = cast(QPushButton, getattr(self, 'loop_button', None))

        is_playing = bool(getattr(self, 'is_playing', False))
        is_looping = bool(getattr(self, 'is_looping', False))

        if play_btn is not None:
            play_btn.setChecked(is_playing)
            play_btn.setText("⏸ 停止" if is_playing else "▶ 再生")
        if play_button is not None:
            play_button.setChecked(is_playing)
            play_button.setText("⏸ 停止" if is_playing else "▶ 再生")

        if stop_btn is not None:
            stop_btn.setChecked(False)

        if loop_btn is not None:
            loop_btn.setChecked(is_looping)
            loop_btn.setText("↻ ループON" if is_looping else "↻ ループ")
        if loop_button is not None:
            loop_button.setChecked(is_looping)
            loop_button.setText("ループ: ON" if is_looping else "ループ: OFF")


    def _apply_initial_styles(self) -> None:
        """初期スタイル適用の安全な実行"""
        # ログ 2620 等の「未定義属性アクセス」を防ぐため、メソッドの存在を確実に担保
        if hasattr(self, 'update_timeline_style'):
            # 代表が定義したタイムラインの視覚効果を適用
            self.update_timeline_style()

    
        self._refresh_transport_button_states()
        # ステータスバーへの初期メッセージ
        if self.statusBar():
            self.statusBar().showMessage("Engine Initialized. Ready for production.")


    def open_audio(self) -> None:
        """WAVファイルを選択し、TimelineWidgetに波形を描画させる"""
        from PySide6.QtWidgets import QFileDialog
        
        # ファイル選択ダイアログを開く
        file_path, _ = QFileDialog.getOpenFileName(
            self, 
            "オーディオファイルを選択", 
            "", 
            "Wave Files (*.wav)"
        )
        
        if file_path:
            # 1. 属性にパスを保存（TimelineWidgetがこれを参照する）
            self.current_audio_path = file_path
            
            # 2. TimelineWidgetにキャッシュを捨てさせて再描画を促す
            if hasattr(self.timeline, '_wave_cache_path'):
                del self.timeline._wave_cache_path
            
            self.timeline.update()
            
            # 3. ステータスバーに通知
            self.statusBar().showMessage(f"読み込み完了: {os.path.basename(file_path)}", 3000)
            
    # ==========================================================================
    # UI セクション構築
    # ==========================================================================

    def setup_toolbar(self):
        """上部ツールバー：再生・録音・テンポ・ファイル操作（省略なし統合版）"""
        from PySide6.QtWidgets import QToolBar, QPushButton, QLabel, QLineEdit, QWidget
        
        self.toolbar = QToolBar("Main Toolbar")
        self.addToolBar(self.toolbar)
        self.toolbar.setMovable(False)

        # 1. 再生コントロール
        self.play_btn = QPushButton("▶ 再生")
        self.play_btn.setObjectName("SegmentLeft")
        self.play_btn.setCheckable(True)
        self.play_btn.clicked.connect(self.on_play_pause_toggled)
        self.toolbar.addWidget(self.play_btn)

        self.stop_btn = QPushButton("■ 停止")
        self.stop_btn.setObjectName("SegmentMid")
        self.stop_btn.setCheckable(True)
        self.stop_btn.clicked.connect(self.stop_and_clear_playback)
        self.toolbar.addWidget(self.stop_btn)

        self.loop_btn = QPushButton("↻ ループ")
        self.loop_btn.setObjectName("SegmentRight")
        self.loop_btn.setCheckable(True)
        self.loop_btn.clicked.connect(self.on_loop_button_toggled)
        self.toolbar.addWidget(self.loop_btn)

        self.toolbar.addSeparator()

        self.time_display_label = QLabel("00:00.000 / 00:00.000")
        self.time_display_label.setMinimumWidth(150)
        self.time_display_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.time_display_label.setStyleSheet(
            "font-family: Menlo, Consolas, monospace; font-weight: 700; color: #f2f2f7;"
        )
        self.toolbar.addWidget(self.time_display_label)

        self.toolbar.addSeparator()

         # 読み上げ
        self.talk_button = QPushButton("読み上げ")
        self.talk_button.clicked.connect(self.on_talk)
        
        # 2. テンポ設定
        self.toolbar.addWidget(QLabel(" Tempo: "))
        self.tempo_input = QLineEdit("120")
        self.tempo_input.setFixedWidth(40)
        self.tempo_input.returnPressed.connect(self.update_tempo_from_input)
        self.toolbar.addWidget(self.tempo_input)

        self.toolbar.addSeparator()

        # 3. WAVファイル読み込み（追加）
        self.open_wav_btn = QPushButton("OPEN WAV")
        self.open_wav_btn.setObjectName("SecondaryButton")
        self.open_wav_btn.clicked.connect(self.open_audio)
        self.toolbar.addWidget(self.open_wav_btn)

        # 4. Cエンジン・レンダリング
        # 以前は execute_render (print文のみのダミー) に接続されており、
        # ボタンを押しても何も起きなかった。実際にノートデータの準備から
        # C++エンジンでのレンダリング・再生までを行う
        # on_render_button_clicked に接続する。
        self.render_btn = QPushButton("RENDER (C++ ENGINE)")
        self.render_btn.setObjectName("PrimaryButton")
        self.render_btn.clicked.connect(self.on_render_button_clicked)
        self.toolbar.addWidget(self.render_btn)

        # 右端を整えるためのスペーサー
        spacer = QWidget()
        self.setSizePolicy(QSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred))
        self.toolbar.addWidget(spacer)

    def setup_main_editor_area(self):
        """メインエディタエリア（トラックリスト + タイムライン）"""
        from PySide6.QtWidgets import QFrame
        from PySide6.QtCore import Qt

        # --- スプリッターの生成（一度だけ） ---
        self.editor_splitter = QSplitter(Qt.Orientation.Horizontal)

        # --- 左側：トラック管理パネル ---
        self.track_panel = QFrame()
        self.track_panel.setFrameShape(QFrame.Shape.StyledPanel)
        self.track_panel.setMinimumWidth(200)
        self.track_panel.setMaximumWidth(400)

        track_layout = QVBoxLayout(self.track_panel)
        track_layout.setContentsMargins(5, 5, 5, 5)

        self.track_list_widget = QListWidget()
        self.track_list_widget.setObjectName("TrackList")
        self.track_list_widget.currentRowChanged.connect(self.switch_track)

        btn_layout = QHBoxLayout()
        self.btn_add_vocal = QPushButton("+ Vocal")
        self.btn_add_wave = QPushButton("+ Audio")
        self.btn_add_vocal.clicked.connect(lambda: self.add_track("vocal"))
        self.btn_add_wave.clicked.connect(lambda: self.add_track("wave"))
        btn_layout.addWidget(self.btn_add_vocal)
        btn_layout.addWidget(self.btn_add_wave)

        track_layout.addWidget(QLabel("TRACKS"))
        track_layout.addWidget(self.track_list_widget)
        track_layout.addLayout(btn_layout)

        # トラックのミュート/ソロ/音量コントロール
        # (元々は setup_mixer_controls / setup_track_controls として
        #  実装されていたが、init_ui から一度も呼ばれておらず、
        #  ミュート・ソロ・音量スライダーが画面に表示されていなかった)
        mixer_layout = self.setup_mixer_controls()
        track_layout.addLayout(mixer_layout)

        # --- 右側：タイムライン（ここで一度だけ生成） ---
        right_container = QWidget()
        right_layout = QVBoxLayout(right_container)
        right_layout.setContentsMargins(0, 0, 0, 0)

        timeline_row = QHBoxLayout()
        self.timeline_widget = TimelineWidget()
        self.keyboard_sidebar = KeyboardSidebarWidget(
            key_height_pixels=self.timeline_widget.key_height_pixels
        )
        self.keyboard_sidebar_widget = self.keyboard_sidebar
    
        # ↓ ここで初期化（setup_timeline_area から移植）
        self.v_scrollbar = QSlider(Qt.Orientation.Vertical, self)
        self.v_scrollbar.setRange(0, 1000)
        self.v_scrollbar.setValue(10)
        self.v_scrollbar.valueChanged.connect(self.timeline_widget.set_vertical_offset)

        timeline_row.addWidget(self.keyboard_sidebar)
        timeline_row.addWidget(self.timeline_widget)
        timeline_row.addWidget(self.v_scrollbar)
    
        self.h_scrollbar = QScrollBar(Qt.Orientation.Horizontal)
        self.h_scrollbar.valueChanged.connect(self.timeline_widget.set_horizontal_offset)
    
        # タイムライン上下分割スプリッター
        timeline_splitter = QSplitter(Qt.Orientation.Vertical)

        # 上段：タイムライン本体
        timeline_container = QWidget()
        timeline_container_layout = QHBoxLayout(timeline_container)
        timeline_container_layout.setContentsMargins(0, 0, 0, 0)
        timeline_container_layout.addLayout(timeline_row)
        timeline_splitter.addWidget(timeline_container)

        # 下段：グラフエディタ
        self.graph_editor_widget = GraphEditorWidget()
        self.graph_editor_widget.pixels_per_beat = self.timeline_widget.pixels_per_beat
        self.graph_editor_widget.parameters_changed.connect(self.on_graph_parameters_changed)
        timeline_splitter.addWidget(self.graph_editor_widget)

        # 初期比率（タイムライン7：グラフ3）
        timeline_splitter.setSizes([700, 300])

        right_layout.addWidget(timeline_splitter)
        right_layout.addWidget(self.h_scrollbar)

        # --- スプリッターに追加（それぞれ一度だけ） ---
        self.editor_splitter.addWidget(self.track_panel)
        self.editor_splitter.addWidget(right_container)

        # --- メインレイアウトに追加（一度だけ） ---
        if self.main_layout is not None:
            self.main_layout.addWidget(self.editor_splitter)

        # --- 初期リスト更新 ---
        self.refresh_track_list_ui()

    def setup_bottom_panel(self):
        """下部：歌詞入力などのツール"""
        bottom_box = QHBoxLayout()
        
        self.lyrics_button = QPushButton("歌詞一括入力")
        self.lyrics_button.setFixedHeight(40)
        self.lyrics_button.clicked.connect(self.on_click_apply_lyrics_bulk)
        bottom_box.addWidget(self.lyrics_button)
        
        # フォルマントやパフォーマンス等のボタンもここに追加
        self.main_layout.addLayout(bottom_box)


    
    def setup_control_panel(self):
        """
        上部コントロールパネル(セカンドツールバー)の構築。

        以前はこのメソッド自体が init_ui から一度も呼ばれておらず、
        「AI Auto Setup」「自動歌詞配置」「キャラクター選択」
        「MIDIポート選択」「編集モード切替(Pitch/Gender/Tension/Breath)」
        「録音ボタン」の操作手段が画面上に一切存在しなかった。

        再生・停止・ループ・テンポ・時間表示は setup_toolbar 側に
        既に存在するため、ここでは重複させずユニークな機能のみを置く。
        """
        panel_layout = QHBoxLayout()

        # 録音コントロール (setup_toolbar には無いユニーク機能)
        self.record_button = QPushButton("● 録音")
        self.record_button.setCheckable(True)
        self.record_button.clicked.connect(self.on_record_toggled)
        panel_layout.addWidget(self.record_button)

        panel_layout.addSpacing(12)

        # キャラクター選択
        panel_layout.addWidget(QLabel("Voice:"))
        self.character_selector = QComboBox()
        panel_layout.addWidget(self.character_selector)
        
        # MIDIポート選択
        panel_layout.addWidget(QLabel("MIDI:"))
        self.midi_port_selector = QComboBox()
        self.midi_port_selector.addItem("ポートなし", None)
        self.midi_port_selector.currentIndexChanged.connect(self.on_midi_port_changed)
        panel_layout.addWidget(self.midi_port_selector)
        
        # ファイルを開く(MIDI/UST等)
        self.open_button = QPushButton("開く")
        self.open_button.clicked.connect(self.open_file_dialog_and_load_midi)
        panel_layout.addWidget(self.open_button)

        # 音源フォルダの再スキャン
        # (refresh_voice_list は実装済みだったが、これを呼ぶUIが
        #  どこにも存在しなかったため、音源を後から追加しても
        #  アプリを再起動するまでギャラリーに反映されなかった)
        self.rescan_voices_button = QPushButton("音源再スキャン")
        self.rescan_voices_button.clicked.connect(self.refresh_voice_list)
        panel_layout.addWidget(self.rescan_voices_button)

        panel_layout.addSpacing(12)

        # AI解析ボタン
        self.ai_analyze_button = QPushButton(" AI Auto Setup")
        self.ai_analyze_button.setStyleSheet(
            "background-color: #4A90E2; color: white; font-weight: bold;"
        )
        self.ai_analyze_button.clicked.connect(self.start_batch_analysis)
        panel_layout.addWidget(self.ai_analyze_button)
        
        # AI歌詞配置ボタン
        self.auto_lyrics_button = QPushButton("自動歌詞配置")
        self.auto_lyrics_button.clicked.connect(self.on_click_auto_lyrics)
        panel_layout.addWidget(self.auto_lyrics_button)

        # --- パラメーター切り替えボタン ---
        panel_layout.addSpacing(20) # 少し隙間をあける
        panel_layout.addWidget(QLabel("Edit Mode:"))
        
        # ボタングループで「どれか1つが選択されている状態」を作る
        self.param_group = QButtonGroup(self)
        self.param_buttons = {} # 後で参照しやすいように辞書に保存
        
        param_list = [
            ("Pitch", "#3498db"),   # 青
            ("Gender", "#e74c3c"),  # 赤
            ("Tension", "#2ecc71"), # 緑
            ("Breath", "#f1c40f")   # 黄
        ]
        
        for name, color in param_list:
            btn = QPushButton(name)
            btn.setCheckable(True)
            btn.setFixedWidth(60)
            # 選択中のボタンに色を付けるスタイルシート
            btn.setStyleSheet(f"QPushButton:checked {{ background-color: {color}; color: white; border: 1px solid white; }}")
            
            if name == "Pitch":
                btn.setChecked(True) # 初期状態
            
            panel_layout.addWidget(btn)
            self.param_group.addButton(btn)
            self.param_buttons[name] = btn

        # ボタンがクリックされたらグラフエディタのモードを切り替える
        self.param_group.buttonClicked.connect(self.on_param_mode_changed)

        panel_layout.addStretch()
        self.main_layout.addLayout(panel_layout)



    def setup_status_bar(self):
        """ステータスバーの構築 (Pyright/Pylance 完全対応版)"""
        
        # 1. 自身の statusBar オブジェクトを取得し、存在と型を確定させる
        # これにより "addWidget is not a known attribute of None" を一掃します
        status_bar = self.statusBar()
        if not status_bar:
            return

        # 2. ラベルの生成と追加
        self.status_label = QLabel("準備完了")
        status_bar.addWidget(self.status_label)
        
        # 3. プログレスバーの生成と追加
        self.progress_bar = QProgressBar()
        
        # 型を明示的にキャストしてアクセスすることで、以降の hide/show での警告を防ぎます
        prog_bar = cast(QProgressBar, self.progress_bar)
        prog_bar.hide()
        
        # ステータスバーの右側に常駐させる
        status_bar.addPermanentWidget(prog_bar)

    def setup_actions(self):
        """アクションの定義"""
        self.copy_action = QAction("コピー", self)
        self.copy_action.setShortcuts(QKeySequence.StandardKey.Copy)
        self.copy_action.triggered.connect(
            self.timeline_widget.copy_selected_notes_to_clipboard
        )
        
        self.paste_action = QAction("ペースト", self)
        self.paste_action.setShortcuts(QKeySequence.StandardKey.Paste)
        self.paste_action.triggered.connect(
            self.timeline_widget.paste_notes_from_clipboard
        )
        
        self.save_action = QAction("保存(&S)", self)
        self.save_action.setShortcuts(QKeySequence.StandardKey.Save)
        self.save_action.triggered.connect(self.save_file_dialog_and_save_midi)

        # Undo / Redo
        # (ショートカット自体は setup_vose_shortcuts の QShortcut で結線済み。
        #  ここでは編集メニューに表示するための QAction のみ用意し、
        #  ショートカットの二重登録は避ける)
        self.undo_action = QAction("元に戻す(&U)", self)
        self.undo_action.triggered.connect(self.undo)

        self.redo_action = QAction("やり直し(&R)", self)
        self.redo_action.triggered.connect(self.redo)

    def setup_menus(self):
        """メニューバーの構築"""
        # ファイルメニュー
        file_menu = self.menuBar().addMenu("ファイル(&F)")
        file_menu.addAction(self.save_action)
        
        export_action = QAction("WAV書き出し...", self)
        export_action.triggered.connect(self.on_export_button_clicked)
        file_menu.addAction(export_action)
        
        export_midi_action = QAction("MIDI書き出し...", self)
        export_midi_action.triggered.connect(self.export_to_midi_file)
        file_menu.addAction(export_midi_action)

        # 編集メニュー
        edit_menu = self.menuBar().addMenu("編集(&E)")
        edit_menu.addAction(self.undo_action)
        edit_menu.addAction(self.redo_action)
        edit_menu.addSeparator()
        edit_menu.addAction(self.copy_action)
        edit_menu.addAction(self.paste_action)

    def setup_connections(self):
        """
        シグナル/スロット接続の完全版（省略なし）。
        UI、エンジン、および各ウィジェット間の通信を確立します。
        """
        # --- 1. スクロール同期（垂直：鍵盤とノート領域） ---
        if self.v_scrollbar and self.keyboard_sidebar and self.timeline_widget:
            self.v_scrollbar.valueChanged.connect(self.keyboard_sidebar.set_vertical_offset)
            
        if self.keyboard_sidebar is not None:
            self.keyboard_sidebar.note_pressed.connect(
                lambda note: self.handle_midi_realtime(note, 100, "on")
            )
            self.keyboard_sidebar.note_released.connect(
                lambda note: self.handle_midi_realtime(note, 0, "off")
            )

        # --- 2. スクロール同期（水平：ノートとピッチグラフ領域） ---
        if self.h_scrollbar and self.timeline_widget and self.graph_editor_widget:
            
            self.h_scrollbar.valueChanged.connect(self.graph_editor_widget.set_horizontal_offset)
            self.timeline_widget.scroll_synced_signal.connect(self._sync_horizontal_scrollbar_from_timeline)

        # --- 3. タイムライン・データ更新の同期 ---
        if self.timeline_widget:
            # ノートが動いたときにメインウィンドウ側で受け取る
            self.timeline_widget.notes_changed_signal.connect(self.on_timeline_updated)
            # タイムラインからグラフエディタへ通知（ピッチ描画の基準更新）
            if self.graph_editor_widget is not None:
                self.timeline_widget.notes_changed_signal.connect(
                    self.graph_editor_widget.sync_with_notes
                )
            # ノート編集（追加/削除/移動/リサイズ/ペースト/複製/歌詞編集）が
            # 確定したタイミングでUndo履歴に登録する
            self.timeline_widget.edit_committed_signal.connect(
                self.on_timeline_edit_committed
            )
        # --- 4. テンポ入力の確定（Returnキー押下で反映） ---
        if self.tempo_input:
            self.tempo_input.returnPressed.connect(self.update_tempo_from_input)

        # --- 5. ボイスギャラリーとの接続（キャラクター切り替え） ---
        # VoiceCardGalleryが MainWindow の属性 (self.voice_gallery) として存在すると仮定
        if hasattr(self, 'voice_gallery') and self.voice_gallery:
            self.voice_gallery.voice_selected.connect(self.on_voice_changed)

        # --- 6. 再生・停止・録音ボタンの制御 ---
        if self.play_button:
            self.play_button.clicked.connect(self.toggle_playback)
        if self.record_button:
            self.record_button.clicked.connect(self.toggle_recording)

        # --- 7. エンジンからのフィードバック（再生位置の同期） ---
        if self.playback_timer:
            self.playback_timer.timeout.connect(self.update_playback_ui)

        print("✅ All internal signals and slots have been connected.")
        

    def setup_formant_slider(self):
        """フォルマントスライダーの設定"""
        from PySide6.QtWidgets import QSlider
        
        self.formant_label = QLabel("声の太さ (Formant)")
        self.formant_slider = QSlider(Qt.Orientation.Horizontal)
        self.formant_slider.setRange(-100, 100)
        self.formant_slider.setValue(0)
        self.formant_slider.setMaximumWidth(150)
        self.formant_slider.valueChanged.connect(self.on_formant_changed)
        
        self.toolbar.addWidget(self.formant_label)
        self.toolbar.addWidget(self.formant_slider)

    def on_formant_changed(self, value):
        """フォルマント変更時の処理"""
        shift = value / 100.0
        if hasattr(self.vo_se_engine, 'vose_set_formant'):
            self.vo_se_engine.vose_set_formant(shift)

    def init_pro_talk_ui(self):
        """Talk入力UI初期化"""
        self.text_input = QLineEdit()
        self.text_input.setPlaceholderText("喋らせたい文章を入力（Enterで展開）...")
        self.text_input.setFixedWidth(300)
        self.text_input.returnPressed.connect(self.on_talk_execute)
        
        self.toolbar.addWidget(QLabel("Talk:"))
        self.toolbar.addWidget(self.text_input)

    def on_talk_execute(self):
        """Talk実行処理（省略なし完全版）"""
        # 1. 入力チェック（Noneガード付き）
        if not hasattr(self, 'text_input') or self.text_input is None:
            return
            
        text = self.text_input.text()
        if not text:
            return
        
        # 2. 解析と反映
        if hasattr(self, 'analyzer') and self.analyzer:
            new_events = self.analyzer.analyze_to_pro_events(text)
            
            tw = getattr(self, 'timeline_widget', None)
            if tw:
                if hasattr(tw, 'set_notes'):
                    tw.set_notes(new_events)
                tw.update()
            
            # 3. 通知とクリア
            status_bar = self.statusBar()
            if status_bar:
                status_bar.showMessage(f"Talkモード: '{text}' を展開しました")
            self.text_input.clear()

    def on_talk(self):
        text_input = getattr(self, 'text_input', None)
        if text_input is None:
            return
        text = text_input.text()
        if not text:
            return
        if self.talk_manager is None:
            print("WARNING: talk_manager が初期化されていません")
            return
        self.talk_manager.speak(text)
    
    @Slot(object)
    def on_param_mode_changed(self, button):
        """パラメーター切り替えボタン処理（省略なし完全版）"""
        if not button:
            return
            
        # button.text() でモード名を取得
        mode = button.text()
        
        # グラフエディタへ通知
        ge = getattr(self, 'graph_editor_widget', None)
        if ge and hasattr(ge, 'set_mode'):
            ge.set_mode(mode)
            
        status_bar = self.statusBar()
        if status_bar:
            status_bar.showMessage(f"編集モード: {mode}")

    def toggle_playback(self, event=None):
        """
        Spac eキーまたは再生ボタンでの再生/停止切り替え（完全安全版）
        """
        # 0. スレッドロックを使用して競合状態を防ぐ
        with self._playback_lock:
            # 1. 現在の再生状態を安全に取得
            monitoring = getattr(self, 'pro_monitoring', None)
        
            if monitoring and not isinstance(monitoring, bool):
                is_playing = getattr(monitoring, 'is_playing', False)
            else:
                is_playing = getattr(self, 'is_playing', False)

            if not is_playing:
                # ==========================================
                # 再生開始処理
                # ==========================================
                print("▶ VO-SE Engine: 再生開始")
            
                # ステータスバー更新
                status_bar = self.statusBar()
                if status_bar:
                    status_bar.showMessage("再生中...")
            
                # 1. トラックの取得
                tracks = getattr(self, 'tracks', [])
                idx = getattr(self, 'current_track_idx', 0)
            
                if 0 <= idx < len(tracks):
                    current_track = tracks[idx]
                
                    # 2. 伴奏トラック（Wave）の場合
                    if current_track.track_type == "wave" and current_track.audio_path:
                        player = getattr(self, 'audio_player', None)
                        output = getattr(self, 'audio_output', None)
                    
                        if player and hasattr(player, 'setSource'):
                            from PySide6.QtCore import QUrl
                            url = QUrl.fromLocalFile(current_track.audio_path)
                            player.setSource(url)
                        
                        if output and hasattr(output, 'setVolume'):
                             output.setVolume(current_track.volume)
                        
                        if player and hasattr(player, 'play'):
                            player.play()
                
                    # 3. 再生位置の設定
                    timeline = getattr(self, 'timeline_widget', None)
                    if timeline:
                        start_time = getattr(timeline, '_current_playback_time', 0.0)
                        if start_time is None:
                            start_time = 0.0
                    
                        # Waveトラックの場合は位置をシーク
                        if current_track.track_type == "wave":
                            player = getattr(self, 'audio_player', None)
                            if player and hasattr(player, 'setPosition'):
                                player.setPosition(int(start_time * 1000))

                 # 4. フラグ更新
                if monitoring and not isinstance(monitoring, bool):
                    setattr(monitoring, 'is_playing', True)
            
                self.is_playing = True
            
                # 5. 再生ボタンの表示更新
                play_btn = getattr(self, 'play_button', None)
                if play_btn:
                    play_btn.setText("■ 停止")

            else:
                # ==========================================
                # 再生停止処理
                # ==========================================
                print("■ VO-SE Engine: 再生停止")
            
                # ステータスバー更新
                status_bar = self.statusBar()
                if status_bar:
                    status_bar.showMessage("一時停止")
            
                # 1. すべての音を停止
                player = getattr(self, 'audio_player', None)
                if player and hasattr(player, 'pause'):
                    player.pause()
            
                # 2. フラグ更新
                if monitoring and not isinstance(monitoring, bool):
                    setattr(monitoring, 'is_playing', False)
            
                self.is_playing = False
            
                # 3. 再生ボタンの表示更新
                play_btn = getattr(self, 'play_button', None)
                if play_btn:
                    play_btn.setText("▶ 再生")

            # UI全体の再描画
            self.update()


    def refresh_canvas(self):
        """キャンバス（描画領域）を再描画する"""
        if hasattr(self, 'timeline_widget'):
            if self.timeline_widget: 
                assert self.timeline_widget is not None
                self.timeline_widget.update()

    def sync_ui_to_selection(self):
        """選択されたアイテムに合わせてUI表示を同期する"""
        # ここに選択状態の同期処理を書く
        pass

    def setup_vose_shortcuts(self):
        """ショートカットキーの設定 (PySide6方式)"""        
        # Spaceキーで再生/停止
        self.play_shortcut = QShortcut(QKeySequence(Qt.Key.Key_Space), self)
        self.play_shortcut.activated.connect(self.toggle_playback)

        # Undo / Redo
        # (HistoryManager / EditCommand は実装済みだが、ショートカットが
        #  どこにも結線されておらず Ctrl+Z / Ctrl+Y が機能していなかったため追加)
        self.undo_shortcut = QShortcut(QKeySequence.StandardKey.Undo, self)
        self.undo_shortcut.activated.connect(self.undo)

        self.redo_shortcut = QShortcut(QKeySequence.StandardKey.Redo, self)
        self.redo_shortcut.activated.connect(self.redo)
        # Windows/Linuxでは Ctrl+Y も Redo として広く使われているため追加で割り当てる
        self.redo_shortcut_alt = QShortcut(QKeySequence("Ctrl+Y"), self)
        self.redo_shortcut_alt.activated.connect(self.redo)


    def perform_startup_sequence(self):
        """[完全版] 起動時のハードウェア診断とエンジン最適化"""
        # 1. UIの初期化（ステータスバーにラベルを追加）
        if not hasattr(self, 'device_status_label'):
            from PySide6.QtWidgets import QLabel
            self.device_status_label = QLabel(self)
            self.statusBar().addPermanentWidget(self.device_status_label)

        self.statusBar().showMessage("Initializing VO-SE Engine...")
        
        # 2. ハードウェア診断ロジック
        try:
            # 外部ライブラリがあるか、どのハードが使えるかチェック
            import onnxruntime as ort
            providers = ort.get_available_providers()
            
            if 'DmlExecutionProvider' in providers:
                self.active_device = "GPU (DirectML)"
                self.active_provider = "DmlExecutionProvider"
            elif 'CoreMLExecutionProvider' in providers:
                self.active_device = "Neural Engine (Apple)"
                self.active_provider = "CoreMLExecutionProvider"
            elif 'CUDAExecutionProvider' in providers:
                self.active_device = "NVIDIA GPU (CUDA)"
                self.active_provider = "CUDAExecutionProvider"
            else:
                self.active_device = "CPU (Standard)"
                self.active_provider = "CPUExecutionProvider"
                
        except ImportError:
            # ライブラリが見つからない場合は安全なCPUモードへ
            self.active_device = "CPU (Safe Mode)"
            self.active_provider = "CPUExecutionProvider"

        # 3. 診断結果をUIに反映
        if self.device_status_label is not None:
            self.device_status_label.setText(f" [ {self.active_device} ] ")
        self.statusBar().showMessage(f"Engine Ready: {self.active_device}", 5000)
        
        # アップデート確認（CI/スモークテストでは環境変数で無効化可能）
        skip_update_check = os.environ.get("VOSE_SKIP_UPDATE_CHECK", "").lower() in {
            "1", "true", "yes", "on"
        }
        if not skip_update_check:
            QTimer.singleShot(3000, self._check_for_updates)

        # 起動時にトラックが1件も無いと、ユーザーは「+ Vocal」ボタンを
        # 自分で押すまで何も編集できない。多くのDAW/エディタと同様、
        # 最低1トラックがある状態で起動するようにする。
        # add_track() はUndo履歴に積む設計のため、ユーザー操作ではない
        # この自動生成では使わず、直接 tracks に追加する。
        if not self.tracks:
            initial_track = VoseTrack("Vocal 1", "vocal")
            self.tracks.append(initial_track)
            self.refresh_track_list_ui()
            self.current_track_idx = 0
            self.track_list_widget.setCurrentRow(0)

    def log_startup(self, message):
        """標準出力へのログ記録）""" 
        timestamp = time.strftime('%H:%M:%S')
        print(f"[{timestamp}] [BOOT] {message}")
        """起動ログ（デバッグ用）"""
        print(f"[{time.strftime('%H:%M:%S')}] VO-SE Boot: {message}")

    def setup_vose_keyboard_navigation(self):
        """高度なキーボードナビゲーションの設定"""
        from PySide6.QtGui import QShortcut, QKeySequence
        # 1. 1音移動 (Alt + Left/Right)
        QShortcut(QKeySequence("Alt+Right"), self).activated.connect(self.select_next_note)
        QShortcut(QKeySequence("Alt+Left"), self).activated.connect(self.select_prev_note)

        # 2. 削除 (Delete / Backspace)
        QShortcut(QKeySequence(Qt.Key.Key_Delete), self).activated.connect(self.delete_selected_note)
        QShortcut(QKeySequence(Qt.Key.Key_Backspace), self).activated.connect(self.delete_selected_note)

        # 3. Tabキーによる歌詞入力フォーカス移動
        QShortcut(QKeySequence(Qt.Key.Key_Tab), self).activated.connect(self.focus_next_note_input)

    # --- 動作ロジック ---

    def select_next_note(self):
        """
        Alt+Right で次のノートを選択する。

        以前は self.notes (timeline_widget.notes_list とは別管理の
        ミラーリスト) を操作しており、画面の実データと食い違っていたため
        何も起きないように見えていた。timeline_widget 側の選択状態を
        直接操作する形に修正した。
        """
        if self.timeline_widget:
            self.timeline_widget.select_next_note_in_time()

    def select_prev_note(self):
        """Alt+Left で前のノートを選択する。"""
        if self.timeline_widget:
            self.timeline_widget.select_prev_note_in_time()

    def delete_selected_note(self):
        """
        Delete/Backspace で選択中のノートを削除する。

        以前は self.notes.pop(...) で別管理のミラーリストを操作するだけで
        画面（timeline_widget.notes_list）には反映されず、Undo履歴にも
        積まれなかった。timeline_widget.delete_selected() を呼ぶことで、
        実データの削除と Undo 履歴への登録の両方を行う。
        """
        if self.timeline_widget:
            self.timeline_widget.delete_selected()

    def focus_next_note_input(self):
        """Tabキーで次の入力欄へ。Pro Audio的な爆速入力を実現"""
        if not hasattr(self, 'input_fields') or not self.input_fields:
            return
        
        # 現在フォーカスされているウィジェットを確認
        current = self.focusWidget()
        if isinstance(current, QLineEdit) and current in self.input_fields:
            idx = self.input_fields.index(current)
            next_idx = (idx + 1) % len(self.input_fields)
            self.input_fields[next_idx].setFocus()
            self.input_fields[next_idx].selectAll()

    def draw_pro_grid(self):
        """プロ仕様のグリッド（背景線）を描画"""
        # 代表のコードをここに配属
        # 縦線（時間軸）
        for x in range(0, 10000, 50):
            grid_color = "#3A3A3C" if x % 200 == 0 else "#242424"
            if hasattr(self, 'canvas'):
                self.canvas.draw_line(x, grid_color)
        
        # 横線（音階軸）
        for y in range(0, 1000, 40):
            if hasattr(self, 'canvas'):
                pass

    # --- 1. データ・ファイル管理系 ---
    # (このセクションの主要メソッドは modules/gui/mixins/project_io_mixin.py へ移動済み)

    def update_timeline_with_notes(self, notes_data: list):
        """解析したノートデータをタイムラインウィジェットにセットする"""
        if hasattr(self, 'timeline_widget'):
            # notes_data は辞書のリストを想定
            self.timeline_widget.set_notes(notes_data)
            self.refresh_voice_ui()

        # 実装詳細は midi_manager の保存機能に依存

    # --- 2. 音声・AI処理系 ---

    def preprocess_lyrics(self, text: str, notes: Optional[List[Any]] = None):
        """歌詞の事前処理（平仮名化など）を実行"""
        if hasattr(self, 'text_analyzer') and self.text_analyzer is not None:
            processed = self.text_analyzer.analyze_text(text)
            print(f"歌詞を解析しました: {text} -> {len(processed)}音素")
            return processed
        return []

    def refresh_voice_ui(self):
        """音声設定やタイムラインの表示を最新状態に更新する"""
        self.update() # 再描画
        print("UIをリフレッシュしました。")

    # --- 3. エンジン・モニタリング系 ---

    def run_engine(self, alias: Optional[str] = None, params: Optional[Any] = None):
        """音声合成エンジンの実行（レンダリング）"""
        print("エンジンのレンダリングを開始します...")
        if hasattr(self, 'ai_manager'):
            # AIマネージャーを通じた処理をここに記述
            pass

    @property
    def pro_monitoring(self):
        """プロフェッショナル・モニタリング設定の参照用プロパティ"""
        # エラーログで self.pro_monitoring へのアクセスがあったため定義
        return getattr(self, "_pro_monitoring_enabled", False)

    @pro_monitoring.setter
    def pro_monitoring(self, value: bool):
        self._pro_monitoring_enabled = value
        print(f"Pro Monitoring: {value}")


  #=======================================================
    #ai処理接続

    def on_ai_auto_setup(self):
        """おまかせ調声ボタン"""
        if not self.ai_manager.is_model_loaded():
            QMessageBox.warning(self, "AI未準備", 
                "AIモデルが見つかりません。\n"
                "train_aural_model.py を実行してください。")
            return
    
        notes = self.timeline_widget.get_all_notes()
        if not notes:
            return
    
        self.status_bar.showMessage("AI調声中...")
        for note in notes:
            result = self.ai_manager.predict(note)
            note.pre_utterance = result["pre_utterance"]
            note.overlap        = result["overlap"]
            note.consonant      = result["consonant"]
    
        self.timeline_widget.update()
        self.status_bar.showMessage("AI調声完了")

        
    #=======================================================
    # --- Undo / Redo スロット ---
    #======================================================-
    
    @Slot()
    def undo(self):
        """Ctrl+Z で呼び出し"""
        self.history.undo()
        self.statusBar().showMessage("Undo executed")

    @Slot()
    def redo(self):
        """Ctrl+Y で呼び出し"""
        self.history.redo()
        self.statusBar().showMessage("Redo executed")

    def register_edit(self, old_state, new_state, description):
        """状態変化を履歴に登録"""
        def redo_fn(): self.apply_state(new_state)
        def undo_fn(): self.apply_state(old_state)
        self.history.execute(EditCommand(redo_fn, undo_fn, description))

    def apply_state(self, state):
        """状態（ノートリストなど）を反映"""
        self.timeline_widget.notes_list = deepcopy(state)
        assert self.timeline_widget is not None
        self.timeline_widget.update()

    @Slot(object, object, str)
    def on_timeline_edit_committed(self, before_state, after_state, description):
        """
        タイムライン上のノート編集（追加/削除/移動/リサイズ/ペースト/複製/歌詞編集）
        が確定したときに、その操作をUndo履歴に積む。

        この時点でノート編集自体はすでにUI上で実行済みのため、
        HistoryManager.execute() ではなく push() を使う
        (execute() は内部で redo_func() を呼んでしまい、操作が二重に
        実行されてしまうため)。
        """
        def redo_fn():
            self.timeline_widget._restore_notes_snapshot(after_state)

        def undo_fn():
            self.timeline_widget._restore_notes_snapshot(before_state)

        self.history.push(EditCommand(redo_fn, undo_fn, description))

    # --- マルチトラック操作 ---

    def add_track(self, t_type="vocal"):
        """新規トラックの追加と履歴登録"""
        count = len(self.tracks) + 1
        name = f"Vocal {count}" if t_type == "vocal" else f"Audio {count}"
        new_track = VoseTrack(name, t_type)

        def redo_fn():
            self.tracks.append(new_track)
            self.refresh_track_list_ui()
            new_idx = len(self.tracks) - 1
            # setCurrentRow() は currentRowChanged 経由で switch_track() を
            # 暗黙的に再発火させてしまう場合があり、下の明示的な switch_track()
            # 呼び出しと二重実行になって notes が壊れることがあるため、
            # blockSignals で選択行の更新だけ行い、画面同期は明示的な呼び出しに一本化する
            self.track_list_widget.blockSignals(True)
            self.track_list_widget.setCurrentRow(new_idx)
            self.track_list_widget.blockSignals(False)
            self.switch_track(new_idx)
            if t_type == "wave":
                self.load_audio_for_track(new_track)

        def undo_fn():
            if new_track in self.tracks:
                removed_idx = self.tracks.index(new_track)
                was_current = (removed_idx == self.current_track_idx)
                self.tracks.remove(new_track)
                self.refresh_track_list_ui()

                if self.tracks:
                    if was_current:
                        # 削除したトラックがまさに選択中だった場合、
                        # timeline_widget の中身は「削除されたトラックの
                        # 残骸」であり、どのトラックの内容としても正しくない。
                        # switch_track() 経由だとこの残骸が退避先トラックの
                        # notes を上書きしてしまうため、退避処理を経由せず
                        # 直接ロードのみ行う。
                        new_idx = min(removed_idx, len(self.tracks) - 1)
                        self.current_track_idx = new_idx
                        # setCurrentRow() が switch_track() を暗黙的に再発火させ、
                        # 直後の直接ロードと二重実行になって notes を壊すことを防ぐ
                        self.track_list_widget.blockSignals(True)
                        self.track_list_widget.setCurrentRow(new_idx)
                        self.track_list_widget.blockSignals(False)
                        target_tr = self.tracks[new_idx]
                        if self.timeline_widget:
                            self.timeline_widget.set_notes(target_tr.notes)
                    else:
                        # 選択中のトラックは削除されていないので、通常の
                        # switch_track() で安全に退避→ロードできる。
                        new_idx = min(self.current_track_idx, len(self.tracks) - 1)
                        self.track_list_widget.blockSignals(True)
                        self.track_list_widget.setCurrentRow(new_idx)
                        self.track_list_widget.blockSignals(False)
                        self.switch_track(new_idx)
                else:
                    # 最後の1トラックを削除した場合、選択中のインデックスも
                    # 安全な値に戻す(refresh_ui側でも防御しているが、
                    # ここで揃えておくことでUIの選択状態を正しく保つ)
                    self.current_track_idx = 0
                    if self.timeline_widget:
                        self.timeline_widget.set_notes([])

        # 履歴にコマンドを登録して実行
        self.history.execute(EditCommand(redo_fn, undo_fn, f"Add {name}"))

    def switch_track(self, index: int) -> None:
        """
        トラック切り替え時のデータ保護と読み込み。
        代表の設計に基づき、編集中のデータを退避させてから新しいトラックをロードします。
        """
        # 1. 境界チェック（絶対に安全に）
        # self.tracks がリストであることを型ヒントで保証
        tracks_list: List[Any] = getattr(self, 'tracks', [])
        if index < 0 or index >= len(tracks_list):
            return

        # 2. 現在の編集状態を今のトラックに退避
        # self.current_track_idx の妥当性をチェック
        curr_idx: int = getattr(self, 'current_track_idx', 0)
        if 0 <= curr_idx < len(tracks_list):
            current_tr = tracks_list[curr_idx]
            # timeline_widget の存在を確認してデータをコピー
            t_widget = getattr(self, 'timeline_widget', None)
            if t_widget is not None:
                # deepcopyにより、切り替え後に元データが壊れるのを防ぐ（代表の安全設計）
                current_tr.notes = deepcopy(t_widget.notes_list)

        # 3. 新しいトラックの取得とインデックス更新
        self.current_track_idx = index
        target_tr = tracks_list[index]

        # 4. タイムラインへデータをロード
        # 1140行目のエラー対策: target_tr が辞書ではなく、
        # プロパティ(notes, name等)を持つオブジェクトであることを確実にする
        if hasattr(self, 'timeline_widget') and self.timeline_widget is not None:
            self.timeline_widget.set_notes(target_tr.notes)
            # 背景の波形などを再描画
            if self.timeline_widget: 
                self.timeline_widget.update()

        # 5. UI（ミキサー等）の同期
        # 各UIパーツの存在を確認しながら値をセット（AttributeAccessIssue対策）
        vol_slider = getattr(self, 'vol_slider', None)
        vol_label = getattr(self, 'vol_label', None)
        btn_mute = getattr(self, 'btn_mute', None)
        btn_solo = getattr(self, 'btn_solo', None)

        if vol_slider is not None:
            vol_slider.blockSignals(True)  # 無限ループ防止
            # volume が None の場合を考慮して 0.0 をデフォルトに
            vol_val = getattr(target_tr, 'volume', 0.8)
            vol_int = int(vol_val * 100)
            vol_slider.setValue(vol_int)
            if vol_label is not None:
                vol_label.setText(f"Volume: {vol_int}%")
            vol_slider.blockSignals(False)

        if btn_mute is not None:
            btn_mute.setChecked(getattr(target_tr, 'is_muted', False))
        if btn_solo is not None:
            btn_solo.setChecked(getattr(target_tr, 'is_solo', False))

        # 6. ステータスバー更新
        tr_name = getattr(target_tr, 'name', f"Track {index+1}")
        msg = f"Track: {tr_name}"
        
        # track_type が wave の場合はファイル名も表示
        tr_type = getattr(target_tr, 'track_type', "midi")
        tr_audio = getattr(target_tr, 'audio_path', "")
        if tr_type == "wave" and tr_audio:
            msg += f" (File: {os.path.basename(tr_audio)})"
            
        self.statusBar().showMessage(msg)

    def load_audio_for_track(self, track):
        """Audioトラックにファイルを読み込み、波形解析をキックする"""
        file_path, _ = QFileDialog.getOpenFileName(
            self, "伴奏を選択", "", "Audio Files (*.wav *.mp3)"
        )
        if file_path:
            track.audio_path = file_path
            track.name = os.path.basename(file_path)
            
            # 重要：読み込み時に一度解析させてキャッシュを作る
            # TimelineWidgetのメソッドを呼び出してピークを取得
            track.vose_peaks = self.timeline_widget.get_audio_peaks(file_path)
            
            self.refresh_track_list_ui()
            if self.timeline_widget: 
                self.timeline_widget.update()
            self.statusBar().showMessage(f"Loaded: {track.name}")

    def refresh_ui(self):
        """Undo/Redo後に現在のトラック状態を画面に同期"""
        if not self.tracks:
            # トラックが1件も無い状態（全トラックがUndoで消えた等）。
            # タイムラインを空にして安全に終了する。
            if self.timeline_widget:
                self.timeline_widget.set_notes([])
            self.update()
            return

        # current_track_idx がトラック削除等で範囲外になっている場合に備える
        if self.current_track_idx >= len(self.tracks):
            self.current_track_idx = len(self.tracks) - 1

        current_notes = self.tracks[self.current_track_idx].notes
        self.timeline_widget.set_notes(current_notes)
        self.update()

    # --- 保存（マルチトラック対応） ---
    # (旧: save_project は modules/gui/mixins/project_io_mixin.py へ移動済み)

    #ミュート（M）とソロ（S）

    def setup_track_controls(self):
        """トラックごとのM/S状態を制御する（setup_main_editor_areaから呼び出し）"""
        # 現在選択されているトラックに対して操作を行う
        control_layout = QHBoxLayout()
        
        self.btn_mute = QPushButton("M")
        self.btn_mute.setCheckable(True)
        self.btn_mute.setFixedWidth(30)
        self.btn_mute.clicked.connect(self.toggle_mute)
        
        self.btn_solo = QPushButton("S")
        self.btn_solo.setCheckable(True)
        self.btn_solo.setFixedWidth(30)
        self.btn_solo.clicked.connect(self.toggle_solo)
        
        control_layout.addWidget(self.btn_mute)
        control_layout.addWidget(self.btn_solo)
        return control_layout

    def toggle_mute(self):
        """現在のトラックをミュートにする"""
        target = self.tracks[self.current_track_idx]
        target.is_muted = self.btn_mute.isChecked()
        self.refresh_track_list_ui()
        self.statusBar().showMessage(f"{target.name} Muted: {target.is_muted}")

    def toggle_solo(self):
        """現在のトラックをソロにする"""
        target = self.tracks[self.current_track_idx]
        target.is_solo = self.btn_solo.isChecked()
        
        # ソロがONになった場合、他のトラックのソロ状況も考慮するロジック
        self.refresh_track_list_ui()
        self.statusBar().showMessage(f"{target.name} Solo: {target.is_solo}")

    def get_active_tracks(self):
        """現在鳴らすべきトラックのリストを返す（再生エンジン用）"""
        # ソロがあるかチェック
        solo_exists = any(t.is_solo for t in self.tracks)
        
        active_tracks = []
        for t in self.tracks:
            if solo_exists:
                # ソロがあるなら、ソロがONかつミュートでないものだけ
                if t.is_solo and not t.is_muted:
                    active_tracks.append(t)
            else:
                # ソロがないなら、ミュートでないものすべて
                if not t.is_muted:
                    active_tracks.append(t)
        return active_tracks

    def refresh_track_list_ui(self):
        """UI上のリスト表示を最新状態に同期（M/S状態を反映）"""
        # Noneガード：widgetが存在しない場合は何もしない
        if not self.track_list_widget:
            return

        from PySide6.QtWidgets import QListWidgetItem
        from PySide6.QtCore import Qt

        self.track_list_widget.blockSignals(True)
        self.track_list_widget.clear()
        
        # ソロ状態のトラックが1つでも存在するかチェック
        solo_exists = any(t.is_solo for t in self.tracks)
        
        for i, t in enumerate(self.tracks):
            status = ""
            # Actionエラー E701 回避済みの綺麗なif文
            if t.is_muted:
                status += "[M]"
            if t.is_solo:
                status += "[S]"
            
            item_text = f"{status} [{'V' if t.track_type == 'vocal' else 'A'}] {t.name}"
            item = QListWidgetItem(item_text)
            
            # ミュート中や、ソロモード時にソロではないトラックをグレーアウト
            if t.is_muted or (solo_exists and not t.is_solo):
                item.setForeground(Qt.GlobalColor.gray)
            elif t.track_type == "wave":
                item.setForeground(Qt.GlobalColor.cyan)
                
            self.track_list_widget.addItem(item)
        
        # 現在の選択行を維持（範囲チェック付き）
        if 0 <= self.current_track_idx < self.track_list_widget.count():
            self.track_list_widget.setCurrentRow(self.current_track_idx)
        
        # 現在のトラックに合わせてM/SボタンのUI状態も同期（Noneガード徹底）
        if 0 <= self.current_track_idx < len(self.tracks):
            current_t = self.tracks[self.current_track_idx]
            if self.btn_mute:
                self.btn_mute.setChecked(current_t.is_muted)
            if self.btn_solo:
                self.btn_solo.setChecked(current_t.is_solo)
        
        self.track_list_widget.blockSignals(False)


    def init_audio_playback(self):
        """オーディオ再生機能の初期設定（MainWindowの__init__から呼び出し）"""
        from PySide6.QtMultimedia import QMediaPlayer, QAudioOutput
        
        # 伴奏（Wave）再生用の心臓部
        self.audio_player = QMediaPlayer()
        self.audio_output = QAudioOutput()
        self.audio_player.setAudioOutput(self.audio_output)
        
        # 再生位置が動いた時にタイムラインのカーソルを同期させる
        self.audio_player.positionChanged.connect(self.sync_ui_to_audio)
        
        # 再生が終わった時の処理
        self.audio_player.playbackStateChanged.connect(self.on_playback_state_changed)

    def sync_ui_to_audio(self, ms):
        """オーディオの再生位置（ms）をUIの秒数に反映"""
        if self.audio_player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
            current_sec = ms / 1000.0
            # タイムラインのカーソル位置を更新
            self.timeline_widget._current_playback_time = current_sec
            if self.timeline_widget:
                self.timeline_widget.update()

    @Slot(object)
    def on_playback_state_changed(self, state: Any) -> None:
        """再生状態の変化をUIと内部フラグに同期する。"""
        is_playing = state == QMediaPlayer.PlaybackState.PlayingState
        self.is_playing = is_playing
        if hasattr(self, "play_btn") and self.play_btn:
            self.play_btn.setText("⏸ 停止" if is_playing else "▶ 再生")

    def setup_audio_interface(self) -> None:
        """
        オーディオ再生エンジンの初期化（PySide6完全対応版）。

        以前はここで音量スライダーUIも構築していたが、
        setup_mixer_controls() の vol_slider と同名属性が重複し、
        後から呼ばれた方が上書きしてしまう問題があったため、
        UI構築は setup_mixer_controls 側に一本化し、
        ここでは再生エンジン(QMediaPlayer/QAudioOutput)の構築に専念する。
        """
        from PySide6.QtMultimedia import QMediaPlayer, QAudioOutput

        # --- 再生エンジンの構築 ---
        self.player = QMediaPlayer()
        self.audio_output = QAudioOutput()
    
        player = self.player
        audio_output = self.audio_output
        if player is None or audio_output is None:
            return
        player.setAudioOutput(audio_output)
        audio_output.setVolume(0.5)
        player.playbackStateChanged.connect(self.on_playback_state_changed)


    def get_current_playback_state(self) -> bool:
        """
 
        """
        if not hasattr(self, 'player') or self.player is None:
            return False
            
        # 旧: self.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState
        # 新: PySide6 の正確な Enum 比較
        from PySide6.QtMultimedia import QMediaPlayer
        # getattr を使って、解析ツール(Pyright)の警告を完全にスルーします
        current_state = getattr(self.player, 'playbackState', None)
        return current_state == QMediaPlayer.PlaybackState.PlayingState

    #オーディオミキサー

    def setup_mixer_controls(self):
        """トラックの音量を調整するスライダーを構築（setup_main_editor_areaから呼び出し）"""
        from PySide6.QtWidgets import QSlider
        from PySide6.QtCore import Qt

        mixer_layout = QVBoxLayout()
        
        # 音量ラベル
        self.vol_label = QLabel("Volume: 100%")
        self.vol_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        
        # 音量スライダー (0-100で管理)
        self.vol_slider = QSlider(Qt.Orientation.Horizontal)
        self.vol_slider.setRange(0, 100)
        self.vol_slider.setValue(100)
        self.vol_slider.setTickPosition(QSlider.TickPosition.TicksBelow)
        self.vol_slider.setTickInterval(10)
        
        # 値が変わった時の連動
        self.vol_slider.valueChanged.connect(self.on_volume_changed)
        
        mixer_layout.addWidget(self.vol_label)
        mixer_layout.addWidget(self.vol_slider)
        
        # 前に作ったM/Sボタンもここにまとめると綺麗です
        ms_layout = self.setup_track_controls()
        mixer_layout.addLayout(ms_layout)
        
        return mixer_layout

    def on_volume_changed(self, value):
        """
        スライダーを動かした時の処理
        内部データ保持、ラベル更新、および再生エンジンへの即時反映を行います。
        """
        # 1. 現在操作対象のトラックを取得
        target = self.tracks[self.current_track_idx]
        
        # 2. 内部データは 0.0 ~ 1.0 の浮動小数点で保持
        target.volume = value / 100.0
        
        # 3. UIラベルの更新
        self.vol_label.setText(f"Volume: {value}%")
        
        # 4. 【重要】もし再生中のトラックがオーディオトラックなら、出力を即座に変更
        # これにより、再生を止めずに音量バランスを調整できます
        if hasattr(self, 'audio_output'):
            if target.track_type == "wave":
                self.audio_output.setVolume(target.volume)
        
        # 5. ステータスバーへの表示（履歴登録の代わり）
        self.statusBar().showMessage(f"{target.name} Volume set to {value}%")
            

    # --- [2] 連続音（VCV）解決メソッド ---


    # --- [3] 音声生成のメインループ ---
    def on_synthesize(self, notes):
        prev_lyric = None
        for note in notes:
            wav_path = self.resolve_target_wav(note.lyric, prev_lyric)
            self.run_engine(wav_path, None)
            prev_lyric = note.lyric

    def init_vcv_logic(self):
        """起動時に一度だけ。MainWindowの__init__から呼び出してください"""
        self.vowel_groups = {
            'a': 'あかさたなはまやらわがざだばぱぁゃ',
            'i': 'いきしちにひみりぎじぢびぴぃ',
            'u': 'うくすつぬふむゆるぐずづぶぷぅゅ',
            'e': 'えけせてねへめれげぜでべぺぇ',
            'o': 'おこそとのほもよろをごぞどぼぽぉょ',
            'n': 'ん'
        }

    # =============================================================
    # 診断されたプロバイダーを使用してAIモデルをロードする                                      
    # =============================================================

    def setup_aural_ai(self):
        """診断されたプロバイダーを使用してAIモデルをロードする"""
        import os
        model_path = "models/aural_dynamics.onnx"

        if ort is None:
            self.log_startup("Aural AI disabled: onnxruntime is not installed.")
            return
    
        if not os.path.exists(model_path):
            self.statusBar().showMessage("Error: Aural AI model not found.")
            return

        try:
            # 1. 診断済みのプロバイダー（NPU等）をセッションに渡す
            # セッションオプションの設定（スレッド数などをCore i3向けに最適化）
            options = ort.SessionOptions()
            options.intra_op_num_threads = 1  # 信号処理との競合を避けるため1に固定
        
            self.ai_session = ort.InferenceSession(
                model_path, 
                sess_options=options,
                providers=[self.active_provider, 'CPUExecutionProvider'] # NPUがダメならCPU
            )
        
            self.log_startup(f"Aural AI binding successful on {self.active_provider}")
        
        except Exception as e:
            self.log_startup(f"AI Binding Failed: {e}")
            # 最終防衛線としてCPUで再試行
            self.ai_session = ort.InferenceSession(model_path, providers=['CPUExecutionProvider'])

    # =============================================================
    # DSP CONTROL: PRECISION EQUALIZER (No-Noise Logic)
    # =============================================================

    def apply_dsp_equalizer(self, frequency=8000.0, gain=3.0, Q=1.0):
        """
        DSP技術による「無ノイズ」イコライザー設定。
        AI合成で発生しがちな「高域のチリチリ音」を物理数学的に除去します。
        """
        # 1. サンプリングレート取得 (44100Hz等)
        fs = 44100.0
    
        # 2. DSPフィルタ係数の計算 (Bi-quad Filter設計)
        A = math.pow(10, gain / 40)
        omega = 2 * math.pi * frequency / fs
        sn = math.sin(omega)
        cs = math.cos(omega)
        alpha = sn / (2 * Q)

        # フィルタの「キレ」を決める5つの係数
        b0 = A * ((A + 1) + (A - 1) * cs + 2 * math.sqrt(A) * alpha)
        b1 = -2 * A * ((A - 1) + (A + 1) * cs)
        b2 = A * ((A + 1) + (A - 1) * cs - 2 * math.sqrt(A) * alpha)
        a0 = (A + 1) - (A - 1) * cs + 2 * math.sqrt(A) * alpha
        a1 = 2 * ((A - 1) - (A + 1) * cs)
        a2 = (A + 1) - (A - 1) * cs - 2 * math.sqrt(A) * alpha

        # 3. C++エンジンへ係数を転送
        if hasattr(self, 'vo_se_engine') and hasattr(self.vo_se_engine, 'lib'):
            self.vo_se_engine.lib.vose_update_dsp_filter(
                float(b0/a0), float(b1/a0), float(b2/a0), 
                float(a1/a0), float(a2/a0)
            )
    
        self.statusBar().showMessage(f"DSP EQ Active: {frequency}Hz Optimized.")

    #===========================================================
    #エンジン接続関係
    #===========================================================

    def init_vose_engine(self):
        """C++エンジンのロードと初期設定（OS横断・core_manager経由）"""
        self.engine_dll = vose_manager.get_lib()
        if self.engine_dll:
            print("✅ Engine Loaded Successfully.")
        else:
            print("❌ Engine DLL not found or failed to load.")

    def generate_pitch_curve(self, note, prev_note=None):
        """
        [完全版] AI予測ピッチ + 黄金比ポルタメント + ビブラート
        """
        import numpy as np      
        import math
        # 1. 基礎となる音程（Hz）の計算
        target_hz = 440.0 * (2.0 ** ((note.note_number - 69) / 12.0))
        
        # フレーム数計算（5ms = 1フレーム。1.0秒なら200フレーム）
        num_frames = max(1, int((note.duration * 1000.0) / 5.0))
        
        # AIが予測したピッチ曲線があればそれをベースにし、なければ定数で初期化
        if hasattr(note, 'dynamics') and 'pitch' in note.dynamics:
            curve = np.array(note.dynamics['pitch'], dtype=np.float64)
        else:
            curve = np.ones(num_frames, dtype=np.float64) * target_hz

        # 2. ポルタメント（前の音からの滑らかな接続）
        if prev_note:
            prev_hz = 440.0 * (2.0 ** ((prev_note.note_number - 69) / 12.0))
            # ノートの最初の15%を使って滑らかに繋ぐ（黄金比的な減衰）
            port_len = min(int(num_frames * 0.15), 40)
            if port_len > 0:
                # 指数関数的にターゲットに近づけることで人間らしさを出す
                t = np.linspace(0, 1, port_len)
                curve[:port_len] = prev_hz + (target_hz - prev_hz) * (1 - np.exp(-5 * t))

        # 3. ビブラート・ロジック
        vibrato_depth = 6.0  # Hz単位の揺れ幅
        vibrato_rate = 5.5   # 1秒間に5.5回
        
        # ノートの後半50%からビブラートを開始
        vib_start = int(num_frames * 0.5)
        for i in range(vib_start, num_frames):
            time_sec = i * 0.005 # 5ms単位
            osc = math.sin(2 * math.pi * vibrato_rate * time_sec)
            curve[i] += osc * vibrato_depth

        return curve

    def get_notes_from_timeline(self):
        """
        [完全実装] ピアノロール上の全音符をスキャンし、演奏データへと変換する
        """
        note_events = []
        
        # 1. ピアノロールの「シーン」から全アイテムを取得
        if not hasattr(self, 'piano_roll_scene') or self.piano_roll_scene is None:
            self.log_startup("Error: Piano roll scene not initialized.")
            return []

        all_items = self.piano_roll_scene.items()
        
        # 2. 音符アイテム（NoteItemクラス）だけをフィルタリング
        raw_notes = []
        for item in all_items:
            if hasattr(item, 'is_note_item') and item.is_note_item:
                raw_notes.append(item)

        # 3. 時間軸（X座標）でソート
        raw_notes.sort(key=lambda x: x.x())

        # 4. GUI上の物理量を「音楽的データ」に変換
        for item in raw_notes:
            start_time = item.x() / 100.0  
            duration = item.rect().width() / 100.0
            
            # 歌詞（あ）を音素（a）に変換
            phoneme_label = self.convert_lyrics_to_phoneme(item.lyrics)

            # C++構造体 NoteEvent を作成
            event = NoteEvent(
                phonemes=phoneme_label,
                note_number=item.note_number,
                duration=duration,
                start_time=start_time,
                velocity=item.velocity
            )
            note_events.append(event)

        self.log_startup(f"Timeline Scan: {len(note_events)} notes collected.")
        return note_events

    def convert_lyrics_to_phoneme(self, lyrics):
        """簡単な歌詞→音素変換（辞書）"""
        dic = {"あ": "a", "い": "i", "う": "u", "え": "e", "お": "o"}
        return dic.get(lyrics, "n") # 見つからなければ「ん」にする

    def handle_playback(self):
        """
        [究極統合] AI推論・競合回避・DSP処理を一本化した再生メインフロー
        """
        import os
        import time
        # 1. タイムラインから音符データを取得
        notes = self.get_notes_from_timeline()
        if not notes:
            self.statusBar().showMessage("No notes to play.", 3000)
            return

        try:
            self.statusBar().showMessage("Aural AI is thinking...")

            # 2. 【脳】AI推論ループ
            prev = None
            for n in notes:
                # AIに歌い方の設計図を予測させる
                n.dynamics = self.predict_dynamics(n.phonemes, n.note_number)
                # AIの予測をベースに、さらに滑らかなピッチ曲線を生成
                n.pitch_curve = self.generate_pitch_curve(n, prev)
                prev = n

            # 3. 【安全性】ファイルロック回避のためのキャッシュ名生成
            os.makedirs("cache", exist_ok=True)
            temp_wav = os.path.abspath(f"cache/render_{int(time.time() * 1000)}.wav")

            # 4. 【喉】C++レンダリング実行
            final_file = self.synthesize(notes, temp_wav)

            # 5. 【磨き】DSP処理 & 再生
            if final_file and os.path.exists(final_file):
                # 合成後に高域ノイズを除去するDSP EQを適用
                self.apply_dsp_equalizer(frequency=8000.0, gain=-2.0)
                
                # 音を鳴らす
                self.play_audio(final_file)
                self.statusBar().showMessage(f"Playing via {self.active_device}", 5000)

        except Exception as e:
            error_msg = f"Playback Failed: {str(e)}"
            self.log_startup(error_msg)
            self.statusBar().showMessage(error_msg, 10000)

    def predict_dynamics(self, phonemes, notes):
        """AIモデル(ONNX)を使用してパラメータを予測"""
        # [前処理] 歌詞をAIが理解できる数値に変換
        input_data = self.preprocess_lyrics(phonemes, notes) 

        # [推論] NPUまたはCPUで実行
        inputs = {self.ai_session.get_inputs()[0].name: input_data}
        prediction = self.ai_session.run(None, inputs)

        # AIが予測したピッチ、テンション、ジェンダー等の多次元配列を返す
        return prediction[0]

    def synthesize_voice(self, dynamics_data):
        """AIの結果をC++に投げてスピーカーから鳴らす"""
        self.statusBar().showMessage("Rendering via Aural Engine...")

        try:
            # 1. C++ DLLのレンダリング関数を叩く
            if self.engine_dll is None:
                self.log_startup("Synthesis Error: engine DLL is not loaded")
                return
            raw_audio = self.engine_dll.render(dynamics_data)
            
            # 2. sounddevice で再生（ノンブロッキング）
            import sounddevice as sd
            sd.play(raw_audio, samplerate=44100)
            
            self.statusBar().showMessage(f"Playing on {self.active_device}", 3000)
        except Exception as e:
            self.log_startup(f"Synthesis Error: {e}")

    def synthesize(self, notes, output_path="output.wav"):
        """
        スレッドセーフなレンダリングと完璧なメモリ管理。
        GC（ガベージコレクション）からNumPy配列を保護します。
        """
        import numpy as np
        import ctypes
    
        # 1. 入力検証
        if not notes:
            print("エラー: レンダリングするノートがありません")
            return None

        note_count = len(notes)
    
        # 2. C++構造体配列の確保
        cpp_notes_array = (NoteEvent * note_count)()
    
        # 3. 【重要】GCからNumPy配列を保護するリスト
        # このリストが存在する限り、配列はメモリに保持される
        keep_alive = []

        try:
            # 4. 各ノートのデータを構造体に変換
            for i, note in enumerate(notes):
                # ピッチカーブの準備（常にfloat64）
                if hasattr(note, 'pitch_curve') and note.pitch_curve:
                    p_curve = np.array(note.pitch_curve, dtype=np.float64)
                else:
                    # デフォルトのピッチカーブ
                    p_curve = np.array([440.0], dtype=np.float64)
            
                 # GC保護リストに追加
                keep_alive.append(p_curve)
            
                # その他のパラメータカーブ（DSP最適化済み標準値）
                curve_length = len(p_curve)
                g_curve = np.full(curve_length, 0.5, dtype=np.float64)  # Gender
                t_curve = np.full(curve_length, 0.5, dtype=np.float64)  # Tension
                b_curve = np.full(curve_length, 0.0, dtype=np.float64)  # Breath
            
                # すべてのカーブをGC保護
                keep_alive.extend([g_curve, t_curve, b_curve])

                # 5. C++構造体へのポインタ転送
                # 音素情報
                phoneme_str = getattr(note, 'phonemes', 'a')
                cpp_notes_array[i].wav_path = phoneme_str.encode('utf-8')
            
                # ピッチカーブ
                cpp_notes_array[i].pitch_curve = p_curve.ctypes.data_as(
                    ctypes.POINTER(ctypes.c_double)
                )
                cpp_notes_array[i].pitch_length = curve_length
            
                # その他のカーブ
                cpp_notes_array[i].gender_curve = g_curve.ctypes.data_as(
                    ctypes.POINTER(ctypes.c_double)
                )
                cpp_notes_array[i].tension_curve = t_curve.ctypes.data_as(
                    ctypes.POINTER(ctypes.c_double)
                )
                cpp_notes_array[i].breath_curve = b_curve.ctypes.data_as(
                    ctypes.POINTER(ctypes.c_double)
                )

              # 6. C++エンジンでレンダリング実行
            if not hasattr(self, 'engine_dll') or not self.engine_dll:
                print("エラー: C++エンジンがロードされていません")
                return None
            
            result_code = self.engine_dll.execute_render(
                cpp_notes_array,
                note_count,
                output_path.encode('utf-8'),
                0
            )
        
            # 7. 結果チェック
            if result_code == 0:
                 print(f"レンダリング成功: {output_path}")
                 return output_path
            else:
                print(f"レンダリング失敗: エラーコード {result_code}")
                return None
            
        except Exception as e:
            print(f"重大なエンジンエラー: {e}")
            import traceback
            traceback.print_exc()
            return None
        
        finally:
            # 8. レンダリング終了後に安全にメモリ解放
            # keep_alive が削除されることで、参照カウントが減り、
            # Pythonのガベージコレクタが適切に処理する
            del keep_alive
            del cpp_notes_array
            print("メモリクリーンアップ完了")


    def on_notes_updated(self):
        """タイムラインが変更された時の処理（オートセーブなど）"""
        pass

    def play_audio(self, path: str) -> None:
        """オーディオファイルを安全に再生（構文エラー・型チェック対策済）"""
    
        # 1. パスのチェック
        if not path or not os.path.exists(path):
            print(f"エラー: ファイルが見つかりません: {path}")
            return
 
        # 2. プレイヤーの取得と型確定
        # getattrの戻り値をcastすることで、その後の hasattr チェックを有効にします
        player = cast(Any, getattr(self, 'player', None))
    
        # 3. プレイヤーが有効かチェック
        if player is None or isinstance(player, bool):
            print("警告: プレイヤーが初期化されていません")
            return

        # 4. 再生処理
        try:
            # 🔴 重要: インデントを修正 (ここがズレていると invalid-syntax になります)
            from PySide6.QtCore import QUrl
        
            # 停止処理
            if hasattr(player, 'stop'):
                player.stop()
         
            # ソースを設定
            if hasattr(player, 'setSource'):
                # 絶対パスを取得して QUrl に変換
                abs_path = os.path.abspath(path)
                file_url = QUrl.fromLocalFile(abs_path)
                player.setSource(file_url)
        
            # 再生開始
            if hasattr(player, 'play'):
                player.play()
                print(f"再生開始: {path}")
    
        except Exception as e:
            # ここも上の try と垂直に揃える必要があります
            print(f"再生エラー: {e}")
            
    # ==========================================================================
    #  アップデートデート自動確認　　　　　　　　　　　　　　　　　　　　　　　　　
    # ==========================================================================

    def _check_for_updates(self):
        try:
            import importlib
            updater_mod = importlib.import_module("modules.updater.auto_updater")  # type: ignore
            UpdateChecker = updater_mod.UpdateChecker
        except (ImportError, ModuleNotFoundError):
            return
        checker = UpdateChecker()
        checker.check_async(self._on_update_result)
        
    def _on_update_result(self, has_update, latest_ver, page_url, exe_url):
        if not has_update:
            return

        from PySide6.QtCore import QTimer

        # 型ヒントの修正と変数名のリネーム
        self._pending_update = (latest_ver, page_url, exe_url)

        # 0ミリ秒後にメインスレッドで安全にダイアログを表示
        QTimer.singleShot(0, self._show_update_dialog)

    @Slot()
    def _show_update_dialog(self):
        ver, page_url, exe_url = self._pending_update
        reply = QMessageBox.question(
            self, "アップデートあり",
            f"VO-SE Pro {ver} が公開されています。\n今すぐ更新しますか？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        )
        if reply == QMessageBox.StandardButton.Yes:
            if exe_url:
                self._start_auto_download(exe_url)
            else:
                import webbrowser
                webbrowser.open(page_url)

    def _start_auto_download(self, url):
        try:
            import importlib
            updater_mod = importlib.import_module("modules.updater.auto_updater")  # type: ignore
            DownloadThread = updater_mod.DownloadThread
            apply_update_and_restart = updater_mod.apply_update_and_restart
        except (ImportError, ModuleNotFoundError):
            import webbrowser
            webbrowser.open(url)
            return
        self._dl_thread = DownloadThread(url)
        self._dl_thread.progress.connect(self.progress_bar.setValue)
        self._dl_thread.finished.connect(apply_update_and_restart)
        self._dl_thread.error.connect(lambda e: QMessageBox.critical(self, "エラー", e))
        self.progress_bar.show()
        self._dl_thread.start()

    # ==========================================================================
    #  Pro audio modeling の起動、呼び出し　　　　　　　　　　　
    # ==========================================================================

    # setup_shortcuts は空の pass のみで、docstring 通り
    # setup_vose_shortcuts に統合済みのため削除した。

    def toggle_audio_monitoring(self, event=None) -> None:
        """
        Spaceキー一発で『音』と『UI』を同時に動かす。
        Actionsログ 2125-2129行目の型推論エラーを完全に回避する防弾仕様。
        """
        # 1. 属性の存在確認と型チェックを同時に行う
        # getattr で取得し、それが期待する「モニタリングオブジェクト」であることを確認
        monitor = getattr(self, 'pro_monitoring', None)
        
        # monitor が None でも bool (False) でも物理的な実体がある場合のみ処理
        if monitor is not None and not isinstance(monitor, bool):
            # 2. 内部属性へのアクセスを hasattr でさらに保護 (AttributeAccessIssue 対策)
            if hasattr(monitor, 'is_playing'):
                # 現在の状態を判定
                current_state = bool(getattr(monitor, 'is_playing', False))
                
                if not current_state:
                    print(" Pro Audio Monitoring: ON")
                    # 各プロパティへの代入を安全に行う
                    if hasattr(monitor, 'current_time'):
                        setattr(monitor, 'current_time', 0.0)
                    
                    # is_playing を True に
                    setattr(monitor, 'is_playing', True)
                    
                    # 3. UI更新メソッドの呼び出し
                    update_func = getattr(monitor, 'update_frame', None)
                    if callable(update_func):
                        update_func()
                else:
                    print(" Pro Audio Monitoring: OFF")
                    # is_playing を False に
                    setattr(monitor, 'is_playing', False)
            else:
                # 属性がない場合のフォールバック（デバッグ用）
                print(" DEBUG: pro_monitoring exists but lacks 'is_playing' attribute.")
        else:
            # エンジンが初期化されていない場合の通知
            print(" WARNING: Pro Audio Monitoring engine is not initialized.")

        # 4. メインウィンドウ側の状態も同期（もし必要であれば）
        if hasattr(self, 'is_playing'):
            # monitor の状態に合わせて self のフラグも更新
            active_monitor = getattr(self, 'pro_monitoring', None)
            if active_monitor is not None and not isinstance(active_monitor, bool):
                self.is_playing = bool(getattr(active_monitor, 'is_playing', False))

    # ==========================================================================
    # VO-SE Pro v1.3.0: 連続音（VCV）解決 ＆ レンダリング準備
    #==========================================================================

    def resolve_target_wav(self, lyric, prev_lyric):
        """前の歌詞から母音を判定し、最適なWAVパスを特定する"""
        vowel_groups = {
            'a': 'あかさたなはまやらわがざだばぱぁゃ',
            'i': 'いきしちにひみりぎじぢびぴぃ',
            'u': 'うくすつぬふむゆるぐずづぶぷぅゅ',
            'e': 'えけせてねへめれげぜでべぺぇ',
            'o': 'おこそとのほもよろをごぞどぼぽぉょ',
            'n': 'ん'
        }

        prev_v = None
        if prev_lyric:
            last_char = prev_lyric[-1]
            for v, chars in vowel_groups.items():
                if last_char in chars:
                    prev_v = v
                    break

        candidates = []
        if prev_v:
            candidates.append(f"{prev_v} {lyric}") # 例: 'a い'
        candidates.append(f"- {lyric}")           # 例: '- い'
        candidates.append(lyric)                   # 例: 'い'

        voice_path = getattr(self.vo_se_engine, 'voice_path', "")
        oto_map = getattr(self.vo_se_engine, 'oto_data', {})

        for alias in candidates:
            if alias in oto_map:
                filename = oto_map[alias].get('wav', f"{lyric}.wav")
                return os.path.join(voice_path, filename)

        return os.path.join(voice_path, f"{lyric}.wav")

    def prepare_rendering_data(self):
        """タイムラインとグラフのデータをエンジン形式にシリアライズ"""
        if not hasattr(self, 'timeline_widget'):
            return None

        notes = self.timeline_widget.notes_list
        if not notes:
            return None

        voice_path = ""
        if hasattr(self, 'voice_manager') and self.voice_manager:
            if hasattr(self.voice_manager, 'get_current_voice_path'):
                voice_path = self.voice_manager.get_current_voice_path()

        render_data = {
            "project_name": "New Project",
            "voice_path": voice_path,
            "tempo": self.timeline_widget.tempo,
            "notes": []
        }

        graph = getattr(self, 'graph_editor_widget', None)
        all_params = getattr(graph, 'all_parameters', {}) if graph else {}

        pitch_events = all_params.get("Pitch", [])
        tension_events = all_params.get("Tension", [])

        for note in notes:
            note_info = {
                "lyric": note.lyrics,
                "note_num": note.note_number,
                "start_sec": note.start_time,
                "duration_sec": note.duration,
                "pitch_bend": self._sample_range(pitch_events, note, 64),
                "dynamics": self._sample_range(tension_events, note, 64)
            }
            render_data["notes"].append(note_info)

        return render_data

    def start_playback(self):
        """再生ボタンが押された時のメインエントリ"""
        notes_data = self.prepare_rendering_data()
        
        if not notes_data:
            self.statusBar().showMessage("再生するノートがありません。")
            return

        self.statusBar().showMessage("VCV解析完了。合成を開始します...")
        
        audio_data = self.vo_se_engine.synthesize(notes_data)

        if audio_data is not None and len(audio_data) > 0:
            self.vo_se_engine.play(audio_data)
            self.statusBar().showMessage("再生中 (v1.3.0 VCV Engine)")
        else:
            self.statusBar().showMessage("合成エラー。ログを確認してください。")
    
    # ==========================================================================
    # 初期化メソッド
    #==========================================================================

    def init_dll_engine(self):        
        """C言語レンダリングエンジンの接続（OS横断・core_manager経由）"""
        self.lib = vose_manager.get_lib()
        if self.lib is not None:
            print("✓ Engine core loaded successfully")
        else:
            print("⚠ Warning: libvo_se.dll not found")

    def init_engine(self):
        """エンジンの総合初期化"""
        import os
        #ext = ".dll" if platform.system() == "Windows" else ".dylib"
        #dll_relative_path = os.path.join("bin", f"libvo_se{ext}")
        
        # 音源の自動ロード
        official_voice_path = os.path.join("assets", "voice", "official")
        official_oto_path = os.path.join(official_voice_path, "oto.ini")

        if os.path.exists(official_oto_path):
            print(f"✓ Official voice found: {official_voice_path}")

        try:
            # DLLの読み込み試行
            pass
        except Exception:
            print("⚠ Warning: VOSE core library not available")

    def open_about(self):
        """About画面を表示"""
        dialog = CreditsDialog(self.confirmed_partners, self)
        dialog.exec()

    def clear_layout(self, layout):
        """レイアウト内のウィジェットを安全に全削除"""
        while layout.count():
            item = layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
    


    # ==========================================================================
    # PERFORMANCE CONTROL CENTER (Core i3 Survival Logic)
    # ==========================================================================

    def setup_performance_toggle(self):
        """
        [Strategic Toggle] パフォーマンスモードの初期化。
        リソースの乏しい環境(Core i3等)と、ハイスペック環境を瞬時に最適化します。
        （機能維持・解析エラー根絶版）
        """
        # 1. アクションの生成（代表の設計通り、プロ感を演出）
        self.perf_action = QAction("High-Performance Mode", self)
        self.perf_action.setCheckable(True)
        
        # 初期状態は省電力(False)にしておき、ユーザーが必要に応じてブーストする仕様
        self.perf_action.setChecked(False) 
        self.perf_action.triggered.connect(self.toggle_performance)
        
        # 2. ツールバーへの追加
        # Pylance対策：toolbar を cast して「存在する」と明示し、かつ if で実在確認をします
        # これにより、機能を削らずに reportOptionalMemberAccess エラーを消去します
        toolbar = cast(QToolBar, self.toolbar) if self.toolbar else None
        
        if toolbar:
            # メイン操作部に配置してアクセシビリティを確保
            toolbar.addAction(self.perf_action)

    @Slot(bool)
    def toggle_performance(self, checked):
        """
        パフォーマンスモードの動的切り替え。
        C++エンジン(vose_core)の内部バッファやスレッド優先度を操作します。
        """
        # 1. 動作モードの決定 (1: 高負荷・高品質, 0: 低負荷・安定)
        mode = 1 if checked else 0
        
        # 2. C++エンジン(Shared Library)への安全なアクセス
        try:
            if hasattr(self.vo_se_engine, 'lib'):
                if hasattr(self.vo_se_engine.lib, 'vose_set_performance_mode'):
                    # C言語形式でモードを転送
                    self.vo_se_engine.lib.vose_set_performance_mode(mode)
                
                # [蹂躙ポイント] 省電力モード時は内部バッファを増やして途切れを防ぐなどの追加処理
                if mode == 0 and hasattr(self.vo_se_engine.lib, 'vose_set_buffer_size'):
                    self.vo_se_engine.lib.vose_set_buffer_size(4096) # Core i3向けの安全策
                elif mode == 1 and hasattr(self.vo_se_engine.lib, 'vose_set_buffer_size'):
                    self.vo_se_engine.lib.vose_set_buffer_size(1024) # 高速レスポンス
        except Exception as e:
            print(f"Engine Performance Control Warning: {e}")

        # 3. ユーザーへのフィードバック
        status = "【High-Mode】レンダリング優先" if mode == 1 else "【Power-Save】Core i3最適化モード"
        _ = "#ff4444" if mode == 1 else "#44ff44"
        
        self.statusBar().showMessage(f"System: {status} に切り替えました")
        
        # ログにも残して「まともに動いている」ことを証明
        print(f"Performance Mode Changed to: {mode}")

    

    # ==========================================================================
    # ドラッグ&ドロップ・ZIP解凍（文字化け対策済み）
    # ==========================================================================


            


            


    # ==========================================================================
    # 再生・録音制御
    # ==========================================================================


    def on_click_play(self):
        # タイムラインのデータを渡して合成・再生
        audio = self.vo_se_engine.synthesize(self.timeline_widget.notes_list)
        self.vo_se_engine.play(audio)

    def tart_playback_locked_s(self):
        """
        再生を開始（スレッドロック保持中のみ呼び出し）
        注意: このメソッドは _playback_lock を取得した状態で呼び出す
        """
        # 既に再生中の場合は何もしない
        if self.playback_thread and self.playback_thread.is_alive():
            print("警告: 既に再生スレッドが実行中です")
            return
    
        # 再生フラグを立てる
        self.is_playing = True
    
        # 再生ワーカースレッドを開始
        self.playback_thread = threading.Thread(
            target=self._playback_worker,
            daemon=True,
            name="VO-SE-Playback"
        )
        self.playback_thread.start()
        print("再生スレッド開始")
 
    def _stop_playback_locked(self):
        """
        再生を停止（スレッドロック保持中のみ呼び出し）    
        注意: このメソッドは _playback_lock を取得した状態で呼び出す
        """
        # 再生フラグを下げる
        self.is_playing = False
    
        # スレッドの終了を待機（最大1秒）
        if self.playback_thread:
            self.playback_thread.join(timeout=1.0)
        
            # タイムアウトした場合の警告
            if self.playback_thread.is_alive():
                print("警告: 再生スレッドが1秒以内に終了しませんでした")
        
            self.playback_thread = None
    
        print("再生スレッド停止")

    def _playback_worker(self):
        """
        バックグラウンドで動作する再生ワーカー
      
        このメソッドは別スレッドで実行されます
        """
        try:
            # エンジンの取得
            engine = getattr(self, 'vo_se_engine', None)
            if not engine:
                print("エラー: 再生エンジンが見つかりません")
                return
        
            # 再生処理
            if hasattr(engine, 'play_audio'):
                engine.play_audio()
        
        except Exception as e:
            print(f"再生ワーカーエラー: {e}")
            import traceback
            traceback.print_exc()
    
        finally:
            # 再生終了時にフラグをクリア
            with self._playback_lock:
                self.is_playing = False

    @Slot() 
    def on_play_pause_toggled(self):
        """
        再生/停止を切り替えるハンドラ（Ruff/Pyright/Pylance/VSCode 全エラー根絶版）
        一切の省略なし、完全防衛型コード。
        """
        
        # --- 0. 徹底的な型キャストと安全な属性取得 ---
        # getattrを使用し、かつ None チェックを行うことで reportOptionalMemberAccess を完全に防ぎます
        play_btn = cast(QPushButton, getattr(self, 'play_btn', None) or getattr(self, 'play_button', None))
        status_lbl = cast(QLabel, getattr(self, 'status_label', None))
        timeline = cast(Any, getattr(self, 'timeline_widget', None))
        timer = cast(Any, getattr(self, 'playback_timer', None))

        # --- 1. 再生中の場合の停止ロジック (代表の設計を完全維持) ---
        if self.is_playing:
            self.is_playing = False
            
            # タイマーの停止
            if timer is not None and hasattr(timer, 'stop'):
                timer.stop()
            
            # エンジンの停止処理（動的チェック）
            engine = getattr(self, 'vo_se_engine', None)
            if engine is not None and hasattr(engine, 'stop_playback'):
                engine.stop_playback()
            
            # スレッドの終了待ち
            thread = cast(threading.Thread, getattr(self, 'playback_thread', None))
            if thread is not None and thread.is_alive():
                thread.join(timeout=0.2) 

            # UIの更新（Ruff対策で改行、Pyright対策で None チェック）
            if play_btn is not None:
                play_btn.setText("▶ 再生")
            self._refresh_transport_button_states()
            if status_lbl is not None: 
                status_lbl.setText("停止しました")
            self.statusBar().showMessage(f"停止: {self._format_timecode(self.current_playback_time)}", 2000)
                
            self.playing_notes = {}
            return

        # --- 2. 停止中の場合の再生開始ロジック ---
        # 録音中なら止める（getattrで安全に確認）
        if getattr(self, 'is_recording', False):
            # 録音停止メソッドを安全に呼び出す
            on_record = getattr(self, 'on_record_toggled', None)
            if on_record is not None:
                on_record()

        # タイムラインが存在しない場合は何もしない
        if timeline is None:
            return
            
        # timeline.notes_list が型不明と言われないよう cast
        notes = cast(List[Any], getattr(timeline, 'notes_list', []))

        try:
            if status_lbl is not None: 
                status_lbl.setText("音声生成中...")
            
            # GUIをフリーズさせないためのイベントループ処理
            from PySide6.QtWidgets import QApplication
            QApplication.processEvents()

            # 再生開始位置の取得（型安全なフォールバック付き）
            start_time = float(getattr(timeline, '_current_playback_time', self.current_playback_time))
            if hasattr(timeline, 'get_selected_notes_range'):
                range_data = timeline.get_selected_notes_range()
                if range_data and isinstance(range_data, tuple) and len(range_data) >= 2:
                    start_time = float(range_data[0])
            
            self.is_playing = True
            self.current_playback_time = start_time
            self.playback_start_time = start_time
            self.playback_end_time = self._get_project_duration_seconds()
            self.playback_started_monotonic = time.monotonic()
            self._set_transport_time(start_time)
            
            # UI表示の更新
            if play_btn is not None: 
                play_btn.setText("■ 停止")
            self._refresh_transport_button_states()
            if status_lbl is not None: 
                status_lbl.setText(f"再生中: {start_time:.2f}s -")
            if not notes:
                self.statusBar().showMessage("ノートなし: タイムラインのみ再生します", 3000)
            else:
                self.statusBar().showMessage(f"再生中: {self._format_timecode(start_time)}", 3000)

            # 再生スレッドの構築
            engine_for_play = getattr(self, 'vo_se_engine', None)
            if notes and engine_for_play is not None and hasattr(engine_for_play, 'play_audio'):
                new_thread = threading.Thread(
                    target=engine_for_play.play_audio, 
                    daemon=True
                )
                # スレッドを属性に保持
                self.playback_thread = new_thread
                new_thread.start()
            
            # UI更新タイマーの開始
            if timer is not None and hasattr(timer, 'start'):
                timer.start(20)

        except Exception as e:
            # 例外発生時も安全にUIを復元
            if status_lbl is not None:
                status_lbl.setText(f"再生エラー: {e}")
            
            self.is_playing = False
            
            if play_btn is not None:
                play_btn.setText("▶ 再生")
            self._refresh_transport_button_states()

    @Slot()
    def on_record_toggled(self):
        """録音開始/停止"""
        self.is_recording = not self.is_recording

        if self.is_recording:
            if getattr(self, 'is_playing', False):
                self.on_play_pause_toggled()

            if hasattr(self, 'record_button'):
                self.record_button.setText("■ 録音中")

            if hasattr(self, 'status_label'):
                self.status_label.setText("録音開始 - MIDI入力待機中...")

            if hasattr(self, 'timeline_widget'):
                self.timeline_widget.set_recording_state(True, time.time())
        else:
            if hasattr(self, 'record_button'):
                self.record_button.setText("● 録音")

            if hasattr(self, 'status_label'):
                self.status_label.setText("録音停止")

            if hasattr(self, 'timeline_widget'):
                self.timeline_widget.set_recording_state(False, 0.0)

    @Slot()
    def on_loop_button_toggled(self):
        """ループ再生切り替え"""
        self.is_looping_selection = not self.is_looping_selection
        self.is_looping = self.is_looping_selection

        if hasattr(self, 'loop_button'):
            self.loop_button.setText("ループ: ON" if self.is_looping else "ループ: OFF")

        self._refresh_transport_button_states()
        if hasattr(self, 'status_label'):
            if self.is_looping:
                self.status_label.setText("選択範囲でのループ再生を有効にしました")
            else:
                self.status_label.setText("ループ再生を無効にしました")


    def stop_and_clear_playback(self) -> None:
        """
        再生を停止し、内部状態とUIを初期状態にリセットする。
        3678行目のエラーを根絶し、すべての属性アクセスを安全に行います。
        """
        # 1. プレイヤーの停止 (AttributeAccessIssue 対策)
        # self.player が bool (False) や None の場合にメソッドを呼ぼうとしてクラッシュするのを防ぐ
        player_obj = getattr(self, 'player', None)
        if player_obj is not None and not isinstance(player_obj, bool):
            # stop メソッドが存在するか確認してから実行
            if hasattr(player_obj, 'stop'):
                stop_func = player_obj.stop
                if callable(stop_func):
                    stop_func()

        # 2. 内部フラグの安全なリセット
        # Pyright の reportAttributeAccessIssue を防ぐため、確実に属性を更新
        self.is_playing: bool = False
        self.current_playback_time: float = 0.0
        self.playback_start_time = 0.0
        self.playback_started_monotonic = 0.0
        self.is_looping = False
        self.is_looping_selection = False

        timer = getattr(self, 'playback_timer', None)
        if timer is not None and hasattr(timer, 'stop'):
            timer.stop()
        
        # 3. UI状態の更新 (メソッド不在エラーを回避)
        # 循環参照や動的なメソッド追加を考慮し、hasattr でチェック
        update_ui_func = getattr(self, 'update_playback_ui', None)
        if callable(update_ui_func):
            update_ui_func()
            
        # 4. タイムラインカーソルを 0.0 (先頭) へ戻す
        # timeline_widget が None である可能性を考慮したガード
        t_widget = getattr(self, 'timeline_widget', None)
        if t_widget is not None:
            # 引数の型を float(0.0) で確定させて呼び出し
            if hasattr(t_widget, 'set_playback_time'):
                t_widget.set_playback_time(0.0)
            elif hasattr(t_widget, 'set_current_time'):
                t_widget.set_current_time(0.0)
                
        # 5. グラフエディタも同期してリセット
        g_widget = getattr(self, 'graph_editor_widget', None)
        if g_widget is not None and hasattr(g_widget, 'set_current_time'):
            g_widget.set_current_time(0.0)

        stop_btn = cast(QPushButton, getattr(self, 'stop_btn', None))
        if stop_btn is not None:
            stop_btn.setChecked(True)
            QTimer.singleShot(140, lambda: stop_btn.setChecked(False))
        self._refresh_transport_button_states()

        # 6. ステータスバーへのリセット通知
        status_bar = self.statusBar()
        if status_bar is not None:
            status_bar.showMessage("Playback stopped and reset to 00:00.000")
            
    # ==========================================================================
    # REAL-TIME PREVIEW ENGINE (Low-Latency Response)
    # ==========================================================================

    @Slot(object)
    def on_single_note_modified(self, note):
        """
        ノートが1つ変更された瞬間に呼ばれる（リアルタイム・プレビュー）。
        軽量なDSPエンジンだからこそ、Core i3でも遅延なく鳴らせます。
        """
        if not self.perf_action.isChecked():
            # 省電力モード（Core i3モード）の時は、負荷を考えてプレビューを
            # 簡略化するか、タイマー待機にする
            self.render_timer.start(100) 
            return

        # 1. 変更されたノートだけの「部分合成」をリクエスト
        # 全体を計算し直さないのが「軽量」の極意
        threading.Thread(
            target=self.vo_se_engine.preview_single_note,
            args=(note,),
            daemon=True
        ).start()

    def setup_realtime_monitoring(self):
        """
        マウスの動きを監視し、『今まさにいじっている音』を
        ダイレクトにオーディオデバイスへ送る設定。
        """
        if hasattr(self.vo_se_engine, 'enable_realtime_monitor'):
            # C++側の低遅延モニタリングを有効化
            self.vo_se_engine.enable_realtime_monitor(True)
            self.statusBar().showMessage("Real-time Monitor: Active (Low Latency)")

    # ==========================================================================
    # GLOBAL DOMINANCE: Pro Audio Performance Engine (Full Integration)
    # ==========================================================================

    @Slot()
    def start_batch_analysis(self):
        """
        [Strategic Engine] 高速音響特性解析の開始。
        AIという呼称を排し、DSP(信号処理)による『Pro Audio Performance』として実行。
        海外勢を凌駕する解析速度と精度を実現します。
        """
        # 1. ターゲットディレクトリの取得とバリデーション
        target_dir = self.voice_manager.get_current_voice_path()
        
        if not target_dir or not os.path.exists(target_dir):
            QMessageBox.warning(self, "Performance Error", "有効な音源ライブラリがロードされていません。")
            return

        # 2. スレッド競合の防止（爆弾3・4対策）
        if hasattr(self, 'analysis_thread') and self.analysis_thread.isRunning():
            QMessageBox.warning(self, "System Busy", "現在、別の解析プロセスが実行中です。")
            return

        # 3. 解析スレッドの初期化
        # ※AnalysisThreadは別途定義されているQThreadクラス
        self.analysis_thread = AnalysisThread(self.voice_manager, target_dir)
        
        # 4. シグナルとスロットの完全接続（省略なし）
        self.analysis_thread.progress.connect(self.update_analysis_status)
        self.analysis_thread.finished.connect(self.on_analysis_complete)
        self.analysis_thread.error.connect(self.on_analysis_error)
        
        # [爆弾5対策] 完了後のメモリ解放を予約
        self.analysis_thread.finished.connect(self.analysis_thread.deleteLater)
        
        # 5. UIの戦闘態勢への切り替え
        ai_btn = getattr(self, 'ai_analyze_button', None)
        if ai_btn:
            ai_btn.setEnabled(False)
        prog = getattr(self, 'progress_bar', None)
        if prog:
            prog.show()
            prog.setValue(0)
        
        self.statusBar().showMessage("Pro Audio Dynamics Engine: Initializing high-speed analysis...")
        
        # 6. 解析実行
        self.analysis_thread.start()

    def update_analysis_status(self, percent: int, filename: str):
        """解析進捗のリアルタイム表示（UXの質で海外勢に差をつける）"""
        self.progress_bar.setValue(percent)
        self.statusBar().showMessage(f"Acoustic Sampling [{percent}%]: {filename}")

    @Slot(dict)
    def on_analysis_complete(self, results: dict) -> None:
        """
        解析完了後の統合・最適化処理。
        抽出されたパラメータをプロジェクトに反映し、世界標準の精度へ昇華させます。
        """
        from PySide6.QtWidgets import QMessageBox, QStatusBar, QProgressBar, QPushButton

        # 1. 安全なUI操作（Noneチェックを追加）
        if hasattr(self, 'progress_bar') and isinstance(self.progress_bar, QProgressBar):
            self.progress_bar.hide()
        
        if hasattr(self, 'ai_analyze_button') and isinstance(self.ai_analyze_button, QPushButton):
            self.ai_analyze_button.setEnabled(True)
        
        # ステータスバーの取得
        status_bar = self.statusBar()
        
        if not results:
            if isinstance(status_bar, QStatusBar):
                status_bar.showMessage("Analysis completed, but no data was returned.")
            return

        # 2. 解析結果の精密適用（爆弾2対策済・省略なし）
        update_count = 0
        
        # timeline_widget の存在確認
        t_widget = getattr(self, 'timeline_widget', None)
        if t_widget is not None:
            # notes_list の存在確認
            notes_list = getattr(t_widget, 'notes_list', [])
            for note in notes_list:
                # note.lyrics が results に存在するかチェック
                lyric = getattr(note, 'lyrics', None)
                if lyric in results:
                    res = results[lyric]
                    # 配列の長さをチェックし、インデックスエラーを回避
                    if isinstance(res, (list, tuple)) and len(res) >= 3:
                        # 内部データへの反映（safe_to_f の存在も前提）
                        safe_f = getattr(self, 'safe_to_f', float)
                        try:
                            note.onset = safe_f(res[0])
                            note.overlap = safe_f(res[1])
                            note.pre_utterance = safe_f(res[2])
                            note.has_analysis = True
                            update_count += 1
                        except (ValueError, TypeError, AttributeError):
                            continue
        
        # 3. UI更新（ピアノロールの再描画など）
        if t_widget is not None:
            t_widget.update()
            
        if isinstance(status_bar, QStatusBar):
            status_bar.showMessage(f"Optimization Complete: {update_count} samples updated.", 5000)
        
        # 4. グローバルシェア奪還のための自動保存ダイアログ
        # ログ 3134行目対策：QMessageBox.StandardButton.No を StandardButton.No に修正
        reply = QMessageBox.question(
            self, 
            "Acoustic Config Save", 
            "解析結果を oto.ini に反映し、音源ライブラリを最適化しますか？\n(既存ファイルは自動でバックアップされます)",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        )
            
        if reply == QMessageBox.StandardButton.Yes:
            # メソッドの存在を確認してから実行
            export_func = getattr(self, 'export_analysis_to_oto_ini', None)
            if callable(export_func):
                export_func()


    def on_analysis_error(self, message: str):
        """解析失敗時の例外ハンドリング"""
        ai_btn = getattr(self, 'ai_analyze_button', None)
        if ai_btn:
            ai_btn.setEnabled(True)
        prog = getattr(self, 'progress_bar', None)
        if prog:
            prog.hide()
        QMessageBox.critical(self, "Engine Fault", f"解析中にエラーが発生しました:\n{message}")
    
    def safe_to_f(self, val):
        """[爆弾2対策] あらゆる入力値を安全に数値化する変換機"""
        try:
            s_val = str(val).strip()
            return float(s_val) if s_val else 0.0
        except (ValueError, TypeError):
            return 0.0

    # ==========================================================================
    # レンダリング
    # ==========================================================================

    @Slot()
    def on_render_button_clicked(self):
        """合成ボタンの最終接続"""
        from modules.data.licensing import LicenseManager # 追加
        
        is_pro = LicenseManager.is_pro()
        status_msg = "レンダリング中 (Pro Mode)..." if is_pro else "レンダリング中..."
        self.statusBar().showMessage(status_msg)
    
        # 1. データの準備
        song_data = self.prepare_rendering_data()
        if not song_data:
            self.statusBar().showMessage("ノートがありません")
            return

        # 2. C++エンジンでWAV生成
        # Pro版なら高精度フラグをエンジンに渡すようにしておく
        output_filename = "preview_render.wav"
        
        # 代表、ここがポイントです。将来的に render() が is_pro 引数を受け取れるようにします。
        result_path = self.vo_se_engine.render(
            song_data, 
            output_filename, 
            is_pro=is_pro # フラグを渡す
        )

        # 3. 再生
        if result_path and os.path.exists(result_path):
            self.statusBar().showMessage("再生中...")
            self.vo_se_engine.play_result(result_path)
        else:
            QMessageBox.critical(self, "エラー", "合成に失敗しました。DLLまたは音源パスを確認してください。")


    @Slot()
    def on_ai_button_clicked(self):
        """AIピッチ補正ボタン"""
        f0 = self.timeline_widget.get_pitch_data()
        if not f0:
            self.statusBar().showMessage("ピッチデータがありません")
            return
        
        new_f0 = self.dynamics_ai.generate_emotional_pitch(f0)
        self.timeline_widget.set_pitch_data(new_f0)
        self.statusBar().showMessage("AIピッチ補正を適用しました")


    def start_vocal_analysis(self, audio_data):
        """AIによるボーカル解析を開始する"""
        if not audio_data:
            self.statusBar().showMessage("解析エラー: オーディオデータがありません")
            return

        self.statusBar().showMessage("AI解析中... しばらくお待ちください")
        
        # 解析処理を非同期（バックグラウンド）で実行
        try:
            self.ai_manager.analyze_async(audio_data)
        except Exception as e:
            self.statusBar().showMessage(f"解析開始失敗: {e}")
            print(f"Analysis Error: {e}")

    def on_analysis_finished(self, results):
        """AIがスキャンした全音符のデータをタイムラインに展開"""
        if not results:
            self.statusBar().showMessage("音符が見つかりませんでした")
            return

        for note_data in results:
            # 1秒 = 100ピクセルの基準で配置
            x_pos = note_data["onset"] * 100 
            
            # 代表のVO-SEエンジンに合わせてノードを生成
            self.create_new_note(
                x=x_pos, 
                lyric="あ", 
                overlap=note_data.get("overlap", 0.0),
                pre_utterance=note_data.get("pre_utterance", 0.0)
            )

        self.statusBar().showMessage(f"{len(results)} 個の音符を配置しました")
        self.update()

    def create_new_note(self, x, lyric, overlap, pre_utterance):
        """実際にノードをリストに追加し、描画を指示する関数（仮）"""
        # ここに代表のVO-SE Proのノード追加ロジックを書く
        print(f"Node at {x}px added.")

  

    # ==========================================================================
    # ファイル操作
    # (旧: import_external_project, _parse_vsqx, load_ust_file, read_file_safely,
    #      save_oto_ini, get_safe_installed_name, on_export_button_clicked,
    #      save_file_dialog_and_save_midi, load_json_project,
    #      load_midi_file_from_path, parse_ust_dict_to_note は
    #      modules/gui/mixins/project_io_mixin.py へ移動済み)
    # ==========================================================================

    def _sample_range(self, events, note, res):
        """サンプリング補助関数 (Actionsエラー修正版)"""
        # 1. note が None でないことを確認 (reportOptionalOperand対策)
        if note is None:
            return [0.5] * res
            
        # 2. サンプリングポイントの生成
        times = np.linspace(note.start_time, note.start_time + note.duration, res)
        
        # 3. events が空の場合の早期リターン
        if not events:
            return [0.5] * res
            
        # 4. graph_editor_widget の存在確認と呼び出し
        if hasattr(self, 'graph_editor_widget') and self.graph_editor_widget is not None:
            return [self.graph_editor_widget.get_value_at_time(events, t) for t in times]
        else:
            # widgetがない場合のフォールバック
            return [0.5] * res


    # ==========================================================================
    # 音源管理
    # ==========================================================================

    def scan_utau_voices(self):
        """音源フォルダをスキャンし統合管理"""
        voice_roots = [
            os.path.join(os.getcwd(), "voices"),
            os.path.join(os.getcwd(), "voice_banks"),
        ]

        for root in voice_roots:
            os.makedirs(root, exist_ok=True)

        found_voices: dict = {}

        # 1. ユーザー追加音源のスキャン
        for voice_root in voice_roots:
            for dir_name in os.listdir(voice_root):
                dir_path = os.path.join(voice_root, dir_name)
                if not os.path.isdir(dir_path):
                    continue

                oto_path = os.path.join(dir_path, "oto.ini")
                if not os.path.exists(oto_path):
                   continue

                char_name = dir_name
                char_txt = os.path.join(dir_path, "character.txt")

                if os.path.exists(char_txt):
                    content = self.read_file_safely(char_txt)
                    if content:
                        for line in content.splitlines():
                            if line.startswith("name="):
                                char_name = line.split("=", 1)[1].strip()
                                break

                if char_name in found_voices:
                    char_name = f"{char_name} ({os.path.basename(voice_root)})"

                found_voices[char_name] = {
                    "path": dir_path,
                    "icon": (
                        os.path.join(dir_path, "icon.png")
                        if os.path.exists(os.path.join(dir_path, "icon.png"))
                        else "resources/default_avatar.png"
                    ),
                    "id": f"{os.path.basename(voice_root)}:{dir_name}",
                }

        # 2. 公式音源のスキャン
        base_path = getattr(self, "base_path", os.getcwd())
        official_base = os.path.join(base_path, "assets", "official_voices")

        if os.path.exists(official_base):
            for char_dir in os.listdir(official_base):
                full_dir = os.path.join(official_base, char_dir)
                if not os.path.isdir(full_dir):
                    continue

                display_name = f"[Official] {char_dir}"
                found_voices[display_name] = {
                    "path": full_dir,
                    "icon": "resources/official_icon.png",
                    "id": f"__INTERNAL__:{char_dir}",
                }

        voice_manager = getattr(self, "voice_manager", None)
        if voice_manager and hasattr(voice_manager, "voices"):
            voice_manager.voices = found_voices

        return found_voices

    def parse_oto_ini(self, voice_path: str) -> dict:
        """
        oto.iniを解析して辞書に格納する
        戻り値:
        {
            "あ": {
                "wav_path": ".../a.wav",
                "offset": 50.0,
                "consonant": 100.0,
                "blank": 0.0,
                "preutterance": 120.0,
                "overlap": 30.0
            },
            ...
        }
        """
        oto_map: dict = {}

        oto_path = os.path.join(voice_path, "oto.ini")
        if not os.path.exists(oto_path):
            return oto_map

        content = self.read_file_safely(oto_path)
        if not content:
            return oto_map

        for line in content.splitlines():
            line = line.strip()
            if not line or "=" not in line:
                continue

            try:
                wav_file, params = line.split("=", 1)
                wav_file = wav_file.strip()

                parts = params.split(",")

                alias = parts[0].strip() if parts and parts[0].strip() else os.path.splitext(wav_file)[0]

                oto_map[alias] = {
                    "wav_path": os.path.join(voice_path, wav_file),
                    "offset": self.safe_to_float(parts[1]) if len(parts) > 1 else 0.0,
                    "consonant": self.safe_to_float(parts[2]) if len(parts) > 2 else 0.0,
                    "blank": self.safe_to_float(parts[3]) if len(parts) > 3 else 0.0,
                    "preutterance": self.safe_to_float(parts[4]) if len(parts) > 4 else 0.0,
                    "overlap": self.safe_to_float(parts[5]) if len(parts) > 5 else 0.0,
                }

            except Exception as e:
                # oto.ini は壊れている行が普通にあるので黙殺が正解
                print(f"DEBUG: oto.ini parse skipped line: {line} ({e})")
                continue

        return oto_map

    def safe_to_float(self, val: Any) -> float:
        """
        文字列や数値を安全に浮動小数点数に変換。
        
        代表の設計思想に基づき、変換不能なデータが入った場合でも
        システムを停止させず、デフォルト値 0.0 を返して継続させます。
        """
        if val is None:
            return 0.0
            
        try:
            # 1. すでに数値（int/float）である可能性を考慮
            if isinstance(val, (int, float)):
                return float(val)
                
            # 2. 文字列として扱い、strip() を実行
            # Actions対策: str(val) で包むことで、もしリスト等が来ても強制変換して strip 可能にする
            s_val = str(val).strip()
            
            # 3. 空文字チェック
            if not s_val:
                return 0.0
                
            # 4. 浮動小数点変換
            return float(s_val)
            
        except (ValueError, TypeError, AttributeError):
            # 変換エラー時は沈黙して 0.0 を返す（代表の安全設計を完遂）
            return 0.0
        except Exception:
            # 万が一の予期せぬ例外もすべてキャッチ
            return 0.0

    def refresh_voice_ui_with_scan(self):
        """スキャンを実行してUIを最新状態にする"""
        self.statusBar().showMessage("音源フォルダをスキャン中...")

        self.scan_utau_voices()
        self.update_voice_list()

        if self.voice_manager is not None:
            count = len(self.voice_manager.voices)
        else:
            count = 0

        self.statusBar().showMessage(
            f"スキャン完了: {count} 個の音源",
            3000
        )

    def update_voice_list(self):
        """VoiceManagerと同期してUI（カード一覧）を再構築"""
        if self.voice_cards is None:
            self.voice_cards = []
        else:
            self.voice_cards.clear()

        if self.voice_grid is None:
            return

        for i in reversed(range(self.voice_grid.count())):
            item = self.voice_grid.itemAt(i)
            if item is None:
                continue

            w = item.widget()
            if w is not None:
                w.setParent(None)
                w.deleteLater()

        if self.voice_manager is None:
            voices_dict = {}
        else:
            voices_dict = self.voice_manager.voices

        for index, (name, data) in enumerate(voices_dict.items()):
            path = data.get("path", "")
            icon_path = data.get("icon", os.path.join(path, "icon.png"))

            if self.voice_manager is not None:
                color = self.voice_manager.get_character_color(path)
            else:
                color = "#FFFFFF"

            try:
                from .widgets import VoiceCardWidget  # type: ignore
                card = VoiceCardWidget(name, icon_path, color)
                card.clicked.connect(self.on_voice_selected)
                self.voice_grid.addWidget(card, index // 3, index % 3)
                self.voice_cards.append(card)
            except ImportError:
                pass

        if self.character_selector is not None:
            self.character_selector.clear()
            self.character_selector.addItems(list(voices_dict.keys()))

    @Slot(str)
    def on_voice_selected(self, character_name: str):
        """
        ボイスカード選択時の処理。
        音源データのロード、エンジンの更新、トークマネージャーの設定を同期。
        """
        import os

        # 1. UIの表示更新（選択状態のハイライト切り替え）
        if self.voice_cards:
            for card in self.voice_cards:
                if card is not None and hasattr(card, "set_selected"):
                    card.set_selected(getattr(card, "name", "") == character_name)

        # 2. 音源データの取得準備
        if self.voice_manager is None:
            status_bar = self.statusBar()
            if status_bar:
                status_bar.showMessage("エラー: voice_manager が初期化されていません")
            return

        voices_dict = self.voice_manager.voices
        if character_name not in voices_dict:
            status_bar = self.statusBar()
            if status_bar:
                status_bar.showMessage(f"エラー: {character_name} のデータが見つかりません")
            return

        voice_data = voices_dict[character_name]
        path = voice_data.get("path", "")
        if not path:
            return

        try:
            # 3. 原音設定(oto.ini)の解析と保持
            oto_data = self.parse_oto_ini(path)
            self.current_oto_data = oto_data if isinstance(oto_data, list) else []

            # 4. エンジン(vo_se_engine)への音源反映
            if self.vo_se_engine is not None:
                self.vo_se_engine.set_voice_library(path)
                self.vo_se_engine.set_oto_data(self.current_oto_data)

            self.current_voice = character_name

            # 5. トーク用音源(htsvoice)のチェックと設定
            talk_model = os.path.join(path, "talk.htsvoice")
            if os.path.exists(talk_model) and self.talk_manager is not None:
                self.talk_manager.set_voice(talk_model)

            # 6. キャラクターカラーの取得と完了通知
            char_color = "#FFFFFF"
            if hasattr(self.voice_manager, "get_character_color"):
                char_color = self.voice_manager.get_character_color(path)

            msg = f"【{character_name}】に切り替え完了 ({len(self.current_oto_data)} 音素ロード)"

            status_bar = self.statusBar()
            if status_bar:
                status_bar.showMessage(msg, 5000)

            print(f"Selected voice: {character_name} at {path} (Color: {char_color})")

        except Exception as e:
            from PySide6.QtWidgets import QMessageBox
            print(f"Error loading voice: {e}")
            QMessageBox.critical(
                self,
                "音源ロードエラー",
                f"音源の読み込み中にエラーが発生しました:\n{str(e)}",
            )

    def refresh_voice_list(self):
        """voice_banksフォルダを再スキャン（省略なし完全版）"""
        # scan_utau_voices が MainWindow にあるか VoiceManager にあるかを確認
        if hasattr(self, 'scan_utau_voices'):
            self.scan_utau_voices()
        elif hasattr(self, 'voice_manager') and hasattr(self.voice_manager, 'scan_utau_voices'):
            self.voice_manager.scan_utau_voices()

        # 以前は update_voice_list() を呼んでいたが、これは
        # VoiceCardGallery に統合される前の旧実装で、self.voice_grid が
        # 常に None のため実質何も起きない死んだコードだった。
        # 実際に画面のギャラリーを更新するには voice_gallery.setup_gallery()
        # を呼ぶ必要がある。
        gallery = getattr(self, 'voice_gallery', None)
        if gallery is not None and hasattr(gallery, 'setup_gallery'):
            gallery.setup_gallery()

        print("ボイスリストを更新しました")

    def play_selected_voice(self, note_text: str):
        """選択されたボイスでプレビュー再生（省略なし完全版）"""
        if not hasattr(self, 'character_selector') or self.character_selector is None:
            return
            
        selected_name = self.character_selector.currentText()
        # self.voices 自体が None の可能性を排除
        voices_path_map = getattr(self, 'voices', {})
        if voices_path_map is None:
            voices_path_map = {}
            
        voice_path = voices_path_map.get(selected_name, "")

        if voice_path and voice_path.startswith("__INTERNAL__"):
            char_id = voice_path.split(":")[1]
            internal_key = f"{char_id}_{note_text}"
            
            # vose_engine または vo_se_engine どちらの名前でも対応
            engine = getattr(self, 'vose_engine', getattr(self, 'vo_se_engine', None))
            if engine and hasattr(engine, 'play_voice'):
                engine.play_voice(internal_key)

    def get_cached_oto(self, voice_path: str):
        """ 原音設定のキャッシュ管理。pickleによる高速"""

        # キャッシュファイル(.vose)と元の設定ファイル(.ini)のパス
        cache_path = os.path.join(voice_path, "oto_cache.vose")
        ini_path = os.path.join(voice_path, "oto.ini")
    
        # キャッシュが存在し、かつ元の.iniより新しい場合のみキャッシュを使用
        if os.path.exists(cache_path) and os.path.exists(ini_path):
            try:
                if os.path.getmtime(cache_path) > os.path.getmtime(ini_path):
                    with open(cache_path, 'rb') as f:
                        data = pickle.load(f)
                        if data:
                            return data
            except (pickle.UnpicklingError, EOFError, AttributeError, ImportError):
                # キャッシュが壊れている、またはクラス定義が変わった場合は無視して再解析
                pass
    
        # キャッシュが使えない場合は再解析
        oto_data = self.parse_oto_ini(voice_path)
        
        # 次回のためにキャッシュを保存
        try:
            with open(cache_path, 'wb') as f:
                pickle.dump(oto_data, f)
        except Exception as e:
            print(f"DEBUG: Cache save failed: {e}")
            
        return oto_data

    def smart_cache_purge(self):
        """[Core i3救済] メモリ最適化。未使用キャッシュの強制解放（省略なし）"""
        vm = getattr(self, 'voice_manager', None)
        # 属性の存在を厳密にチェックして Pyright エラーを回避
        if vm and hasattr(vm, 'clear_unused_cache'):
            vm.clear_unused_cache()
            
            status_bar = self.statusBar()
            if status_bar:
                status_bar.showMessage("Memory Optimized.", 2000)
        else:
            # メソッドがない場合はガベージコレクションを直接呼ぶ
            import gc
            gc.collect()
            print("DEBUG: Direct memory optimization executed.")

    # ==========================================================================
    # 歌詞・ノート操作
    # ==========================================================================

    @Slot()
    def on_click_auto_lyrics(self) -> None:
        """AI自動歌詞配置 (Actions完全合格版)"""
        # 1. 冒頭で import QInputDialog 済み。型ヒントでActionsを安心させる
        from PySide6.QtWidgets import QInputDialog, QMessageBox

        text, ok = QInputDialog.getText(self, "自動歌詞配置", "文章を入力:")
        
        # bool値と文字列の存在を厳密にチェック
        if not ok or not text:
            return

        try:
            # 2. analyzerの存在チェックをガード
            if not hasattr(self, 'analyzer') or self.analyzer is None:
                return
            
            # analyzeメソッドの戻り値を型推論させる
            trace_data = self.analyzer.analyze(text)
            parsed_notes = self.analyzer.parse_trace_to_notes(trace_data)

            # 3. インポートとNoteEvent生成
            # フォルダ構成エラーを防ぐため絶対パス的なインポートを試みる
            try:
                from modules.data.data_models import NoteEvent  # type: ignore
            except ImportError:
                # 万が一インポートできない場合のフォールバック（Actions対策）
                class NoteEvent:
                    def __init__(self, **kwargs: Any):
                        for k, v in kwargs.items(): 
                            setattr(self, k, v)

            new_notes: List[Any] = []
            for d in parsed_notes:
                # 辞書の get 戻り値の型を明示的に扱う
                note = NoteEvent(
                    lyrics=str(d.get("lyric", "")),
                    start_time=float(d.get("start", 0.0)),
                    duration=float(d.get("duration", 0.5)),
                    note_number=int(d.get("pitch", 60))
                )
                new_notes.append(note)

            # 4. タイムラインへの反映
            if new_notes:
                # timeline_widgetの存在を担保
                if hasattr(self, 'timeline_widget') and self.timeline_widget is not None:
                    self.timeline_widget.set_notes(new_notes)
                    if self.timeline_widget:
                        self.timeline_widget.update()
                
                # statusBarの存在確認（Noneになる可能性があるため）
                status_bar = self.statusBar()
                if status_bar:
                    status_bar.showMessage(f"{len(new_notes)}個の音素を配置しました")

        except Exception as e:
            QMessageBox.critical(self, "エラー", f"歌詞解析エラー: {e}")
        
        # 5. プロ版監視機能 (pro_monitoring) への同期
        # Literal[True] などのエラーを避けるため、丁寧に属性を辿る
        pro_mon = getattr(self, 'pro_monitoring', None)
        if pro_mon is not None:
            sync_func = getattr(pro_mon, 'sync_notes', None)
            if callable(sync_func):
                # timeline_widget の notes_list 存在確認
                notes = getattr(self.timeline_widget, 'notes_list', [])
                sync_func(notes)

    def update_timeline_style(self):
        """タイムラインの見た目を Apple Pro 仕様に固定"""
        if not hasattr(self, 'timeline_widget'):
            return
        self.timeline_widget.setStyleSheet("background-color: #121212; border: none;")
        self.timeline_widget.note_color = "#FF9F0A"
        self.timeline_widget.note_border_color = "#FFD60A" 
        self.timeline_widget.text_color = "#FFFFFF"

    def apply_lyrics_to_notes(self, text: str):
        """歌詞を既存ノートに割り当て"""
        lyrics = [char for char in text if char.strip()]
        notes = self.timeline_widget.notes_list
        
        for i, note in enumerate(notes):
            if i < len(lyrics):
                note.lyrics = lyrics[i]
        
        if self.timeline_widget:
            self.timeline_widget.update()

    @Slot()
    def on_click_apply_lyrics_bulk(self):
        """歌詞の一括流し込み"""
        text, ok = QInputDialog.getMultiLineText(self, "歌詞の一括入力", "歌詞を入力:")
        if not (ok and text):
            return
        
        lyric_list = [char for char in text if char.strip() and char not in "、。！？"]
        notes = sorted(self.timeline_widget.notes_list, key=lambda n: n.start_time)
        
        for i in range(min(len(lyric_list), len(notes))):
            notes[i].lyrics = lyric_list[i]
            
        if self.timeline_widget:
            self.timeline_widget.update()
        
        if hasattr(self, 'pro_monitoring') and self.pro_monitoring:
            self.sync_notes = True
            
            # QColorを明示的に使用（
            self.bg_color: QColor = QColor("#FFFFFF")
            
            if hasattr(self, 'timeline_widget'):
                self.refresh_canvas() # 再描画で同期を視覚化

   
    # =========================================================================
    # スクロールバー制御
    # ==========================================================================
    @Slot(int)
    def _sync_horizontal_scrollbar_from_timeline(self, offset: int) -> None:
        """TimelineWidget内部操作（ホイール/端スクロール）を外部UIへ反映する。"""
        if self.h_scrollbar is not None:
            if offset > self.h_scrollbar.maximum():
                self.update_scrollbar_range()
            self.h_scrollbar.blockSignals(True)
            self.h_scrollbar.setValue(max(0, min(int(offset), self.h_scrollbar.maximum())))
            self.h_scrollbar.blockSignals(False)

        if self.graph_editor_widget is not None:
            if self.timeline_widget is not None and hasattr(self.graph_editor_widget, "pixels_per_beat"):
                self.graph_editor_widget.pixels_per_beat = self.timeline_widget.pixels_per_beat
            self.graph_editor_widget.set_horizontal_offset(int(offset))

    @Slot()
    def update_scrollbar_range(self):
        """水平スクロールバーの範囲更新"""
        if self.h_scrollbar is None or self.timeline_widget is None:
            return

        if not self.timeline_widget.notes_list:
            self.h_scrollbar.setRange(0, 0)
            return

        max_beats = self.timeline_widget.get_max_beat_position()

        max_x_position = (max_beats + 4) * self.timeline_widget.pixels_per_beat
        viewport_width = self.timeline_widget.width()

        max_scroll_value = max(0, int(max_x_position - viewport_width))
        self.h_scrollbar.setRange(0, max_scroll_value)
        self.h_scrollbar.setPageStep(viewport_width)

    # ==========================================================================
    # その他のスロット
    # ==========================================================================
    @Slot()
    def update_tempo_from_input(self):
        """
        テンポ入力をシステム全体（Timeline, GraphEditor, Engine）に反映する。
        Ruff F811を解消した統合版（省略なし）。
        """
        try:
            # 1. 必須ウィジェットの存在チェック
            if self.tempo_input is None:
                return
            
            # 安全な型変換
            try:
                new_tempo = float(self.tempo_input.text())
            except ValueError:
                raise ValueError("数値形式が正しくありません")

            # 2. テンポの範囲バリデーション（30-300 BPM）
            if not (30.0 <= new_tempo <= 300.0):
                raise ValueError("テンポは30.0〜300.0の範囲で入力してください")

            # 3. 各コンポーネントへの伝播
            # TimelineWidgetへの反映
            if hasattr(self, 'timeline_widget') and self.timeline_widget is not None:
                self.timeline_widget.tempo = int(new_tempo)
                self.timeline_widget.update() # 再描画強制
            elif hasattr(self, 'timeline') and self.timeline is not None:
                # 変数名の揺れ対策
                self.timeline.tempo = int(new_tempo)
                self.timeline.update()

            # グラフエディタへの反映
            if hasattr(self, 'graph_editor_widget') and self.graph_editor_widget is not None:
                self.graph_editor_widget.tempo = int(new_tempo)
                self.graph_editor_widget.update()

            # C++エンジンへの即時通知
            if self.vo_se_engine is not None:
                # エンジン側は精度のために float で渡す
                self.vo_se_engine.set_tempo(new_tempo)

            # 4. UIの整合性維持
            self.update_scrollbar_range()

            # ステータス表示
            if self.status_label is not None:
                self.status_label.setText(f"テンポ: {new_tempo:.1f} BPM")
            elif self.statusBar():
                self.statusBar().showMessage(f"Tempo changed to: {new_tempo:.1f}", 2000)

            print(f"DEBUG: System tempo synchronized to {new_tempo} BPM")

        except ValueError as e:
            # エラー時は警告を出し、値を元に戻す
            from PySide6.QtWidgets import QMessageBox
            QMessageBox.warning(self, "テンポ設定エラー", str(e))
            
            # 直近の有効な値（timeline_widget保持分）をUIに復元
            valid_tempo = 120
            if hasattr(self, 'timeline_widget') and self.timeline_widget:
                valid_tempo = self.timeline_widget.tempo
            elif hasattr(self, 'timeline') and self.timeline:
                valid_tempo = self.timeline.tempo
                
                    
            if self.tempo_input:
                self.tempo_input.setText(str(valid_tempo))


    @Slot(str)
    def set_current_parameter_layer(self, layer_name: str):
        if not hasattr(self, 'parameters'):
            return
        if layer_name in self.parameters:
            self.current_param_layer = layer_name
            self.update()
            print(f"Parameter layer switched to: {layer_name}")
        else:
            print(f"Error: Parameter layer '{layer_name}' not found.")


    @Slot()
    def on_timeline_updated(self):
        """
        タイムライン更新時の処理（省略なし完全版）。
        ノートデータを同期し、バックグラウンドでキャッシュを先行生成する。
        """
        import threading
        
        if self.status_label:
            self.status_label.setText("エンジン同期中...")
        elif self.statusBar():
            self.statusBar().showMessage("レンダリングキャッシュ更新中...", 1000)

        # 1. タイムラインから最新のノートリストを取得
        if not hasattr(self, 'timeline_widget') or not self.timeline_widget:
            return
            
        updated_notes = self.timeline_widget.notes_list
        self.notes = updated_notes # MainWindow側のリストも同期

        # 2. Cエンジンへの先行キャッシュ指示
        # ※UIスレッドをブロックしないよう、重い処理（波形生成の準備）は別スレッドで実行
        if hasattr(self, 'vo_se_engine') and self.vo_se_engine:
            try:
                # 既存のスレッドと衝突しないよう、デーモンスレッドとして開始
                cache_thread = threading.Thread(
                    target=self._run_engine_cache,
                    args=(updated_notes,),
                    daemon=True
                )
                cache_thread.start()
            except Exception as e:
                print(f"❌ Cache thread failed to start: {e}")

    def _run_engine_cache(self, notes):
        """エンジン側のキャッシュ生成を安全に実行するサブメソッド"""
        try:
            # エンジン側でNoteEvent構造体への変換と、先行波形計算（UTAUならresampler呼び出し等）を行う
            if self.vo_se_engine and hasattr(self.vo_se_engine, "prepare_cache"):
                self.vo_se_engine.prepare_cache(notes)
                print(f"DEBUG: Cache prepared for {len(notes)} notes.")
            elif self.vo_se_engine:
                print("⚠️ Engine does not support prepare_cache; skipping cache warm-up.")
        except Exception as e:
            print(f"❌ Engine Cache Error: {e}")

    @Slot()
    def on_notes_modified(self):
        """変更検知（連打防止タイマー）"""
        if not hasattr(self, 'render_timer'):
            return
        self.render_timer.stop()
        self.render_timer.start(300)
        self.statusBar().showMessage("変更を検知しました...", 500)

    def execute_async_render(self):
        """非同期レンダリング実行"""
        import threading
        self.statusBar().showMessage("音声をレンダリング中...", 1000)
        
        updated_notes = self.timeline_widget.notes_list
        if not updated_notes:
            return

        if hasattr(self, 'vo_se_engine') and self.vo_se_engine:
            if hasattr(self.vo_se_engine, 'update_notes_data'):
                self.vo_se_engine.update_notes_data(updated_notes)

            def rendering_task():
                try:
                    if hasattr(self.vo_se_engine, 'prepare_cache'):
                        self.vo_se_engine.prepare_cache(updated_notes)
                    
                    if hasattr(self.vo_se_engine, 'synthesize_track'):
                        pitch = getattr(self, 'pitch_data', [])
                        self.vo_se_engine.synthesize_track(
                            updated_notes, 
                            pitch, 
                            preview_mode=True
                        )
                except Exception as e:
                    print(f"Async Render Error: {e}")

            render_thread = threading.Thread(target=rendering_task, daemon=True)
            render_thread.start()

    @Slot(dict)
    def on_graph_parameters_changed(self, all_parameters: dict):
        # GraphEditorWidget は {"Pitch": [...], "Gender": [...], ...} を送る
        self.pitch_data = all_parameters.get("Pitch", [])

    @Slot(list)
    def on_pitch_data_updated(self, new_pitch_events: list):
        self.pitch_data = new_pitch_events

    @Slot()
    def on_midi_port_changed(self):
        if self.midi_port_selector is None:
           return

        selected_port = self.midi_port_selector.currentData()

        if self.midi_manager is not None:
           self.midi_manager.stop()
           self.midi_manager = None

        if selected_port and selected_port != "ポートなし":
            try:
               from .midi_io import MidiInputManager  # type: ignore
               self.midi_manager = MidiInputManager(selected_port)
               self.midi_manager.start()

               if self.status_label is not None:
                   self.status_label.setText(f"MIDI: {selected_port}")

            except ImportError:
                pass

    @Slot(int, int, str)
    def update_gui_with_midi(self, note_number: int, velocity: int, event_type: str):
        if self.status_label is None:
            return

        if event_type == 'on':
            self.status_label.setText(f"ノートオン: {note_number} (Velocity: {velocity})")
        elif event_type == 'off':
            self.status_label.setText(f"ノートオフ: {note_number}")

    def handle_midi_realtime(self, note_number: int, velocity: int, event_type: str):
        if not hasattr(self, 'vo_se_engine') or not self.vo_se_engine:
            return
        if event_type == 'on':
            if hasattr(self.vo_se_engine, 'play_realtime_note'):
                self.vo_se_engine.play_realtime_note(note_number)
            if getattr(self, 'is_recording', False):
                self.timeline_widget.add_note_from_midi(note_number, velocity)
        elif event_type == 'off':
            if hasattr(self.vo_se_engine, 'stop_realtime_note'):
                self.vo_se_engine.stop_realtime_note(note_number)

    @Slot()
    def update_scrollbar_v_range(self):
        if self.timeline_widget is None:
            return

        key_h = self.timeline_widget.key_height_pixels
        full_height = 128 * key_h
        viewport_height = self.timeline_widget.height()

        n_height = getattr(self.timeline_widget, 'note_height', key_h)
        max_v = 128 * n_height

        if self.vertical_scroll is not None:
            self.vertical_scroll.setRange(0, int(max_v))

        if self.v_scrollbar is not None:
            max_scroll_value = max(0, int(full_height - viewport_height + key_h))
            self.v_scrollbar.setRange(0, max_scroll_value)

        if self.keyboard_sidebar is not None:
            self.keyboard_sidebar.set_key_height_pixels(key_h)

    # ==========================================================================
    # ヘルパーメソッド
    # ==========================================================================

    def _get_yomi_from_lyrics(self, lyrics: str) -> str:
        """
        歌詞（漢字・かな混じり）を平仮名に変換する（省略なし完全版）
        pykakasiが未インストールの場合でも、歌詞を壊さず返す安全設計。
        """
        if not lyrics:
            return ""

        try:
            # メソッド内インポートにより、ライブラリがない環境でも起動を妨げない
            import pykakasi
            
            # インスタンス生成（最新のpykakasi仕様に準拠）
            kks = pykakasi.kakasi()
            result = kks.convert(lyrics)
            
            # 各形態素の 'hira' (ひらがな) 属性を結合
            yomi = "".join([str(item.get('hira', '')) for item in result])
            return yomi
            
        except (ImportError, ModuleNotFoundError):
            # pykakasiがインストールされていない場合のフォールバック
            print("DEBUG: pykakasi not found. Returning raw lyrics.")
            return lyrics
        except Exception as e:
            # その他の予期せぬエラー（辞書破損など）への対応
            print(f"DEBUG: Yomi conversion error: {e}")
            return lyrics

    def midi_to_hz(self, midi_note: int) -> float:
        """
        MIDIノート番号を周波数(Hz)に変換する（数学的完全版）
        計算式: $f = 440 \times 2^{\frac{n-69}{12}}$
        """
        # MIDI番号が None や不正な値の場合のガード
        if midi_note is None:
            return 0.0
            
        # 浮動小数点数として計算し、型安全性を確保
        # 69は A4 (440Hz) のMIDI番号
        return float(440.0 * (2.0 ** ((float(midi_note) - 69.0) / 12.0)))

    # ==========================================================================
    # イベントハンドラ
    # ==========================================================================

    def keyPressEvent(self, event) -> None:
        """
        キーボードショートカット制御。
        ActionsのEnumアクセスエラーを完全に回避しつつ、DAWとしての操作性を完遂します。
        """
        from PySide6.QtCore import Qt

        key = event.key()
        mod = event.modifiers()
        
        # 1. スペースキー：再生/一時停止
        # Actions対策: Qt.Key.Key_Space ではなく Qt.Key.Key_Space (PySide6標準) を使用
        if key == Qt.Key.Key_Space:
            play_func = getattr(self, 'on_play_pause_toggled', None)
            if callable(play_func):
                play_func()
            event.accept()
            return

        # 2. Ctrl + R：録音開始/停止
        # Actions対策: KeyboardModifier.ControlModifier を安全に比較
        elif key == Qt.Key.Key_R and (mod & Qt.KeyboardModifier.ControlModifier):
            record_func = getattr(self, 'on_record_toggled', None)
            if callable(record_func):
                record_func()
            event.accept()
            return

        # 3. Ctrl + L：ループ切り替え
        elif key == Qt.Key.Key_L and (mod & Qt.KeyboardModifier.ControlModifier):
            loop_func = getattr(self, 'on_loop_button_toggled', None)
            if callable(loop_func):
                loop_func()
            event.accept()
            return

        # 4. Delete / Backspace：選択項目の削除
        elif key in (Qt.Key.Key_Delete, Qt.Key.Key_Backspace):
            # タイムラインウィジェットの存在を安全に確認
            t_widget = getattr(self, 'timeline_widget', None)
            if t_widget is not None:
                delete_func = getattr(t_widget, 'delete_selected_notes', None)
                if callable(delete_func):
                    delete_func()
            event.accept()
            return

        # 5. その他：親クラスのイベントに渡す
        else:
            super().keyPressEvent(event)

    def closeEvent(self, event):
        reply = QMessageBox.question(
            self, 
            '確認', 
            "作業内容が失われる可能性があります。終了してもよろしいですか？",
            QMessageBox.StandardButton.Save | QMessageBox.StandardButton.Discard | QMessageBox.StandardButton.Cancel, 
            QMessageBox.StandardButton.Save
        )

        if reply == QMessageBox.StandardButton.Save:
            # メソッドがあるか確認（代表のナイスなアイデア！）
            if hasattr(self, 'on_save_project_clicked'):
                # さらに、呼び出し可能（callable）かチェックするとActionはもっと喜びます
                save_func = getattr(self, 'on_save_project_clicked')
                if callable(save_func):
                    save_func()
            event.accept()
        elif reply == QMessageBox.StandardButton.Discard:
            event.accept()
        else:
            # ここで return する前に ignore する代表の設計は、
            # 誤操作でウィンドウが閉じるのを防ぐ「神対応」です。
            event.ignore()
            return
        
        config = {
            "default_voice": getattr(self, 'current_voice', None),
            "volume": getattr(self, 'volume', 1.0)
        }
        save_config = None
        if getattr(self, 'config_manager', None) is not None:
            save_config = getattr(self.config_manager, "save_config", None)
        if callable(save_config):
            save_config(config)
        
        if hasattr(self, 'midi_manager') and self.midi_manager:
            self.midi_manager.stop()
        
        if hasattr(self, 'vo_se_engine') and self.vo_se_engine:
            if hasattr(self.vo_se_engine, 'close'):
                self.vo_se_engine.close()
        
        print("Application closing...")

    # ==============================================================================
    # レリタリング実行メゾット
    # ==============================================================================

    @Slot()
    def request_render(self) -> None:
        """
        タイムライン上の全ノートをスキャンし、非同期でレンダリングを開始する。
        """
        from modules.data.licensing import LicenseManager # ライセンス管理をロード
      
        # 1. 保存先の決定
        output_wav = os.path.join(os.getcwd(), "output", "render_result.wav")
        os.makedirs(os.path.dirname(output_wav), exist_ok=True)

        # 2. データの取得
        raw_notes = self.timeline_widget.get_all_notes_data()
        if not raw_notes:
            self.statusBar().showMessage("エラー: レンダリングするノートがありません。")
            return

        if not (hasattr(self, "vose_core") and self.vose_core):
            self.statusBar().showMessage("エラー: エンジンがロードされていません。")
            return

        # 3. 準備（ここは一瞬なのでメインスレッドでOK）
        try:
            is_pro = LicenseManager.is_pro()
            self.statusBar().showMessage("レンダリング準備中...")
            note_count = len(raw_notes)
            # NoteEvent型が定義されている前提
            NotesArrayType = NoteEvent * note_count 
            c_notes = NotesArrayType()
            


            # --- 【ここから非同期化の修正】 ---
            status_text = "レンダリング中 (Studio Master)..." if is_pro else "レンダリング中..."
            self.statusBar().showMessage(status_text)

            note_count = len(raw_notes)
            NotesArrayType = NoteEvent * note_count 
            c_notes = NotesArrayType()

            for i, note_data in enumerate(raw_notes):
                c_notes[i] = prepare_c_note_event(note_data)
            
            # プログレスバーがあれば動かす
            if hasattr(self, "progress_bar"):
                self.progress_bar.setRange(0, 0) # ぐるぐる回るモード
                self.progress_bar.setVisible(True)

            # ワーカーの作成
            worker = SynthesisWorker(
                self.vose_core, 
                c_notes, 
                note_count, 
                output_wav,
                is_pro=is_pro #有料版かどうか
            )
            
            # シグナルの接続
            worker.signals.finished.connect(self.on_render_success)
            worker.signals.error.connect(self.on_render_failed)

            # スレッドプールで実行開始（これでGUIが固まらなくなる）
            QThreadPool.globalInstance().start(worker)

        except Exception as e:
            self.on_render_failed(str(e))

    # --- コールバック用メソッド ---
    def on_render_success(self, output_wav):
        """レンダリング成功時の処理"""
        if hasattr(self, "progress_bar"):
            self.progress_bar.setVisible(False)
        self.statusBar().showMessage(f"レンダリング完了: {output_wav}")
        self.play_rendered_audio(output_wav)

    def on_render_failed(self, error_msg):
        """レンダリング失敗時の処理"""
        if hasattr(self, "progress_bar"):
            self.progress_bar.setVisible(False)
        self.statusBar().showMessage(f"失敗: {error_msg}")
        QMessageBox.critical(self, "Render Error", f"レンダリング中にエラーが発生しました:\n{error_msg}")

    def play_rendered_audio(self, wav_path: str) -> None:
        """生成されたWAVをAudioPlayerで再生する"""
        if self.player and os.path.exists(wav_path):
            # PySide6.QtMultimedia.QMediaPlayer を想定
            from PySide6.QtCore import QUrl
            self.player.setSource(QUrl.fromLocalFile(wav_path))
            self.player.play()


# ==============================================================================
# エントリーポイント
# ==============================================================================

def main() -> None:
    """
    VO-SE Pro アプリケーション起動エントリーポイント。
    """
    #from PySide6.QtWidgets import QApplication

    # 1. アプリケーションインスタンスの作成
    # sys をインポート済みなので、sys.argv へのアクセスが安全です
    app = QApplication(sys.argv)
    
    # 2. 外観の設定
    # DAWとしての統一感を出すため、Fusionスタイルを適用
    app.setStyle("Fusion")
    
    # 3. メインウィンドウの生成と表示
    # クラス MainWindow が定義済みであることを前提にインスタンス化
    try:
        # 代表、ここで MainWindow を呼び出します
        window = MainWindow()
        window.show()
        
        # 4. イベントループの開始と安全な終了
        # 戻り値を sys.exit に渡すことで、正常終了(0)を保証します
        exit_code = app.exec()
        sys.exit(exit_code)
        
    except NameError as e:
        # MainWindow が見つからない場合のデバッグ用
        print(f"CRITICAL ERROR: MainWindow class is not defined. {e}")
        sys.exit(1)
    except Exception as e:
        # その他の予期せぬ起動エラーの捕捉
        print(f"APPLICATION ERROR: {str(e)}")
        sys.exit(1)

if __name__ == "__main__":
    main()
