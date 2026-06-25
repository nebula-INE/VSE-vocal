#intonation.py

import subprocess
import os
import sys
import re

class IntonationAnalyzer:
    def __init__(self):
        # パス設定：プロジェクト構造に合わせて調整
        if getattr(sys, 'frozen', False):
            self.root = str(getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__))))
        else:
            # backend/ から見たプロジェクトルート (../)
            self.root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            
        self.exe = os.path.join(self.root, "bin", "open_jtalk", "open_jtalk.exe")
        self.dic = os.path.join(self.root, "bin", "open_jtalk", "dic")

    def analyze(self, text):
        """テキストを解析してアクセント句情報(traceデータ)を返す"""
        temp_input = "temp_in.txt"
        temp_trace = "temp_trace.txt"
        
        # Windowsでのパスの空白対策として絶対パス化
        abs_exe = os.path.abspath(self.exe)
        abs_dic = os.path.abspath(self.dic)
        
        try:
            with open(temp_input, "w", encoding="utf-8") as f:
                f.write(text)
                
            # Open JTalk実行 (-ot で解析ログを出力)
            cmd = [abs_exe, "-x", abs_dic, "-ot", temp_trace, "-ow", "NUL", temp_input]
            
            # shell=Trueは環境によって必要。subprocess.runで静かに実行
            subprocess.run(cmd, check=True, shell=True, capture_output=True)
            
            if os.path.exists(temp_trace):
                with open(temp_trace, "r", encoding="utf-8") as f:
                    return f.read()
            return ""
        except Exception as e:
            print(f"Intonation Analysis Error: {e}")
            return ""
        finally:
            for t in [temp_input, temp_trace]:
                if os.path.exists(t): 
                    os.remove(t)

    def parse_trace_to_notes(self, trace_data):
        """
        Open JTalkのトレースログから音素とピッチを抽出して、
        タイムライン用の辞書リストに変換する
        """
        notes = []
        if not trace_data: 
            return notes

        # [粗い解析] トレースデータ内の「Label indicating state transitions」セクションを探す
        # フォーマット例: 0-10000 xx^xx-pau+sh@xx...
        pattern = re.compile(r'(\d+)-(\d+)\s+([^\s]+)')
        lines = trace_data.split('\n')
        
       # current_time_ms = 0
        
        for line in lines:
            line = line.strip()
            # 音素ラベル行を特定 (例: 50000-150000 a^b-k+i@...)
            match = pattern.match(line)
            if match:
                # 使わない変数の代入を消し、labelを正しく扱う(未使用)
                _start_tick = int(match.group(1))
                _end_tick = int(match.group(2))
                _label_text = match.group(3) if (match.lastindex or 0) >= 3 else ""
                # ここで label_text を使った処理を書くか、なければ pass
