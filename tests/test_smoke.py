# tests/test_smoke.py
import os
import subprocess
import sys
import time
import pytest

@pytest.mark.smoke
def test_app_startup(tmp_path):
    """パッケージ化されたアプリが起動し、クラッシュしないことを確認する"""
    # 環境変数でスモークテストモードを有効化
    env = os.environ.copy()
    env["VOSE_STARTUP_SMOKE_TEST"] = "1"
    env["QT_QPA_PLATFORM"] = "offscreen"
    env["QT_MEDIA_BACKEND"] = "ffmpeg"
    env["PYTHONUTF8"] = "1"

    # OSに応じた実行ファイルのパスを決定
    if sys.platform == "win32":
        app_path = "dist/VO-SE_vocal_Win.exe"
    elif sys.platform == "darwin":
        app_path = "dist/VO-SE_vocal_Mac.app/Contents/MacOS/VO-SE_vocal_Mac"
    else:
        app_path = "dist/VO-SE_vocal_Linux"

    assert os.path.exists(app_path), f"Executable not found: {app_path}"

    proc = subprocess.Popen(
        [app_path],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True
    )

    # 5秒間待機して安定性を確認
    time.sleep(5)
    returncode = proc.poll()

    if returncode is None:
        # 正常に動作中 → 終了させて成功
        proc.terminate()
        try:
            proc.wait(timeout=3)
        except subprocess.TimeoutExpired:
            proc.kill()
        assert True
    else:
        stdout, stderr = proc.communicate()
        pytest.fail(f"App crashed with code {returncode}\nSTDOUT:{stdout}\nSTDERR:{stderr}")
