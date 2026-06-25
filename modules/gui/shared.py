# ==========================================================================
# modules/gui/shared.py
#
# main_window.py とその Mixin 群（modules/gui/mixins/*）の双方から
# 参照される、ごく小さな共有シンボルだけをまとめたモジュール。
#
# 目的:
#   - main_window.py から Mixin に分割したメソッドが、グローバルスコープの
#     ヘルパー関数やエンジン参照を「main_window.py を逆 import する」ことなく
#     使えるようにするための置き場所。
#   - main_window.py 側もここから import することで、定義の重複を避ける。
#
# 注意:
#   - ここに置くのは「依存が単純で、循環 import を起こさないもの」だけにする。
#   - VoiceCardGallery のような複雑な UI クラス（QWidget 派生で他のクラスにも
#     依存するもの）はここには置かず、必要な側で遅延 import する方針とする。
# ==========================================================================

import os
import sys
import importlib
from typing import Any, cast


def get_resource_path(relative_path: str) -> str:
    """
    内蔵DLLやアセットなどのリソースパスを取得する。

    PyInstaller等でEXE化された後（sys.frozen=True）は一時展開フォルダ
    (sys._MEIPASS) を基準にし、開発中（.py実行）はこのファイルの場所を
    基準にする。

    元々は main_window.py のモジュールレベルに定義されていた関数。
    """
    if getattr(sys, 'frozen', False):
        base_path = getattr(sys, '_MEIPASS', os.path.dirname(os.path.abspath(__file__)))
    else:
        base_path = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(base_path, relative_path)


# --------------------------------------------------------------------------
# DynamicsAIEngine: 標準のDynamics AIエンジン。
# modules.utils.dynamics_ai からの動的import。失敗時はパススルーの
# フォールバッククラスを使う（元の main_window.py と同じロジック）。
# --------------------------------------------------------------------------
try:
    DynamicsAIEngine = importlib.import_module("modules.utils.dynamics_ai").DynamicsAIEngine  # type: ignore[attr-defined]
except Exception:
    class _DynamicsAIEngineFallback:
        def generate_emotional_pitch(self, f0):
            return f0
    DynamicsAIEngine = cast(Any, _DynamicsAIEngineFallback)
