# modules/audio/vo_se_engine_patch.py
"""
VO-SE Vocal — vo_se_engine.py への差分パッチ

このファイルは vo_se_engine.py の VO_SE_Engine クラスに対して
モンキーパッチを当てる形で優先度1〜2の機能を追加する。

本番運用では vo_se_engine.py 本体にマージすること。

追加・修正メソッド:
  [NEW-1] VO_SE_Engine.refresh_voice_library_v2()
          → VcvResolver を初期化し、音源ロード時に VCV 対応フラグをセットする
  [NEW-2] VO_SE_Engine.export_to_wav_v2()
          → VcvResolver を通じた VCV 解決 + UST vibrato カーブの注入
  [NEW-3] VO_SE_Engine._build_vibrato_curves()
          → UstNote の VBR パラメーターから depth/rate カーブを生成
  [NEW-4] VO_SE_Engine.load_ust_project()
          → UST ファイルを UstParser で読み込み、notes_list に変換して返す
"""
from __future__ import annotations

import math
import os
import ctypes
import logging
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

from modules.data.oto_parser import OtoParser
from modules.audio.vcv_resolver import VcvResolver
from modules.data.ust_parser import UstParser, UstConverter, UstVibratoParams

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# ビブラートカーブビルダー (スタンドアロン関数)
# ---------------------------------------------------------------------------

def build_vibrato_curves(
    duration_sec: float,
    vibrato_params: Optional[UstVibratoParams],
    resolution: int = 128,
    note_start_offset_sec: float = 0.0,
) -> Tuple[np.ndarray, np.ndarray]:
    """
    UstVibratoParams から depth/rate カーブを生成する。

    Args:
        duration_sec:         ノートの長さ (秒)
        vibrato_params:       UST VBR パラメーター (None なら全ゼロ)
        resolution:           サンプル数
        note_start_offset_sec: 先行発声分のオフセット (ビブラートはノート開始後にかける)

    Returns:
        (vibrato_depth_curve, vibrato_rate_curve) それぞれ shape=(resolution,) の float64 配列
    """
    depth_curve = np.zeros(resolution, dtype=np.float64)
    rate_curve  = np.zeros(resolution, dtype=np.float64)

    if vibrato_params is None or vibrato_params.length <= 0:
        return depth_curve, rate_curve

    times = np.linspace(0.0, duration_sec + note_start_offset_sec, resolution)

    # ビブラート開始時刻: ノート開始から length% 後
    vib_start = note_start_offset_sec + duration_sec * (1.0 - vibrato_params.length / 100.0)
    vib_end   = note_start_offset_sec + duration_sec

    for idx, t in enumerate(times):
        if t < vib_start or t >= vib_end:
            continue

        vib_elapsed = t - vib_start
        vib_total   = vib_end - vib_start

        # フェードイン / フェードアウトの包絡
        fade_in_sec  = vib_total * vibrato_params.fade_in  / 100.0
        fade_out_sec = vib_total * vibrato_params.fade_out / 100.0

        if vib_elapsed < fade_in_sec and fade_in_sec > 0:
            env = vib_elapsed / fade_in_sec
        elif vib_elapsed > vib_total - fade_out_sec and fade_out_sec > 0:
            env = (vib_total - vib_elapsed) / fade_out_sec
        else:
            env = 1.0

        depth_curve[idx] = vibrato_params.depth_semitones * env
        rate_curve[idx]  = vibrato_params.rate_hz

    return depth_curve, rate_curve


# ---------------------------------------------------------------------------
# VO_SE_Engine への追加メソッド群
# ---------------------------------------------------------------------------

