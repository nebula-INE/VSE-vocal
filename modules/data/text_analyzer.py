# modules/data/text_analyzer.py

import os
import sys
from typing import List, Optional, Dict, Any, Tuple
import pyopenjtalk
from modules.data.data_models import NoteEvent
from modules.data.oto_parser import OtoParser  # 🚀 新規追加したパーサーをインポート

class TextAnalyzer:
    # 日本語の標準母音
    STANDARD_VOWELS = {"a", "i", "u", "e", "o"}
    # Open JTalkが生成する無声化母音（省略・ささやき化される母音）
    VOICELESS_VOWELS = {"A", "I", "U", "E", "O"}
    # 全母音の結合セット
    ALL_VOWELS = STANDARD_VOWELS | VOICELESS_VOWELS
    
    # 特殊音素: 'N'（ん/撥音）、'cl'（っ/促音）
    SPECIAL_PHONEMES = {"N", "cl"}

    def __init__(self, dict_path: Optional[str] = None):
        """
        VO-SE 究極版歌唱合成用タイムライン・アナライザー (Oto.ini連動モデル)
        """
        self.dict_path: str = dict_path if dict_path is not None else ""
        
        if self.dict_path and os.path.exists(self.dict_path):
            set_dic_path = getattr(pyopenjtalk, "set_dic_path", None)
            if callable(set_dic_path):
                set_dic_path(self.dict_path)

    def _lyric_to_phonemes(self, lyric: str) -> List[str]:
        """歌詞から余計な記号を徹底排除し、純粋な発音用音素配列を抽出"""
        if not lyric or not lyric.strip():
            return ["pau"]
            
        try:
            raw_features = pyopenjtalk.g2p(lyric, kana=False)
            if raw_features:
                return [p for p in raw_features.split() if p not in ("sil", "pau")]
        except Exception as e:
            print(f"[Warning] G2P conversion failed for '{lyric}': {e}", file=sys.stderr)
            
        return ["pau"]

    def align_vocal_timing(
        self, 
        note_events: List[NoteEvent], 
        oto_parser: Optional[OtoParser] = None,  # 🚀 OtoParserをオプションで受け取る
        frame_period_ms: float = 5.0
    ) -> Tuple[List[NoteEvent], List[Dict[str, Any]]]:
        """
        【フェーズ3：完全版】
        Oto.ini（原音設定）の「先行発声」「オーバーラップ」を厳密に反映し、
        5msフレーム単位の連続歌唱タイムライン配列を動的に構築する。
        """
        if not note_events:
            return [], []

        # Oto.ini が読み込まれていない場合のセーフティ・フォールバック定数（秒単位）
        DEFAULT_CONSONANT_DURATION = 0.05
        DEFAULT_SPECIAL_DURATION = 0.08
        DEFAULT_OVERLAP = 0.02
        DEFAULT_PREUTTERANCE = 0.05
        
        RELEASE_DURATION = 0.03  # ノート終了後の残響フェードアウト（30ms）
        frame_period_sec = frame_period_ms / 1000.0

        global_frame_timeline: List[Dict[str, Any]] = []

        for i, note in enumerate(note_events):
            if note is None:
                continue

            # 1. 歌詞を音素配列に分解
            phonemes = self._lyric_to_phonemes(note.lyric)
            note.phonemes = phonemes

            # 休符ノートの処理
            if not phonemes or "pau" in phonemes:
                note.has_analysis = True
                continue

            # 2. 音素の分類
            consonants = [p for p in phonemes if p not in self.ALL_VOWELS and p not in self.SPECIAL_PHONEMES]
            vowels = [p for p in phonemes if p in self.ALL_VOWELS]
            specials = [p for p in phonemes if p in self.SPECIAL_PHONEMES]

            has_voiceless = any(v in self.VOICELESS_VOWELS for v in vowels)

            # 3. 🚀 【新機軸】Oto.ini（原音設定）からミリ秒精度のタイミング属性を抽出
            preutterance = DEFAULT_PREUTTERANCE
            overlap = DEFAULT_OVERLAP
            
            if oto_parser is not None:
                # 歌詞（例：「さ」）または最初の音素で原音設定を検索
                record = oto_parser.find_record(note.lyric)
                if not record and phonemes:
                    record = oto_parser.find_record(phonemes[0])
                
                # レコードが存在すれば、固定定数を破棄し、人間の手で設定された原音設定値で上書き
                if record is not None:
                    preutterance = record.preutterance
                    overlap = record.overlap

            # 4. タイミング・スライス計算（先行発声位置の確定）
            # ノート開始（note.start_time）を基準に、Oto.iniの指定通りに子音のフライング開始点を決定
            vocal_start_time = note.start_time - preutterance
            if vocal_start_time < 0:
                vocal_start_time = 0.0

            # 前のノートの母音とクロスフェードする位置（Overlap）の算出
            overlap_start_time = note.start_time - overlap
            if overlap_start_time < 0:
                overlap_start_time = 0.0

            note_end_time = note.start_time + note.duration
            vocal_end_time = note_end_time + RELEASE_DURATION

            # 後続ノートとの重複カット
            if i + 1 < len(note_events) and note_events[i + 1] is not None:
                next_note = note_events[i + 1]
                if next_note.start_time <= note_end_time:
                    vocal_end_time = next_note.start_time

            # 5. フレーム単位（5ms）の高精度離散レンダリング
            current_time = vocal_start_time
            while current_time < vocal_end_time:
                current_phoneme = "pau"
                weight = 1.0

                if current_time < note.start_time:
                    # --------------------------------------------------
                    # [A: 子音先行発声（Preutterance）区間]
                    # --------------------------------------------------
                    # 子音のタイムスライス
                    if consonants:
                        c_idx = int((current_time - vocal_start_time) / max(0.01, (preutterance / len(consonants))))
                        current_phoneme = consonants[min(c_idx, len(consonants) - 1)]
                    else:
                        current_phoneme = "pau"

                    # 🚀 Oto.iniの「Overlap（重なり）」に基づく滑らかなクロスフェード制御
                    # 前のノートからこのノートの子音、あるいは母音へ滑らかにバトンを渡す
                    if current_time >= overlap_start_time and overlap > 0:
                        weight = (current_time - overlap_start_time) / overlap
                
                elif current_time >= note.start_time and current_time < note_end_time:
                    # --------------------------------------------------
                    # [B: ノート主発声区間 (母音 / 撥音 / 促音)]
                    # --------------------------------------------------
                    main_duration = note.duration
                    
                    if specials and not vowels:
                        sp_idx = int((current_time - note.start_time) / (main_duration / len(specials)))
                        current_phoneme = specials[min(sp_idx, len(specials) - 1)]
                    else:
                        v_sp_sequence = vowels + specials
                        total_elements = len(v_sp_sequence)
                        
                        element_duration = main_duration / max(1, total_elements)
                        seq_idx = int((current_time - note.start_time) / element_duration)
                        seq_idx = min(seq_idx, total_elements - 1)
                        
                        current_phoneme = v_sp_sequence[seq_idx]

                        # 省略（無声化母音）の処理
                        if current_phoneme in self.VOICELESS_VOWELS and has_voiceless:
                            if consonants:
                                current_phoneme = consonants[-1]
                            else:
                                current_phoneme = "cl"
                else:
                    # --------------------------------------------------
                    # [C: リリース区間]
                    # --------------------------------------------------
                    if vowels:
                        current_phoneme = vowels[-1]
                    elif specials:
                        current_phoneme = specials[-1]
                    elif consonants:
                        current_phoneme = consonants[-1]

                    release_elapsed = current_time - note_end_time
                    weight = 1.0 - (release_elapsed / RELEASE_DURATION)

                # タイムラインへプッシュ
                global_frame_timeline.append({
                    "time": current_time,
                    "phoneme": current_phoneme,
                    "weight": max(0.0, min(1.0, weight))
                })

                current_time += frame_period_sec

            note.has_analysis = True

        return note_events, global_frame_timeline
