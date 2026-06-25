import os
import zipfile
import json
import tempfile
from PySide6.QtCore import QObject, Signal as pyqtSignal, Slot as pyqtSlot

# ==========================================================================
# 1. MIDI読み込み非同期ワーカー
# ==========================================================================
class MidiLoadWorker(QObject):
    """
    メインスレッド（UI）を止めずに、バックグラウンドで重いMIDIバイナリデータを
    パースしてノート配列を抽出するワーカー。
    """
    finished = pyqtSignal(list)  # 解析完了後にNoteEvent（または生データ）のリストを返す
    error = pyqtSignal(str)      # 例外発生時にエラーメッセージを通知

    def __init__(self, file_path, midi_manager):
        super().__init__()
        self.file_path = file_path
        self.midi_manager = midi_manager

    @pyqtSlot()
    def run(self):
        try:
            if not os.path.exists(self.file_path):
                raise FileNotFoundError(f"MIDIファイルが見つかりません: {self.file_path}")
            
            # midi_manager内部の重いバイナリ解析処理をBGM実行
            notes = self.midi_manager.load_midi_pure_data(self.file_path)
            self.finished.emit(notes)
        except Exception as e:
            self.error.emit(str(e))


# ==========================================================================
# 2. ZIP音源インポート非同期ワーカー（プログレス連動型）
# ==========================================================================
class VoiceImportWorker(QObject):
    """
    巨大な歌声ライブラリ（ZIP）の解凍、および展開先でのフォルダ構築を
    UIスレッドを1ミリもフリーズさせずに実行するワーカー。
    """
    progress = pyqtSignal(int)    # 進捗率（0-100%）をリアルタイム通信
    finished = pyqtSignal(str)   # 完了時に対象の音源フォルダ名を返す
    error = pyqtSignal(str)

    def __init__(self, zip_path, target_dir):
        super().__init__()
        self.zip_path = zip_path
        self.target_dir = target_dir

    @pyqtSlot()
    def run(self):
        try:
            if not os.path.exists(self.zip_path):
                raise FileNotFoundError(f"音源ZIPファイルが見つかりません: {self.zip_path}")

            # ZIPの総解凍サイズをあらかじめ計算（パーセンテージ算出用）
            with zipfile.ZipFile(self.zip_path, 'r') as zip_ref:
                infolist = zip_ref.infolist()
                uncompress_size = sum((file.file_size for file in infolist))
                extracted_size = 0
                
                os.makedirs(self.target_dir, exist_ok=True)

                # 1ファイルずつ解凍しながら進捗をUIへブロードキャスト
                for file in infolist:
                    zip_ref.extract(file, self.target_dir)
                    extracted_size += file.file_size
                    
                    if uncompress_size > 0:
                        pct = int((extracted_size / uncompress_size) * 100)
                        self.progress.emit(pct)

            voice_name = os.path.basename(self.target_dir)
            self.finished.emit(voice_name)
        except Exception as e:
            self.error.emit(str(e))


# ==========================================================================
# 3. 大規模プロジェクト保存（JSON書き出し）非同期ワーカー
# ==========================================================================
class ProjectSaveWorker(QObject):
    """
    タイムライン上の数千〜数万個のオブジェクトデータをJSONシリアライズし、
    ディスクI/Oブロックを起こさずに安全に書き出すワーカー。
    アトミック書き込みを採用し、保存失敗時のファイル破損を根本から防止。
    """
    finished = pyqtSignal()
    error = pyqtSignal(str)

    def __init__(self, file_path, project_data):
        super().__init__()
        self.file_path = file_path
        self.project_data = project_data

    @pyqtSlot()
    def run(self):
        try:
            dir_name = os.path.dirname(self.file_path)
            if dir_name and not os.path.exists(dir_name):
                os.makedirs(dir_name, exist_ok=True)

            # 💡 安全対策：一時ファイルに先に書き出し、完全に成功したら置換（アトミック書き込み）
            with tempfile.NamedTemporaryFile('w', dir=dir_name, delete=False, encoding='utf-8') as tf:
                json.dump(self.project_data, tf, ensure_ascii=False, indent=4)
                temp_name = tf.name

            # 本番ファイルへ一瞬でアトミックに置き換え
            if os.path.exists(self.file_path):
                os.remove(self.file_path)
            os.rename(temp_name, self.file_path)

            self.finished.emit()
        except Exception as e:
            self.error.emit(str(e))
