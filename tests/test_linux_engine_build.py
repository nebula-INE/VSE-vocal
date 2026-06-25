import ctypes
from pathlib import Path

from modules.ffi import CNoteEvent, validate_note_event_layout


REPO_ROOT = Path(__file__).resolve().parents[1]


def test_cmake_builds_linux_shared_object_to_bin():
    cmake_config = (REPO_ROOT / "CMakeLists.txt").read_text(encoding="utf-8")

    assert "add_library(vose_core SHARED" in cmake_config
    assert 'OUTPUT_NAME "vose_core"' in cmake_config
    assert 'LIBRARY_OUTPUT_DIRECTORY "${CMAKE_CURRENT_SOURCE_DIR}/bin"' in cmake_config


def test_linux_build_script_verifies_ctypes_load():
    build_script = (REPO_ROOT / "scripts" / "build_linux_engine.sh").read_text(encoding="utf-8")

    assert "cmake -S" in build_script
    assert "--target vose_core" in build_script
    assert "bin/libvose_core.so" in build_script
    assert "ctypes.CDLL(engine_path)" in build_script


def test_python_note_event_matches_cpp_linux_engine_abi():
    validate_note_event_layout()

    assert CNoteEvent.pitch_curve.offset == ctypes.sizeof(ctypes.c_void_p)
    assert CNoteEvent.vibrato_depth_curve.offset > CNoteEvent.breath_curve.offset
    assert CNoteEvent.vibrato_curve_length.offset > CNoteEvent.vibrato_rate_curve.offset