def _refresh_voice_library_v2(self) -> None:
    """
    [NEW-1] VcvResolver を再初期化しながら音源フォルダを再スキャンする。
    既存の refresh_voice_library() を置き換えるか、その後に呼び出す。
    """
    # 既存の oto_map をリセット
    self.oto_map = {}

    if not os.path.exists(self.voice_lib_path):
        os.makedirs(self.voice_lib_path, exist_ok=True)
        self.vcv_resolver = None
        return

    # oto_parser がなければ生成
    if not hasattr(self, "oto_parser") or self.oto_parser is None:
        self.oto_parser = OtoParser()

    self.oto_parser.clear()

    for root, _dirs, files in os.walk(self.voice_lib_path):
        files_lower = [f.lower() for f in files]
        if "oto.ini" in files_lower:
            real_name = files[files_lower.index("oto.ini")]
            ini_path  = os.path.join(root, real_name)
            loaded = self.oto_parser.load_oto_file(ini_path)
            logger.debug("oto.ini ロード: %d エントリ (%s)", loaded, ini_path)

        for fname in files:
            if fname.lower().endswith(".wav"):
                lyric = os.path.splitext(fname)[0]
                self.oto_map[lyric] = os.path.abspath(os.path.join(root, fname))

    # VcvResolver を再初期化
    self.vcv_resolver = VcvResolver(self.oto_parser, use_g2p=True)
    logger.info(
        "音源ライブラリ更新: %d WAV / VCV=%s",
        len(self.oto_map),
        self.oto_parser.has_vcv(),
    )


def _export_to_wav_v2(self, notes, parameters, file_path) -> None:
    """
    [NEW-2] VCV 解決 + UST ビブラートカーブ注入に対応した export_to_wav。

    旧 export_to_wav() との差分:
      - resolve_target_wav() を廃止し VcvResolver を使う
      - vibrato_depth_curve / vibrato_rate_curve を NoteEvent の値から生成する
      - UST _ust_vibrato 拡張フィールドが存在する場合はそちらを優先する
    """
    if not self.lib:
        raise RuntimeError("Engine Core library missing!")

    # text_analyzer で先行発声・VCV タイムラインを整合
    oto_parser = getattr(self, "oto_parser", None)
    notes, timeline = self.text_analyzer.align_vocal_timing(notes, oto_parser)

    # C++ へタイムライン転送
    if timeline and hasattr(self, "pipeline_bridge") and self.pipeline_bridge:
        self.pipeline_bridge.send_timeline_to_core(timeline)

    note_count = len(notes)
    from modules.audio.vo_se_engine import CNoteEvent  # 本体の構造体を再利用
    c_notes_array = (CNoteEvent * note_count)()
    self._temp_refs = []

    for i, note in enumerate(notes):
        # WAV パスの解決
        vcv_resolver = getattr(self, "vcv_resolver", None)
        wav_path = ""

        if vcv_resolver is not None:
            prev_lyric = notes[i - 1].lyric if i > 0 else None
            _alias, oto_entry = vcv_resolver.resolve_note(note.lyric, prev_lyric)
            if oto_entry is not None:
                wav_path = oto_entry.wav_path

        if not wav_path or not os.path.exists(wav_path):
            # フォールバック: lyric 直接マッチ
            wav_path = self.oto_map.get(note.lyric) or self.oto_map.get(
                getattr(note, "phonemes", ""), ""
            )
            if not wav_path:
                wav_path = next(iter(self.oto_map.values()), "")

        res = 128

        # パラメーターカーブ
        p_curve = self._get_sampled_curve(parameters["Pitch"],   note, res, is_pitch=True).astype(np.float64)
        g_curve = self._get_sampled_curve(parameters["Gender"],  note, res).astype(np.float64)
        t_curve = self._get_sampled_curve(parameters["Tension"], note, res).astype(np.float64)
        b_curve = self._get_sampled_curve(parameters["Breath"],  note, res).astype(np.float64)

        # ビブラートカーブ: UST VBR > NoteEvent の固定値 > ゼロ
        ust_vib_dict = getattr(note, "_ust_vibrato", None)
        ust_vib: Optional[UstVibratoParams] = None
        if isinstance(ust_vib_dict, dict):
            try:
                ust_vib = UstVibratoParams(**ust_vib_dict)
            except Exception:
                pass

        if ust_vib is not None:
            # UST ビブラートパラメーターから生成
            preutterance_sec = float(getattr(note, "pre_utterance", 0.0)) / 1000.0
            vib_depth, vib_rate = build_vibrato_curves(
                duration_sec          = float(note.duration),
                vibrato_params        = ust_vib,
                resolution            = res,
                note_start_offset_sec = preutterance_sec,
            )
        elif float(getattr(note, "vibrato_depth", 0.0)) > 0:
            # NoteEvent の固定値から正弦波カーブを生成
            depth = float(note.vibrato_depth)
            rate  = float(getattr(note, "vibrato_rate", 5.5))
            times = np.linspace(0.0, float(note.duration), res)
            vib_depth = (np.sin(2 * math.pi * rate * times) * depth).astype(np.float64)
            vib_rate  = np.full(res, rate, dtype=np.float64)
        else:
            vib_depth = np.zeros(res, dtype=np.float64)
            vib_rate  = np.zeros(res, dtype=np.float64)

        self._temp_refs.extend([p_curve, g_curve, t_curve, b_curve, vib_depth, vib_rate])

        c_notes_array[i].wav_path = wav_path.encode("utf-8") if wav_path else b""
        c_notes_array[i].pitch_curve            = p_curve.ctypes.data_as(ctypes.POINTER(ctypes.c_double))
        c_notes_array[i].gender_curve           = g_curve.ctypes.data_as(ctypes.POINTER(ctypes.c_double))
        c_notes_array[i].tension_curve          = t_curve.ctypes.data_as(ctypes.POINTER(ctypes.c_double))
        c_notes_array[i].breath_curve           = b_curve.ctypes.data_as(ctypes.POINTER(ctypes.c_double))
        c_notes_array[i].vibrato_depth_curve    = vib_depth.ctypes.data_as(ctypes.POINTER(ctypes.c_double))
        c_notes_array[i].vibrato_rate_curve     = vib_rate.ctypes.data_as(ctypes.POINTER(ctypes.c_double))
        c_notes_array[i].pitch_length           = res
        c_notes_array[i].vibrato_curve_length   = res

    try:
        self.lib.execute_render(
            c_notes_array,
            note_count,
            os.path.abspath(file_path).encode("utf-8"),
            0,
        )
    finally:
        self._temp_refs = []


