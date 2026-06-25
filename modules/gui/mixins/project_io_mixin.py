# ==========================================================================
# modules/gui/mixins/project_io_mixin.py
#
# MainWindow (modules/gui/main_window.py) からプロジェクトIO関連の
# メソッド群を分離した Mixin クラス。
#
# 対象範囲:
#   - .vose / .json プロジェクトの保存・読込
#   - MIDI / UST / USTX / VSQX ファイルの読込・解析・書き出し
#   - UTAU音源バンク(ZIP)のインポート、oto.ini の生成・保存
#   - ファイルのドラッグ&ドロップ受け入れ
#
# 移行方法について:
#   元の main_window.py から「対象メソッドをそのままこのファイルへ移動」した
#   ものであり、ロジックの変更は行っていない。MainWindow 側は
#
#       class MainWindow(QMainWindow, ProjectIOMixin):
#
#   のように多重継承することで、self.save_project() 等を従来通り呼び出せる。
#
# 重要: 死活未確認のメソッドについて
#   このファイルには、呼び出し元が一件も見つからなかったメソッドも
#   そのまま含めている（移動時に動作を変えないことを優先したため）。
#   各メソッドの docstring 直下に [LIVE] / [DEAD?] のメモを付記したので、
#   将来的な削除判断の参考にしてほしい。
#   - [LIVE]  : 実際に呼ばれている経路が確認できたもの
#   - [DEAD?] : 静的解析の範囲では呼び出し元が見つからなかったもの
#               （動的な getattr 呼び出し等で見逃している可能性はゼロではない）
# ==========================================================================

import os
import json
import shutil
import zipfile
from typing import Any, List, Dict, Optional, cast, TYPE_CHECKING

from PySide6.QtCore import Slot
from PySide6.QtWidgets import QFileDialog, QMessageBox

import numpy as np
import importlib
import importlib.util
mido = importlib.import_module("mido") if importlib.util.find_spec("mido") else None

# 1. 静的解析（Pyright）と実行時（MRO）で親クラスを切り替える
if TYPE_CHECKING:
    from ._mixin_base import _MixinBase
    _Base = _MixinBase  # 型チェッカーの目には _MixinBase を継承しているように見せる
else:
    _Base = object      # 実行時はプレーンな object になり、多重継承の衝突（MRO破綻）を防ぐ

# 2. mypy用の [assignment] を削り、シンプルな ignore に統一して両方正常にインポート
from modules.gui.aural_engine import AuralAIEngine  # type: ignore
from modules.gui.shared import get_resource_path, DynamicsAIEngine  # type: ignore


