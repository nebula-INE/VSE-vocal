# modules/data/oto_parser.py
"""
VO-SE Vocal — oto.ini 完全パーサー

変更点:
  [NEW-1] OtoEntry dataclass: 先行発声・オーバーラップ・子音固定範囲・左右ブランクを全フィールドとして保持
  [NEW-2] OtoParser.load_oto_file(): Shift-JIS/UTF-8 自動判別、サブフォルダ再帰対応
  [NEW-3] OtoParser.get(): alias の完全一致 → 末尾母音一致フォールバック
  [NEW-4] OtoParser.get_preutterance_sec() / get_overlap_sec(): ms → sec 変換ショートカット
  [NEW-5] OtoParser.resolve_alias(): VCV ("a い") → CV ("い") への段階的フォールバック
"""
from __future__ import annotations

import os
import logging
from dataclasses import dataclass
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)


@dataclass
class OtoEntry:
    """oto.ini の 1 エントリを表すデータクラス。単位はすべてミリ秒 (ms)。"""
    alias: str              # エイリアス名 (例: "a い", "- い", "い")
    filename: str           # 対応 WAV ファイル名
    voice_dir: str          # この oto.ini が置かれているフォルダの絶対パス

    left_blank: float       # 左ブランク (ms)  : WAV 先頭からの読み飛ばし量
    fixed_range: float      # 子音固定範囲 (ms) : ストレッチされない先頭部分
    right_blank: float      # 右ブランク (ms)  : WAV 末尾からの読み飛ばし量（負値可）
    preutterance: float     # 先行発声 (ms)    : ノート開始時刻より「先」に発声を始める量
    overlap: float          # オーバーラップ (ms): 前のノートとフェードでクロスする量

    @property
    def wav_path(self) -> str:
        """フルパスで WAV へのパスを返す"""
        return os.path.join(self.voice_dir, self.filename)

    @property
    def preutterance_sec(self) -> float:
        """先行発声を秒単位で返す"""
        return self.preutterance / 1000.0

    @property
    def overlap_sec(self) -> float:
        """オーバーラップを秒単位で返す"""
        return self.overlap / 1000.0

    @property
    def fixed_range_sec(self) -> float:
        """子音固定範囲を秒単位で返す"""
        return self.fixed_range / 1000.0

    @property
    def left_blank_sec(self) -> float:
        """左ブランクを秒単位で返す"""
        return self.left_blank / 1000.0