def _load_ust_project(self, ust_path: str) -> List[Dict[str, Any]]:
    """
    [NEW-4] UST ファイルをネイティブパーサーで読み込み、
    NoteEvent 互換辞書リストを返す。

    Args:
        ust_path: .ust ファイルのパス

    Returns:
        NoteEvent.from_dict() で復元可能な辞書のリスト
    """
    parser = UstParser()
    project = parser.load(ust_path)
    note_dicts = UstConverter.to_note_dicts(project)
    logger.info(
        "UST ロード完了: %d ノート / Tempo=%.1f (%s)",
        len(note_dicts),
        project.tempo,
        os.path.basename(ust_path),
    )
    return note_dicts


# ---------------------------------------------------------------------------
# パッチ適用関数
# ---------------------------------------------------------------------------

def apply_patch(engine_class) -> None:
    """
    VO_SE_Engine クラスに新メソッドをバインドする。

    呼び出し例 (vo_se_engine.py の末尾 or app_main.py):
        from modules.audio.vo_se_engine_patch import apply_patch
        from modules.audio.vo_se_engine import VO_SE_Engine
        apply_patch(VO_SE_Engine)
    """
    engine_class.refresh_voice_library_v2 = _refresh_voice_library_v2
    engine_class.export_to_wav_v2         = _export_to_wav_v2
    engine_class.load_ust_project         = _load_ust_project
    logger.info("VO_SE_Engine パッチ適用完了 (VCV + UST + Vibrato)")
