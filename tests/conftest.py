# tests/conftest.py
import sys
from pathlib import Path

# プロジェクトルートを sys.path に追加
repo_root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(repo_root))
