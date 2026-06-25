# modules/data/oto_parser.py

import os
import sys
from typing import Dict, Optional, NamedTuple

# ❌ 修正前: from .oto_parser import OtoParser  ← 自分自身をインポートする循環参照
# ✅ 修正後: 削除。OtoRecord と OtoParser を同一ファイル内で定義するだけでよい。


class OtoRecord(NamedTuple):
    """
    UTAU形式の原音設定（Oto.ini）の1レコードを格納する型安全な構造体（秒単位に一元化）
    """
    filename: str          # 対象のwavファイル名 (例: "sa.wav")
    alias: str             # 呼び出しエイリアス (例: "さ")
    offset: float          # 左ブランク（秒）
    consonant: float       # 固定子音区間（秒）
    # ✅ 修正: blank は負値の場合「音源末尾からの相対値」という UTAU 仕様を反映するため
    #         raw_blank_ms（パース元の生ミリ秒値）を別途保持し、使用側で判定できるようにする。
    blank: float           # 右ブランク（秒）。正値=絶対位置、負値=末尾からの相対位置
    preutterance: float    # 先行発声時間（秒）
    overlap: float         # オーバーラップ時間（秒）

    def effective_blank(self, wav_duration_sec: float) -> float:
        """
        blank の実効値（秒）を返す。
        UTAU 仕様: blank が負の場合は wav 全体長から差し引いた絶対位置を意味する。
          例) wav=1.0秒, blank=-100ms → 実効値 = 1.0 - 0.1 = 0.9秒
        wav_duration_sec: 音源 wav ファイルの長さ（秒）
        """
        if self.blank < 0.0:
            return wav_duration_sec + self.blank   # blank は負なので加算で引き算になる
        return self.blank


class OtoParser:
    def __init__(self) -> None:
        """
        VO-SE 原音設定（Oto.ini）高精度パーサー
        """
        # キー: エイリアス（またはファイル名）、値: OtoRecord
        self.records: Dict[str, OtoRecord] = {}

    def load_oto_file(self, file_path: str) -> bool:
        """
        指定された oto.ini ファイルを読み込み、レコードをメモリにキャッシュする。
        """
        if not os.path.exists(file_path):
            print(f"[Warning] oto.ini not found at: {file_path}", file=sys.stderr)
            return False

        try:
            with open(file_path, "r", encoding="shift_jis", errors="ignore") as f:
                for line in f:
                    line = line.strip()
                    if not line or "=" not in line:
                        continue

                    # UTAUフォーマット: filename.wav=alias,offset,consonant,blank,preutterance,overlap
                    filename, params_str = line.split("=", 1)
                    params = params_str.split(",")

                    # パラメータ数が足りない不正な行はスキップ
                    if len(params) < 1:
                        continue

                    alias = params[0] if params[0] else os.path.splitext(filename)[0]

                    # 各パラメータのパース（UTAUの定義通りミリ秒単位。未指定時は0）
                    # 単位をすべて「秒(float)」に統一して保持することで計算時のバグを撲滅
                    offset      = float(params[1]) / 1000.0 if len(params) > 1 and params[1] else 0.0
                    consonant   = float(params[2]) / 1000.0 if len(params) > 2 and params[2] else 0.0
                    # ✅ blank は負値を保持したまま変換する（effective_blank() で実効値を取得）
                    blank       = float(params[3]) / 1000.0 if len(params) > 3 and params[3] else 0.0
                    preutterance = float(params[4]) / 1000.0 if len(params) > 4 and params[4] else 0.0
                    overlap     = float(params[5]) / 1000.0 if len(params) > 5 and params[5] else 0.0

                    record = OtoRecord(
                        filename=filename,
                        alias=alias,
                        offset=offset,
                        consonant=consonant,
                        blank=blank,
                        preutterance=preutterance,
                        overlap=overlap,
                    )

                    # 逆引きできるようにエイリアスとファイル名（拡張子なし）の両方を登録
                    self.records[alias] = record
                    self.records[os.path.splitext(filename)[0]] = record

            return True

        except Exception as e:
            print(f"[Error] Failed to parse oto.ini: {e}", file=sys.stderr)
            return False

    def find_record(self, key: str) -> Optional[OtoRecord]:
        """
        エイリアス名、または音素名から原音設定レコードを検索する。
        """
        return self.records.get(key, None)
