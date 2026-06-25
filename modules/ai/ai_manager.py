# modules/ai/ai_manager.py

import os
import sys
import logging
import json
import numpy as np
from concurrent.futures import ThreadPoolExecutor
from PySide6.QtCore import QObject, Signal
from modules.data.licensing import LicenseManager

logger = logging.getLogger(__name__)


class AIManager(QObject):
    """
    VO-SE Pro / Ultra: AI推論マネージャー (フェーズ1: 刷新版)

    【断捨離・安定化の修正ポイント】
    - 古いONNX Runtime（ort）への依存およびセッション初期化コードを完全消去。
    - サンプリング音源用の oto.ini との重み付けブレンドロジック（古い設計）を排除。
    - Pro版（VITS単体）および Ultra版（VITS + BigVGAN）の波形生成推論へ移行するためのクリーンなインターフェースに集約。
    - analyze_async などの主要エントリーポイントに try-except を徹底し、無言クラッシュを完全に防御。
    """

    finished = Signal(object)  # 推論成功時に結果（将来は音声波形など）を送るシグナル
    error = Signal(str)       # エラー発生時にエラーメッセージを送るシグナル

    def __init__(self):
        super().__init__()
        # 推論は1件ずつ安全に順番に行う（GUIのプチフリーズを防ぐ別スレッド実行環境）
        self.executor = ThreadPoolExecutor(max_workers=1)
        self.model_path = self._get_model_path()

        # 音素データの保持
        self.phoneme_dict: dict = {}
        self.dict_path = self._get_dict_path()
        
        # モデルと辞書の初期化を実行
        self.init_model()

    # ============================================================
    # パス解決
    # ============================================================

    def _get_model_path(self) -> str:
        """
        将来的なVITS型AIモデルのパス解決。
        Pro / Ultra のプランに応じてロード対象を切り替える土台を残します。
        """
        try:
            if getattr(sys, 'frozen', False):
                base = getattr(sys, '_MEIPASS', os.path.dirname(os.path.abspath(__file__)))
            else:
                base = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
            models_dir = os.path.join(base, "models")

            # ライセンスに応じてモデルを切り替えるロジックの担保
            if LicenseManager.is_pro():
                # Pro / Ultra 共通のコアVITSモデルのロードを想定
                pro_model = os.path.join(models_dir, "vose_pro_vits.onnx")
                if os.path.exists(pro_model):
                    logger.info(f"[AI] Pro/Ultra VITS model selected: {pro_model}")
                    return pro_model
            
            return os.path.join(models_dir, "vose_default_core.onnx")
        except Exception as e:
            logger.error(f"Failed to resolve model path: {e}")
            return ""

    def _get_dict_path(self) -> str:
        """音素辞書ファイルのパス解決"""
        if getattr(sys, 'frozen', False):
            base = getattr(sys, '_MEIPASS', os.path.dirname(os.path.abspath(__file__)))
        else:
            base = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        return os.path.join(base, "dicts", "phoneme_table.json")

    # ============================================================
    # モデルおよび辞書の初期化
    # ============================================================

    def init_model(self) -> bool:
        """音素辞書の読み込みと、将来のVITS推論環境の初期化準備"""
        try:
            # 1. 音素辞書の読み込み
            if os.path.exists(self.dict_path):
                with open(self.dict_path, 'r', encoding='utf-8') as f:
                    self.phoneme_dict = json.load(f)
                logger.info(f"[AI] Phoneme dictionary loaded: {self.dict_path}")
            else:
                logger.warning(f"[AI] Phoneme dictionary not found: {self.dict_path}. Using empty dict.")

            # 2. VITS推論エンジンの準備完了フラグ
            # ※ フェーズ1ではONNXランタイムを削ったため、ここでは基盤準備のみ完了とする
            logger.info("[AI] VITS Core Interface initialized successfully.")
            return True

        except Exception as e:
            error_msg = f"AI Init Error: {e}"
            logger.error(error_msg)
            self.error.emit(error_msg)
            return False

    # ============================================================
    # 音素解析 (VITSの最重要インプット)
    # ============================================================

    def text_to_phonemes(self, text: str) -> list:
        """
        テキストを歌唱用の音素記号のリストに変換する。
        pyopenjtalk を安全に呼び出し、失敗時は文字分解へフォールバック。
        """
        if not text:
            return []

        words_map = self.phoneme_dict.get("words", {})
        if text in words_map:
            return words_map[text]
            
        try:
            import pyopenjtalk
            g2p_result = pyopenjtalk.g2p(text, kana=False)
            phonemes = [p for p in g2p_result.split() if p]
            if phonemes:
                return phonemes
        except Exception as e:
            logger.debug(f"[AI] pyopenjtalk g2p failed for '{text}': {e}")
        
        logger.debug(f"[AI] Word '{text}' using fallback decomposition.")
        return list(text)

    # ============================================================
    # 非同期推論インターフェース
    # ============================================================

    def analyze_async(self, input_context) -> None:
        """
        GUIスレッドをフリーズさせずに、バックグラウンドでVITS歌唱推論を実行する。
        現行のGUI側（MainWindow等）の呼び出し互換を維持しつつ、中身は完全にクリーンにしています。
        """
        def task():
            try:
                # 1. 入力コンテキストの安全な解析
                if isinstance(input_context, dict) and "text" in input_context:
                    text_input = input_context["text"]
                    # 歌詞を音素に変換 (VITSへの入力用)
                    phonemes = self.text_to_phonemes(text_input)
                    logger.info(f"[AI Task] Target text: '{text_input}' -> Phonemes: {phonemes}")
                else:
                    logger.warning("[AI Task] Received old wave-style input context. Fallback triggered.")
                    phonemes = ["a"]

                # 2. VITS / BigVGAN 推論実行への橋渡し
                # 現段階ではバックエンド未実装によるフリーズ・クラッシュを防ぐため、
                # GUIが次に受け取るべき安全なプレースホルダーデータ、またはゼロ波形オブジェクトを返します。
                # 既存のGUIのシグナル受け取り側のクラッシュを防ぐため、安全なデフォルト値を返送。
                
                # 【将来の完全実装フェーズへの拡張コード】
                # waveforms = self.predict_vits_waveform(phonemes, input_context.get("f0_curve"))
                # self.finished.emit(waveforms)
                
                # 現行GUIの型互換性を担保するためのダミーリスト（無言クラッシュ防止）
                fallback_results = [{
                    "onset": 0.0,
                    "overlap": 0.05,
                    "pre_utterance": 0.1
                }]
                
                self.finished.emit(fallback_results)

            except Exception as e:
                error_msg = f"AI Inference Task Error: {e}"
                logger.error(error_msg)
                self.error.emit(error_msg)

        # ワーカースレッドへタスクを安全に投入
        try:
            self.executor.submit(task)
        except Exception as e:
            logger.critical(f"[AI] Failed to submit task to executor: {e}")
            self.error.emit(f"Thread Submission Error: {e}")

    # ============================================================
    # 将来用: VITS / BigVGAN 波形生成コア（Pro / Ultra 専用）
    # ============================================================

    def predict_vits_waveform(self, phonemes: list, f0_curve: np.ndarray) -> np.ndarray:
        """
        【フェーズ3以降でガチガチに実装するVITS推論器のスタブ】
        音素配列とピッチカーブ（F0）からダイレクトに音声を生成する。
        
        - VO-SE Pro: VITS標準の高速ニューラルボコーダーで波形化
        - VO-SE Ultra: ボコーダー部を最高峰の『BigVGAN』に差し替えて超高音質化
        """
        # 現段階では安全に無音のNumPy配列を返す（クラッシュガード）
        logger.info(f"[AI Core] VITS Waveform generation requested for {len(phonemes)} phonemes.")
        return np.zeros(1024, dtype=np.float32)

    # ============================================================
    # 終了処理
    # ============================================================

    def shutdown(self) -> None:
        """アプリ終了時、バックグラウンドスレッドを即座かつ安全に破棄する"""
        try:
            self.executor.shutdown(wait=False)
            logger.info("[AI] AIManager executor safely shut down.")
        except Exception as e:
            logger.error(f"Error during AIManager shutdown: {e}")