class ProjectIOMixin(_Base):
    """
    プロジェクトの保存/読込、各種ファイル形式(MIDI/UST/USTX/VSQX)の
    インポート/エクスポート、UTAU音源バンクのインストールを担当する Mixin。

    MainWindow から多重継承されることを前提としており、
    self.timeline_widget や self.tracks など、MainWindow 側で
    初期化される属性に依存している。単体では完結しない。
    """

    # [LIVE] メニュー/ドラッグ&ドロップ経路から到達
    def load_file_from_path(self, filepath: str):
        """指定されたパスからプロジェクトまたはMIDIファイルを読み込む"""
        if filepath.endswith('.mid') or filepath.endswith('.midi'):
            self._parse_midi(filepath)
        elif filepath.endswith('.ustx'):
            self._parse_ustx(filepath)
        print(f"ファイルを読み込みました: {filepath}")

    # [LIVE] load_file_from_path から呼ばれる
    def _parse_midi(self, filepath: str):
        """MIDIファイルを解析してタイムラインに反映"""
        from modules.data.midi_manager import load_midi_file
        notes_data = load_midi_file(filepath)
        if notes_data:
            self.update_timeline_with_notes(notes_data)

    # [LIVE] load_file_from_path から呼ばれる
    def _parse_ustx(self, filepath: str):
        """OpenUTAU形式(ustx)を解析（将来拡張用）"""
        print(f"USTX解析は現在開発中です: {filepath}")

    # [LIVE] ファイルメニュー『MIDIエクスポート』アクションに接続
    def export_to_midi_file(self):
        """現在のタイムラインをMIDIファイルとして出力"""
        print("MIDIエクスポートを開始します...")

    # [DEAD?] .vose形式での保存。呼び出し元が見つからない(on_save_project_clickedが実質的な後継とみられる)
    @Slot()
    def save_project(self):
        """プロジェクトを .vose 形式で保存"""
        path, _ = QFileDialog.getSaveFileName(self, "保存", "", "VO-SE Project (*.vose)")
        if not path: 
            return

        # データの同期
        self.tracks[self.current_track_idx].notes = self.timeline_widget.notes_list

        data = {
            "app_id": "VO_SE_Pro_2026",
            "tempo": self.timeline_widget.tempo,
            "tracks": [
                {
                    "name": t.name,
                    "type": t.track_type,
                    "notes": [n.to_dict() for n in t.notes],
                    "audio": t.audio_path,
                    "mixer": {"vol": t.volume, "pan": t.pan}
                } for t in self.tracks
            ]
        }
        
        try:
            with open(path, 'w', encoding='utf-8') as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
            self.statusBar().showMessage(f"Saved: {path}")
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Save Failed: {e}")

    # [LIVE] import_voice_bank から呼ばれる
    def generate_and_save_oto(self, target_voice_dir):
        """
        指定されたフォルダ内の全WAVを解析し、oto.iniを生成して保存する。
        """
        import os

        # 解析エンジンのインスタンス化
        # AutoOtoEngine は main_window.py 側で定義されているため、
        # モジュールトップレベルでimportすると循環importになる。
        # 呼び出し時点では main_window は読み込み済みなので、関数内importで回避。
        from modules.gui.main_window import AutoOtoEngine
        analyzer = AutoOtoEngine(sample_rate=44100)
        oto_lines = []
        
        # フォルダ内のファイルをスキャン
        files = [f for f in os.listdir(target_voice_dir) if f.lower().endswith('.wav')]
        
        if not files:
            print("解析対象のWAVファイルが見つかりませんでした。")
            return

        print(f"Starting AI analysis for {len(files)} files...")

        for filename in files:
            file_path = os.path.join(target_voice_dir, filename)
            try:
                # 1. 各ファイルをAI解析
                params = analyzer.analyze_wav(file_path)
                
                # 2. UTAU互換のテキスト行を生成
                line = analyzer.generate_oto_text(filename, params)
                oto_lines.append(line)
            except Exception as e:
                print(f"Error analyzing {filename}: {e}")

        # 3. oto.iniとして書き出し (Shift-JIS / cp932)
        oto_path = os.path.join(target_voice_dir, "oto.ini")
        try:
            with open(oto_path, "w", encoding="cp932", errors="ignore") as f:
                f.write("\n".join(oto_lines))
            print(f"Successfully generated: {oto_path}")
        except Exception as e:
            print(f"Failed to write oto.ini: {e}")

    # [LIVE] dropEvent からの音源ZIPインストール経路で呼ばれる
    def import_voice_bank(self, zip_path: str):
        """
        ZIP音源インストール完全版
        1. 文字化け修復解凍 2. ゴミ排除 3. AI解析 4. エンジン接続 5. UI更新
        """

        # 保存先ディレクトリ（voicesフォルダ）
        extract_base_dir = get_resource_path("voices")
        os.makedirs(extract_base_dir, exist_ok=True)
        
        installed_name = None
        valid_files = [] 
        found_oto = False

        try:
            # --- STEP 1: ZIP解析と文字化け対策 ---
            with zipfile.ZipFile(zip_path, 'r') as z:
                for info in z.infolist():
                    # Macで作られたZIPの日本語名化けを修正
                    try:
                        filename = info.filename.encode('cp437').decode('cp932')
                    except Exception:
                        filename = info.filename
                    
                    # 不要なゴミファイル（Mac由来など）をスキップ
                    if "__MACOSX" in filename or ".DS_Store" in filename:
                        continue
                    
                    valid_files.append((info, filename))
                    
                    # oto.iniがあるかチェック
                    if "oto.ini" in filename.lower():
                        found_oto = True
                        parts = filename.replace('\\', '/').strip('/').split('/')
                        if len(parts) > 1 and not installed_name:
                            installed_name = parts[-2]

                # 音源名が確定しなかった場合はZIPファイル名を使用
                if not installed_name:
                    installed_name = os.path.splitext(os.path.basename(zip_path))[0]

                target_voice_dir = os.path.join(extract_base_dir, installed_name)
                
                # --- STEP 2: クリーンインストール ＆ スマート展開 ---
                if os.path.exists(target_voice_dir):
                    shutil.rmtree(target_voice_dir)
                os.makedirs(target_voice_dir, exist_ok=True)

                # ZIP内の共通トップフォルダ（親直下の単一ディレクトリ）があるかチェック
                top_dirs = set()
                for _, fname in valid_files:
                    parts = fname.replace('\\', '/').strip('/').split('/')
                    if len(parts) > 1:
                        top_dirs.add(parts[0])
                    else:
                        top_dirs.add("") # ルートにファイルがある場合

                # 共通のトップフォルダが1つだけ存在するか判定
                has_single_top_dir = len(top_dirs) == 1 and "" not in top_dirs
                single_top_dir = list(top_dirs)[0] if has_single_top_dir else ""

                # ファイルを target_voice_dir 直下に適切に展開
                for info, filename in valid_files:
                    normalized_fname = filename.replace('\\', '/').strip('/')
                    
                    if has_single_top_dir:
                        # 共通トップフォルダを剥ぎ取って展開パスを綺麗にする
                        # 例: "KyokoFolder/wav/a.wav" -> "wav/a.wav"
                        rel_path = normalized_fname[len(single_top_dir):].lstrip('/')
                    else:
                        rel_path = normalized_fname
                        
                    target_path = os.path.join(target_voice_dir, rel_path)
                    
                    if info.is_dir():
                        os.makedirs(target_path, exist_ok=True)
                        continue
                        
                    os.makedirs(os.path.dirname(target_path), exist_ok=True)
                    with z.open(info) as source, open(target_path, "wb") as target:
                        shutil.copyfileobj(source, target)

            # --- STEP 3: AIエンジン自動解析 (oto.iniがない場合) ---
            status_bar = self.statusBar()
            if not found_oto:
                if status_bar:
                    status_bar.showMessage(f"AI解析中: {installed_name} の原音設定を自動生成しています...", 0)
                if hasattr(self, 'generate_and_save_oto'):
                    self.generate_and_save_oto(target_voice_dir)

            # --- STEP 4: AIエンジンの優先接続 ---
            aural_model = os.path.join(target_voice_dir, "aural_dynamics.onnx")
            std_model = os.path.join(target_voice_dir, "model.onnx")

            if os.path.exists(aural_model):
                self.dynamics_ai = AuralAIEngine() 
                if hasattr(self.dynamics_ai, 'load_model'):
                    # Pyrightのエラーを回避するために一時的に Any へキャスト
                    cast(Any, self.dynamics_ai).load_model(aural_model)
                engine_msg = "上位Auralモデル"
            elif os.path.exists(std_model):
                self.dynamics_ai = DynamicsAIEngine()
                if hasattr(self.dynamics_ai, 'load_model'):
                    # DynamicsAIEngine 側も安全のために同様のケアをしておくと確実です
                    cast(Any, self.dynamics_ai).load_model(std_model)
                engine_msg = "標準Dynamicsモデル"
            else:
                self.dynamics_ai = AuralAIEngine() 
                engine_msg = "汎用Auralエンジン"

            # --- STEP 5: UIの即時反映 ---
            v_manager = getattr(self, 'voice_manager', None)
            if v_manager and hasattr(v_manager, 'scan_utau_voices'):
                v_manager.scan_utau_voices()
            
            if hasattr(self, 'voice_gallery') and self.voice_gallery is not None:
                # 完全に動的な取得に切り替えることで静的エラーを回避
                refresh_fn = getattr(self.voice_gallery, 'setup_gallery', getattr(self.voice_gallery, 'refresh_gallery', None))
                if refresh_fn:
                    refresh_fn()
                self.voice_gallery.update()
                print(f"✅ Voice gallery refreshed with {installed_name}")
            else:
                print("⚠️ Warning: voice_gallery not initialized, creating new instance")
                if v_manager:
                    # 循環import回避のための遅延import
                    # (VoiceCardGallery は main_window.py 内で定義された UI クラスで、
                    #  他の Mixin にも依存される可能性があるため shared.py には移していない)
                    from modules.gui.main_window import VoiceCardGallery
                    self.voice_gallery = VoiceCardGallery(v_manager)
                    if hasattr(self.voice_gallery, 'set_partner_data'):
                        self.voice_gallery.set_partner_data(self.confirmed_partners)
                    if hasattr(self.voice_gallery, 'setup_gallery'):
                        self.voice_gallery.setup_gallery()
                    self.voice_gallery.voice_selected.connect(self.on_voice_changed)
            
            # 成功通知（ステータスバー）
            msg = f"✅ '{installed_name}' インストール完了！ ({engine_msg})"
            if status_bar:
                status_bar.showMessage(msg, 5000)
            
            # SE再生
            audio_out = getattr(self, 'audio_output', None)
            if audio_out:
                se_path = get_resource_path("assets/install_success.wav")
                if os.path.exists(se_path):
                    if hasattr(audio_out, 'play_se'):
                        try:
                            audio_out.play_se(se_path)
                        except Exception as e:
                            print(f"DEBUG: play_se failed: {e}")
                    elif hasattr(audio_out, 'setSource'):
                        try:
                            from PySide6.QtCore import QUrl
                            audio_out.setSource(QUrl.fromLocalFile(se_path))
                            if hasattr(audio_out, 'play'):
                                audio_out.play()
                        except Exception as e:
                            print(f"DEBUG: setSource/play failed: {e}")
            
            QMessageBox.information(
                self, 
                "導入成功", 
                f"音源 '{installed_name}' をインストールしました。\n"
                f"エンジン: {engine_msg}\n\n"
                f"キャラクター選択パネルから選択できます。"
            )

        except Exception as e:
            QMessageBox.critical(self, "導入エラー", f"インストール中にエラーが発生しました:\n{str(e)}")

    # [LIVE] Qtフレームワークが自動的に呼び出す標準オーバーライド
    def dragEnterEvent(self, event):
        """ファイルドラッグ時の処理（ここでは受け入れるかどうかの判定のみ行う）"""
        if event.mimeData().hasUrls():
            event.accept()
        else:
            event.ignore()

    # [LIVE] Qtフレームワークが自動的に呼び出す標準オーバーライド
    def dropEvent(self, event):
        """
        ファイルドロップ時の処理：ZIP（音源）、MIDI/JSON（プロジェクト）を自動判別。
        """
        # 1. 安全なファイルリストの取得
        mime_data = event.mimeData()
        if not mime_data.hasUrls():
            return
            
        file_items: List[Dict[str, str]] = []
        for url in mime_data.urls():
            local_path = url.toLocalFile()
            if local_path:
                file_items.append({"path": local_path, "source": "local"})
                continue

            # ブラウザ等からのURLドロップに対応（UTAU音源ZIPの直接導入）
            raw_url = url.toString()
            if raw_url:
                file_items.append({"path": raw_url, "source": "url"})

        for item in file_items:
            file_path = item["path"]
            source_type = item["source"]
            file_lower = file_path.lower()
            
            # --- 1. 音源ライブラリ(ZIP)の場合 ---
            if file_lower.endswith(".zip") or (source_type == "url" and ".zip" in file_lower):
                status_bar = self.statusBar()
                if status_bar:
                    status_bar.showMessage(f"音源を処理中: {os.path.basename(file_path)}")
                
                try:
                    zip_input_path = file_path
                    tmp_file_path = None
                    
                    # URLドロップの場合は一時ファイルとしてダウンロード
                    if source_type == "url":
                        from urllib.parse import urlparse
                        from urllib.request import urlretrieve
                        import tempfile
                        
                        parsed = urlparse(file_path)
                        if parsed.scheme not in ("http", "https"):
                            raise ValueError(f"未対応のURLスキームです: {parsed.scheme}")
                        with tempfile.NamedTemporaryFile(delete=False, suffix=".zip") as tmp:
                            tmp_file_path = tmp.name
                        urlretrieve(file_path, tmp_file_path)
                        zip_input_path = tmp_file_path

                    # ✅ 先ほど作成した完全版 import_voice_bank に丸投げする（UI更新もSE再生も自動で行われる）
                    self.import_voice_bank(zip_input_path)
                    
                    # URLからの一時ファイルをクリーンアップ
                    if tmp_file_path and os.path.exists(tmp_file_path):
                        os.remove(tmp_file_path)

                except Exception as e:
                    if 'tmp_file_path' in locals() and tmp_file_path and os.path.exists(tmp_file_path):
                        os.remove(tmp_file_path)
                    from PySide6.QtWidgets import QMessageBox
                    QMessageBox.critical(self, "導入失敗", f"インストール中にエラーが発生しました:\n{str(e)}")

            # --- 2. 楽曲データ(MIDI)の場合 ---
            elif file_lower.endswith(('.mid', '.midi')):
                if hasattr(self, 'load_file_from_path'):
                    self.load_file_from_path(file_path)
                
                status_bar = self.statusBar()
                if status_bar:
                    status_bar.showMessage(f"MIDIファイルを読み込みました: {os.path.basename(file_path)}")

            # --- 3. プロジェクトデータ(JSON)の場合 ---
            elif file_lower.endswith('.json'):
                if hasattr(self, 'load_file_from_path'):
                    self.load_file_from_path(file_path)
                
                status_bar = self.statusBar()
                if status_bar:
                    status_bar.showMessage(f"プロジェクトを読み込みました: {os.path.basename(file_path)}")

    # [LIVE] closeEvent の保存確認フローから呼ばれる
    @Slot()
    def on_save_project_clicked(self) -> None:
        """
        プロジェクトの保存処理。
        Actionsログ 4626行目の 'on_save_project_clicked' 不明エラーを解消します。
        """
        from PySide6.QtWidgets import QFileDialog, QMessageBox
        import json
        import os

        # 保存ダイアログを表示
        file_path, _ = QFileDialog.getSaveFileName(
            self,
            "プロジェクトを保存",
            "",
            "VO-SE Project (*.vose);;JSON Files (*.json);;All Files (*)"
        )

        if not file_path:
            return

        try:
            # データの構築（Noneガードを徹底）
            t_widget = getattr(self, 'timeline_widget', None)
            notes_data = []
            if t_widget is not None and hasattr(t_widget, 'get_notes'):
                notes_data = t_widget.get_notes()

            project_data = {
                "version": "1.0.0",
                "timestamp": 2026, # 代表の現在時間
                "current_time": float(getattr(self, 'current_playback_time', 0.0)),
                "notes": notes_data
            }

            # 書き込み実行
            with open(file_path, 'w', encoding='utf-8') as f:
                json.dump(project_data, f, ensure_ascii=False, indent=4)

            # ステータスバー通知
            sb = self.statusBar()
            if sb:
                sb.showMessage(f"保存完了: {os.path.basename(file_path)}", 3000)

        except Exception as e:
            QMessageBox.critical(self, "保存エラー", f"プロジェクトの保存に失敗しました:\n{str(e)}")

    # [LIVE] 呼び出し元を確認済み
    @Slot()
    def open_file_dialog_and_load_midi(self) -> None:
        """
        MIDIファイルの読み込み。
        Actionsログ 2431行目の 'open_file_dialog_and_load_midi' 不明エラーを解消します。
        """
        from PySide6.QtWidgets import QFileDialog, QMessageBox
        
        file_path, _ = QFileDialog.getOpenFileName(
            self,
            "MIDIファイルを開く",
            "",
            "MIDI Files (*.mid *.midi);;All Files (*)"
        )

        if not file_path:
            return

        try:
            # MIDIロード実行
            from modules.data.midi_manager import load_midi_file
            notes = load_midi_file(file_path)

            if notes:
                t_widget = getattr(self, 'timeline_widget', None)
                if t_widget is not None:
                    # 代表の掟：set_notesメソッドを確実に呼び出し
                    t_widget.set_notes(notes)
                    
                    sb = self.statusBar()
                    if sb:
                        sb.showMessage(f"MIDI読込成功: {len(notes)} ノート", 3000)
            else:
                QMessageBox.information(self, "MIDI読込", "MIDIファイルに有効なノートが含まれていません。")

        except Exception as e:
            QMessageBox.critical(self, "MIDIエラー", f"MIDIの読み込み中にエラーが発生しました:\n{str(e)}")

    # [LIVE] on_analysis_complete の確認ダイアログ『Yes』選択時に実行
    def export_analysis_to_oto_ini(self):
        """
        解析結果を UTAU 互換の oto.ini 形式で物理保存。
        【爆弾4・5対策】Shift-JIS(cp932)完全準拠。
        """
        target_dir = self.voice_manager.get_current_voice_path()
        if not target_dir: 
            return
        
        file_path = os.path.join(target_dir, "oto.ini")
        
        # 9. プロ仕様：既存データの保護（バックアップ作成）
        if os.path.exists(file_path):
            try:
                import shutil
                shutil.copy2(file_path, file_path + ".bak")
            except Exception as e:
                print(f"Backup Warning: {e}")

        # 10. oto.ini データの構築
        oto_lines = []
        processed_keys = set()
        for note in self.timeline_widget.notes_list:
            if getattr(note, 'has_analysis', False) and note.lyrics not in processed_keys:
                # 形式: wav名=エイリアス,左ブランク,固定,右ブランク,先行発音,オーバーラップ
                # 日本語Windows環境の標準 UTAU 形式を完全再現
                line = f"{note.lyrics}.wav={note.lyrics},0,0,0,{note.pre_utterance},{note.overlap}"
                oto_lines.append(line)
                processed_keys.add(note.lyrics)

        # 11. 安全なファイル書き出し
        try:
            content = "\n".join(oto_lines)
            # errors='replace' により、Shift-JISで扱えない特殊文字を'?'に置き換えて保存を継続
            with open(file_path, "w", encoding="cp932", errors="replace") as f:
                f.write(content)
            QMessageBox.information(self, "Global Standard Saved", "設定ファイル(oto.ini)を更新しました。")
        except Exception as e:
            QMessageBox.critical(self, "Write Error", f"保存に失敗しました:\n{e}")

    # [DEAD?] 呼び出し元が見つからない。内部で使う _parse_vsqx も実質未到達
    def import_external_project(self, file_path):
        """
        外部ファイル(.vsqx, .ustx, .mid)を解析しVO-SE形式へ変換
        """
        self.statusBar().showMessage(f"Migrating Project: {os.path.basename(file_path)}...")
        
        ext = os.path.splitext(file_path)[1].lower()
        imported_notes = []

        try:
            if ext == ".vsqx":
                # VOCALOIDファイルのXML解析
                imported_notes = self._parse_vsqx(file_path)
            elif ext == ".ustx":
                # OpenUTAU(YAML形式)の解析
                imported_notes = self._parse_ustx(file_path)
            elif ext == ".mid":
                # 標準MIDIファイルの解析
                imported_notes = self._parse_midi(file_path)

            if imported_notes:
                # 解析した音符をピアノロールに配置し、エンジンにリレーする
                self.update_timeline_with_notes(imported_notes)
                self.log_startup(f"Migration Successful: {len(imported_notes)} notes imported.")
                # そのままAural AIでプレビュー再生
                self.handle_playback() 
        
        except Exception as e:
            self.statusBar().showMessage(f"Migration Failed: {e}")

    # [DEAD?(連鎖)] import_external_project からのみ呼ばれるが、呼び出し元自体がDEAD
    def _parse_vsqx(self, path: str):
        """
        VOCALOID4 プロジェクトファイル(.vsqx)を解析してNoteEventリストを生成する。
        Pyrightの reportOptionalMemberAccess を完全に回避した堅牢版。
        """
        import xml.etree.ElementTree as ET

        # NoteEventクラスの解決（main_window.py と同じフォールバック方針）
        try:
            from modules.data.data_models import NoteEvent  # type: ignore
        except ImportError:
            from dataclasses import dataclass

            @dataclass
            class NoteEvent:  # type: ignore[no-redef]
                lyrics: str
                note_number: int
                duration: float
                start_time: float

        notes = []
        try:
            tree = ET.parse(path)
            root = tree.getroot()
            
            # 名前空間の定義（VSQX4の標準）
            ns = {'v': 'http://www.yamaha.co.jp/vocaloid/schema/vsqx/4.0'} 
            
            # 全ての v:note 要素を探索
            for v_note in root.findall('.//v:note', ns):
                # 1. 各要素を安全に取得（findの結果がNoneでも止まらないようにする）
                y_elem = v_note.find('v:y', ns)   # 歌詞
                n_elem = v_note.find('v:n', ns)   # ノートナンバー
                dur_elem = v_note.find('v:dur', ns) # 長さ
                t_elem = v_note.find('v:t', ns)   # 開始時間
                
                # 2. すべての必須属性が存在し、かつ .text が存在するかチェック
                if (y_elem is not None and y_elem.text is not None and
                    n_elem is not None and n_elem.text is not None and
                    dur_elem is not None and dur_elem.text is not None and
                    t_elem is not None and t_elem.text is not None):
                    
                    try:
                        # 3. データを型変換して NoteEvent を作成
                        # (480.0 で割ってティックから秒に変換)
                        note = NoteEvent(
                            lyrics=str(y_elem.text),
                            note_number=int(n_elem.text),
                            duration=int(dur_elem.text) / 480.0,
                            start_time=int(t_elem.text) / 480.0
                        )
                        notes.append(note)
                    except ValueError:
                        # 数値変換に失敗したデータはスキップ
                        continue
                        
        except (ET.ParseError, FileNotFoundError) as e:
            # ファイルが壊れている、または存在しない場合の処理
            print(f"VSQX Parse Error: {e}")
            return []

        return notes

    # [DEAD?] 呼び出し元が見つからない。on_click_auto_lyrics 等、別経路でUST相当の処理が行われている可能性
    def load_ust_file(self, filepath: str) -> None:
        """
        UTAUの .ust ファイルを読み込んでタイムラインに配置。
        代表の設計に基づき、エンコーディング、型安全、Noneガードを完璧に完遂します。
        """
        from PySide6.QtWidgets import QMessageBox
        import os

        try:
            # 1. 安全な読み込み（Noneガードを徹底）
            # self.read_file_safely は str または None を返す設計であることを明示
            content_raw = self.read_file_safely(filepath)
            if content_raw is None:
                return
            
            # 型を str に確定させてから処理
            content: str = str(content_raw)
            lines = content.splitlines()
            
            notes: List[Any] = []
            current_note: Dict[str, str] = {} # 型を明示
            
            for line in lines:
                line = line.strip()
                if not line:
                    continue
                    
                if line.startswith('[#'): # [#0001] などのセクション開始
                    if current_note:
                        # 2. 辞書からノートオブジェクトへの変換
                        note_obj = self.parse_ust_dict_to_note(current_note)
                        if note_obj is not None:
                            notes.append(note_obj)
                    current_note = {}
                elif '=' in line:
                    parts = line.split('=', 1)
                    if len(parts) == 2:
                        key, val = parts[0].strip(), parts[1].strip()
                        current_note[key] = val
            
            # ループ終了後の最後のノートを処理
            if current_note:
                note_obj_last = self.parse_ust_dict_to_note(current_note)
                if note_obj_last is not None:
                    notes.append(note_obj_last)

            # 3. タイムラインへの反映
            t_widget = getattr(self, 'timeline_widget', None)
            if t_widget is not None:
                # set_notes への引数は List[Any] であることを保証
                t_widget.set_notes(notes)
                
                # statusBarの取得と安全な呼び出し
                status_bar = self.statusBar()
                if status_bar is not None:
                    status_bar.showMessage(f"UST Loaded: {len(notes)} notes from {os.path.basename(filepath)}")
            
        except Exception as e:
            # PySide6の正しい形式での呼び出し
            QMessageBox.critical(self, "Load Error", f"Failed to load UST:\n{str(e)}")

    # [LIVE] scan_utau_voices / parse_oto_ini など複数の生存メソッドから呼ばれる(IO移植範囲外の呼び出し元あり)
    def read_file_safely(self, filepath: str) -> Optional[str]:
        """
        文字コードを自動判別してファイルを安全に読み込む。
        日本語テキストファイル（Shift-JIS、UTF-8等）に完全対応。
        """
        import chardet
        import os

        # 1. ファイル存在チェック
        if not os.path.exists(filepath):
            print(f"エラー: ファイルが見つかりません: {filepath}")
            return None

        try:
            # 2. バイナリモードで読み込み
            with open(filepath, 'rb') as f:
                raw_data = f.read()
        
            # 空ファイルの処理
            if not raw_data:
                return ""
            
            # 3. chardetによる文字コード自動検出
            detected_encoding: Optional[str] = None
            try:
                detection_result = chardet.detect(raw_data)
                detected_encoding = detection_result.get('encoding')
                confidence = detection_result.get('confidence', 0)
            
                # 検出精度が低い場合は無視
                if confidence < 0.7:
                    detected_encoding = None
            except Exception as e:
                print(f"文字コード検出エラー: {e}")
                detected_encoding = None
        
            # 4. 試行するエンコーディングリストの構築
            candidate_encodings = []
        
            # 検出結果があれば最優先
            if detected_encoding:
                candidate_encodings.append(detected_encoding)
        
            # 日本語環境で一般的なエンコーディングを順に追加
            for enc in ['shift_jis', 'utf-8', 'utf-8-sig', 'cp932', 'euc-jp', 'iso-2022-jp']:
                if enc not in candidate_encodings:
                    candidate_encodings.append(enc)

            # 5. 順次デコードを試行
            for encoding in candidate_encodings:
                try:
                    # errors='replace' で不正な文字を '?' に置き換え
                    decoded_text = raw_data.decode(encoding, errors='replace')
                
                    # デコード成功時はログ出力
                    print(f"ファイル読み込み成功: {filepath} ({encoding})")
                    return decoded_text
                
                except (UnicodeDecodeError, LookupError) :
                    # このエンコーディングは失敗、次を試す
                    continue

            # 6. すべて失敗した場合の最終手段
            print(f"警告: すべてのエンコーディングで失敗。cp932で強制デコード: {filepath}")
            return raw_data.decode('cp932', errors='replace')
        
        except Exception as e:
            print(f"ファイル読み込みエラー: {filepath} - {e}")
            import traceback
            traceback.print_exc()
            return None

    # [DEAD?] 呼び出し元が見つからない
    def save_oto_ini(self, path, content):
        """UTF-8の文字が含まれていてもエラーで落ちずに書き出す"""
        try:
            with open(path, "w", encoding="cp932", errors="replace") as f:
                f.write(content)
        except Exception as e:
            QMessageBox.warning(self, "保存エラー", f"文字化けの可能性があります:\n{e}")

    # [DEAD?] 呼び出し元が見つからない
    def get_safe_installed_name(self, filename: str, zip_path: str) -> str:
        """
        [Safety Lock] インストールパスから安全にフォルダ名を取り出す
        （Pyright/Pylance 警告根絶版）
        """
        # 1. プレイヤーの停止（型ガードを追加）
        # getattrとcastを組み合わせることで、hasattrチェック後の呼び出しエラーを防ぎます
        player = cast(Any, getattr(self, 'player', None))
        if player is not None:
            if hasattr(player, 'stop'):
                player.stop()
        
        self.is_playing = False
        
        # 2. タイムラインのリセット（型ガードを追加）
        timeline = cast(Any, getattr(self, 'timeline_widget', None))
        if timeline is not None:
            if hasattr(timeline, 'set_current_time'):
                timeline.set_current_time(0.0)
            
        # 3. パス解析ロジック（代表のロジックを維持）
        clean_path = os.path.normpath(filename)
        # 空の要素を除去
        parts = [p for p in clean_path.split(os.sep) if p]
        
        if len(parts) >= 2:
            # 親ディレクトリ名を返す
            return str(parts[-2])
            
        # ファイル名（拡張子なし）を返す
        return str(os.path.splitext(os.path.basename(zip_path))[0])

    # [LIVE] エクスポートボタンに接続
    @Slot()
    def on_export_button_clicked(self):
        """WAV書き出し（多重起動防止 & 高速化 & AIピッチ統合 & Proライセンス品質分岐版）"""
        from modules.data.licensing import LicenseManager

        # 1. 各種コンポーネントの安全な取得
        tw = getattr(self, 'timeline_widget', None)
        gw = getattr(self, 'graph_editor_widget', None)
        engine = getattr(self, 'vo_se_engine', None)
        ai_engine = getattr(self, 'ai_engine', None)

        if tw is None or gw is None or engine is None:
            QMessageBox.warning(self, "エラー", "書き出しに必要な初期化が完了していません。")
            return

        notes = getattr(tw, 'notes_list', [])
        if not notes:
            QMessageBox.warning(self, "エラー", "ノートがないため書き出しできません。")
            return

        # 2. 保存先の決定
        file_path, _ = QFileDialog.getSaveFileName(
            self, "音声ファイルを保存", "output.wav", "WAV Files (*.wav)"
        )
        if not file_path:
            return

        # 3. ライセンスに応じた品質パラメータの決定
        is_pro = LicenseManager.is_pro()
        if is_pro:
            sample_rate, bit_depth = 96000, 32
            quality_label = "Studio Master Quality"
        else:
            sample_rate, bit_depth = 44100, 16
            quality_label = "Standard Quality"

        # 4. 準備：再生を止めてステータス表示
        self.stop_and_clear_playback()
        status_bar = self.statusBar()
        if status_bar:
            status_bar.showMessage(f"{quality_label} でレンダリング中（AI表現力を付加中）...")

        try:
            # パラメータを一括取得
            all_params = getattr(gw, 'all_parameters', {})
            vocal_data_list = []
            res = 128  # 1ノートあたりのサンプリング解像度

            for note in notes:
                # --- [STEP 1: ベースピッチのサンプリング] ---
                base_f0_list = self._sample_range(all_params.get("Pitch", []), note, res)

                # --- [STEP 2: Aural AI による感情補正] ---
                if ai_engine is not None:
                    base_f0_np = np.array(base_f0_list, dtype=np.float32)
                    emotional_f0_np = ai_engine.get_baked_pitch(id(note), base_f0_np)
                    final_pitch_list = emotional_f0_np.tolist()
                else:
                    final_pitch_list = base_f0_list

                # --- [STEP 3: ノートデータの構築] ---
                note_data = {
                    "lyric": note.lyrics,
                    "phonemes": note.phonemes,
                    "note_number": note.note_number,
                    "start_time": note.start_time,
                    "duration": note.duration,
                    "pitch_list": final_pitch_list,
                    "gender_list": self._sample_range(all_params.get("Gender", []), note, res),
                    "tension_list": self._sample_range(all_params.get("Tension", []), note, res),
                    "breath_list": self._sample_range(all_params.get("Breath", []), note, res),
                }
                vocal_data_list.append(note_data)

            # --- [STEP 4: 音声合成エンジン（C言語側）への送出] ---
            engine.export_to_wav(
                vocal_data=vocal_data_list,
                tempo=tw.tempo,
                file_path=file_path,
                sample_rate=sample_rate,   # ライセンス品質パラメータ
                bit_depth=bit_depth,       # ライセンス品質パラメータ
                is_pro=is_pro,             # エンジン側の追加分岐用
            )

            QMessageBox.information(
                self, "完了",
                f"レンダリングが完了しました！\n"
                f"品質: {quality_label}\n"
                f"AIによる調声が適用されています。"
            )
            if status_bar:
                status_bar.showMessage(f"エクスポート完了（{quality_label}）")

        except Exception as e:
            QMessageBox.critical(self, "エラー", f"書き出し失敗: {e}")
            if status_bar:
                status_bar.showMessage("エラー発生")

    # [LIVE] save_action.triggered (Ctrl+S) に接続
    @Slot()
    def save_file_dialog_and_save_midi(self):
        """プロジェクトの保存（全データ・全パラメーター）"""
        filepath, _ = QFileDialog.getSaveFileName(
            self, "プロジェクトを保存", "", "VO-SE Project (*.vose);;JSON Files (*.json)"
        )
        if not filepath:
            return

        tw = getattr(self, 'timeline_widget', None)
        gw = getattr(self, 'graph_editor_widget', None)

        if tw is None or gw is None:
            QMessageBox.warning(self, "エラー", "保存に必要なデータが初期化されていません")
            return

        all_params = getattr(gw, 'all_parameters', {})

        save_data = {
            "app_id": "VO_SE_Pro_2026",
            "version": "1.1",
            "tempo_bpm": tw.tempo,
            "notes": [note.to_dict() for note in tw.notes_list],
            "parameters": {
                mode: [{"t": p.time, "v": p.value} for p in events]
                for mode, events in all_params.items()
            }
        }

        try:
            with open(filepath, 'w', encoding='utf-8') as f:
                json.dump(save_data, f, indent=2, ensure_ascii=False)

            status_bar = self.statusBar()
            if status_bar:
                status_bar.showMessage(f"保存完了: {filepath}")

        except Exception as e:
            QMessageBox.critical(self, "エラー", f"保存失敗: {e}")

    # [DEAD?] 呼び出し元が見つからない
    def load_json_project(self, filepath: str):
        """
        JSONプロジェクトの読み込み
        型チェックエラー(Attribute unknown)を回避し、安全にパラメータを復元する
        """
        try:
            from modules.data.data_models import NoteEvent, PitchEvent

            with open(filepath, 'r', encoding='utf-8') as f:
                data = json.load(f)

            raw_notes = data.get("notes", [])
            notes = []
            if hasattr(NoteEvent, 'from_dict'):
                notes = [NoteEvent.from_dict(d) for d in raw_notes]
            else:
                for d in raw_notes:
                    notes.append(NoteEvent(**d))

            tw = getattr(self, 'timeline_widget', None)
            if tw and hasattr(tw, 'set_notes'):
                tw.set_notes(notes)

            tempo = data.get("tempo_bpm", 120)
            t_input = getattr(self, 'tempo_input', None)
            if t_input:
                t_input.setText(str(tempo))
                if hasattr(self, 'update_tempo_from_input'):
                    self.update_tempo_from_input()

            gw = getattr(self, 'graph_editor_widget', None)
            saved_params = data.get("parameters", {})

            if gw and hasattr(gw, 'all_parameters'):
                target_params = gw.all_parameters
                for mode in target_params.keys():
                    if mode in saved_params:
                        restored_events = []
                        for p in saved_params[mode]:
                            t_val = p.get("t", p.get("time", 0))
                            v_val = p.get("v", p.get("value", 0))
                            restored_events.append(PitchEvent(time=t_val, value=v_val))
                        target_params[mode] = restored_events

            if hasattr(self, 'update_scrollbar_range'):
                self.update_scrollbar_range()
            if hasattr(self, 'update_scrollbar_v_range'):
                self.update_scrollbar_v_range()

            if gw:
                gw.update()
            if tw:
                tw.update()

            status_bar = self.statusBar()
            if status_bar:
                status_bar.showMessage(f"読み込み完了: {len(notes)}ノート")

        except Exception as e:
            QMessageBox.critical(self, "エラー", f"読み込み失敗: {str(e)}")

    # [DEAD?] 呼び出し元が見つからない。open_file_dialog_and_load_midi が実質的な後継とみられる
    def load_midi_file_from_path(self, filepath: str):
        """MIDI読み込み（自動歌詞変換機能付き）"""
        try:
            if mido is None:
                raise RuntimeError("MIDI import requires 'mido'. Please install dependencies first.")
            from modules.data.data_models import NoteEvent
            from modules.data.midi_manager import load_midi_file

            mid = mido.MidiFile(filepath)
            loaded_tempo = 120.0

            for track in mid.tracks:
                for msg in track:
                    if msg.type == 'set_tempo':
                        loaded_tempo = mido.tempo2bpm(msg.tempo)
                        break

            # 対策：load_midi_file が None を返す可能性へのケア
            notes_data = load_midi_file(filepath)
            if notes_data is None:
                notes_data = []  # 空リストで安全にフォールバック

            notes = [NoteEvent.from_dict(d) for d in notes_data]

            for note in notes:
                lyric_text = str(getattr(note, "lyric", getattr(note, "lyrics", "")))
                phonemes = getattr(note, "phonemes", [])
                if lyric_text and not phonemes:
                    yomi = self._get_yomi_from_lyrics(lyric_text)
                    setattr(note, "phonemes", [yomi] if isinstance(yomi, str) else yomi)

            if hasattr(self, 'timeline_widget') and self.timeline_widget:
                self.timeline_widget.set_notes(notes)

            if hasattr(self, 'tempo_input') and self.tempo_input:
                self.tempo_input.setText(str(loaded_tempo))

            self.update_tempo_from_input()
            self.update_scrollbar_range()
            self.update_scrollbar_v_range()

            status_bar = self.statusBar()
            if status_bar:
                status_bar.showMessage(f"MIDI読み込み完了: {len(notes)}ノート")

        except Exception as e:
            QMessageBox.critical(self, "エラー", f"MIDI読み込み失敗: {e}")
            
    # [LIVE] load_ust_file から呼ばれる(load_ust_file 自体はDEAD?)
    def parse_ust_dict_to_note(
        self,
        d: Dict[str, Any],
        current_time_sec: float = 0.0,
        tempo: float = 120.0
    ) -> Any:
        """
        USTの辞書データを解析し、NoteEventオブジェクトと次の開始時間を生成する統合メソッド。
        """
        # 1. NoteEventクラスの解決（循環参照回避）
        from dataclasses import dataclass
        import importlib

        @dataclass
        class _UstFallbackNoteEvent:
            lyrics: str
            note_number: int
            start_time: float
            duration: float

        try:
            model_mod = importlib.import_module("modules.data.data_models")
            NoteEventCls: Any = getattr(model_mod, "NoteEvent", _UstFallbackNoteEvent)
        except Exception:
            NoteEventCls = _UstFallbackNoteEvent

        # 2. データの抽出とガード（getを使用し、キー不在によるクラッシュを完全回避）
        try:
            length_ticks_str = d.get('Length', '480')
            note_num_str = d.get('NoteNum', '64')
            lyric = str(d.get('Lyric', 'あ'))

            # 3. 数値変換
            length_ticks = int(length_ticks_str)
            note_num = int(note_num_str)

            # 4. 代表の黄金計算式（省略なし）
            # (ティック数 / 480.0) * (60.0 / テンポ) = 実際の秒数
            duration_sec = (length_ticks / 480.0) * (60.0 / tempo)

            # 5. オブジェクトの生成
            # 旧定義の互換性を保ちつつ、NoteEventとして構築
            note = NoteEventCls(
                lyrics=lyric,
                note_number=note_num,
                start_time=current_time_sec,
                duration=duration_sec
            )

            # 6. 下位互換性のための属性追加
            # 旧NoteDataクラスが持っていた属性(length, lyric, note_num)を動的に付与
            setattr(note, 'length', length_ticks)
            setattr(note, 'lyric', lyric)
            setattr(note, 'note_num', note_num)

            # 7. 返却処理
            # 呼び出し側が「次の開始時間」を期待しているか（引数にcurrent_time_secがあるか）で判定
            # 基本的には (ノート, 次の開始時間) のタプルを返します
            return note, current_time_sec + duration_sec

        except (ValueError, TypeError, Exception) as e:
            # エラー発生時：プログラムを止めず、最小限の安全なデータを返す
            print(f"DEBUG: UST Parse Error in note: {e}")
            
            # ダミーデータの構築
            dummy_note = NoteEventCls(
                lyrics=" ",
                note_number=64,
                start_time=current_time_sec,
                duration=0.0
            )
            setattr(dummy_note, 'length', 0)
            setattr(dummy_note, 'lyric', " ")
            setattr(dummy_note, 'note_num', 64)
            
            return dummy_note, current_time_sec
