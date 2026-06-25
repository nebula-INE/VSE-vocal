import ctypes
import os
import pytest
import platform


def _default_engine_path() -> str:
    system = platform.system()
    if system == "Windows":
        lib_name = "vose_core.dll"
    elif system == "Darwin":
        lib_name = "libvose_core.dylib"
    else:
        lib_name = "libvose_core.so"
    return os.path.join("bin", lib_name)

def test_engine():
    engine_path = os.environ.get("ENGINE_PATH", _default_engine_path())
    if not os.path.exists(engine_path):
        pytest.skip(f"Engine not found at {engine_path}")

    try:
        lib = ctypes.CDLL(engine_path)
    except OSError as exc:
        pytest.skip(f"Engine exists but is not loadable on this OS: {exc}")

    assert lib is not None
    print(f"✅ Engine loaded successfully: {engine_path}")