class OtoParser:
    """
    oto.ini をロード・検索する統合パーサー。

    使い方:
        parser = OtoParser()
        parser.load_oto_file("/path/to/voice/oto.ini")
        entry = parser.get("a い")   # OtoEntry or None
    """

    def __init__(self) -> None:
        # alias → OtoEntry の辞書（複数 oto.ini をマージして保持）
        self._db: Dict[str, OtoEntry] = {}

    # ------------------------------------------------------------------
    # 公開 API
    # ------------------------------------------------------------------

    def load_oto_file(self, ini_path: str) -> int:
        """
        oto.ini を 1 ファイル読み込んでデータベースに追加する。

        Returns:
            追加されたエントリ数
        """
        if not os.path.isfile(ini_path):
            logger.warning("oto.ini が見つかりません: %s", ini_path)
            return 0

        voice_dir = os.path.dirname(os.path.abspath(ini_path))
        content = self._read_safe(ini_path)
        count = 0

        for raw_line in content.splitlines():
            line = raw_line.strip()
            if not line or "=" not in line:
                continue
            entry = self._parse_line(line, voice_dir)
            if entry is not None:
                self._db[entry.alias] = entry
                count += 1

        logger.debug("oto.ini ロード完了 (%d エントリ): %s", count, ini_path)
        return count

    def load_voice_dir(self, voice_dir: str) -> int:
        """
        指定フォルダ（サブフォルダ含む）の oto.ini を全部ロードする。

        Returns:
            合計エントリ数
        """
        total = 0
        for root, _dirs, files in os.walk(voice_dir):
            for fname in files:
                if fname.lower() == "oto.ini":
                    total += self.load_oto_file(os.path.join(root, fname))
        return total

    def get(self, alias: str) -> Optional[OtoEntry]:
        """
        alias で完全一致検索。見つからなければ None。
        """
        return self._db.get(alias)

    def resolve_alias(self, lyric: str, prev_vowel: Optional[str]) -> Optional[OtoEntry]:
        """
        VCV → CV → 単独音 の優先順でエントリを解決する。

        Args:
            lyric:       対象の歌詞 (例: "い")
            prev_vowel:  前ノートの末尾母音ラベル ("a"/"i"/"u"/"e"/"o"/"n"/"") or None

        Returns:
            最初に見つかった OtoEntry、全て失敗なら None
        """
        candidates: List[str] = []

        # 1. VCV: "a い"
        if prev_vowel:
            candidates.append(f"{prev_vowel} {lyric}")

        # 2. CV with silence: "- い"
        candidates.append(f"- {lyric}")

        # 3. 単独音: "い"
        candidates.append(lyric)

        for alias in candidates:
            entry = self._db.get(alias)
            if entry is not None:
                return entry

        # 4. 歌詞のみ部分一致（末尾が一致するもの）
        for alias, entry in self._db.items():
            if alias.endswith(f" {lyric}") or alias == lyric:
                return entry

        return None

    def get_preutterance_sec(self, alias: str, default: float = 0.05) -> float:
        """先行発声を秒で返す。エントリが無ければ default。"""
        entry = self.get(alias)
        return entry.preutterance_sec if entry else default

    def get_overlap_sec(self, alias: str, default: float = 0.02) -> float:
        """オーバーラップを秒で返す。エントリが無ければ default。"""
        entry = self.get(alias)
        return entry.overlap_sec if entry else default

    def all_aliases(self) -> List[str]:
        """ロード済み全エイリアスのリストを返す"""
        return list(self._db.keys())

    def has_vcv(self) -> bool:
        """VCV エイリアス (スペース含む) が 1 つ以上あれば True"""
        return any(" " in alias for alias in self._db)

    def clear(self) -> None:
        """ロード済みデータをリセット"""
        self._db.clear()

    # ------------------------------------------------------------------
    # 内部ユーティリティ
    # ------------------------------------------------------------------

    @staticmethod
    def _read_safe(path: str) -> str:
        """Shift-JIS / UTF-8 / latin-1 の順で試みて文字列を返す"""
        for enc in ("cp932", "utf-8-sig", "utf-8", "latin-1"):
            try:
                with open(path, "r", encoding=enc, errors="strict") as f:
                    return f.read()
            except (UnicodeDecodeError, LookupError):
                continue
        # 最終フォールバック
        with open(path, "r", encoding="cp932", errors="ignore") as f:
            return f.read()

    @staticmethod
    def _parse_line(line: str, voice_dir: str) -> Optional[OtoEntry]:
        """
        1 行をパースして OtoEntry を返す。

        oto.ini 行フォーマット:
            filename.wav=alias,left_blank,fixed_range,right_blank,preutterance,overlap
        """
        try:
            filename_part, params_part = line.split("=", 1)
            filename_part = filename_part.strip()
            parts = [p.strip() for p in params_part.split(",")]

            # alias が空の場合は拡張子なしファイル名を使う
            alias = parts[0] if parts[0] else os.path.splitext(filename_part)[0]

            def _f(idx: int, fallback: float = 0.0) -> float:
                try:
                    return float(parts[idx]) if idx < len(parts) and parts[idx] != "" else fallback
                except ValueError:
                    return fallback

            return OtoEntry(
                alias=alias,
                filename=filename_part,
                voice_dir=voice_dir,
                left_blank=_f(1),
                fixed_range=_f(2),
                right_blank=_f(3),
                preutterance=_f(4),
                overlap=_f(5),
            )
        except Exception as exc:
            logger.debug("oto.ini 行のパース失敗 (%s): %s", exc, line)
            return None
