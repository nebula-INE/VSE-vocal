#vose_types.py
import ctypes
from typing import Iterable


class CNoteEvent(ctypes.Structure):
    """`include/vose_core.h` の NoteEvent と ABI を一致させる。"""

    _fields_ = [
        ("wav_path", ctypes.c_char_p),
        ("pitch_curve", ctypes.POINTER(ctypes.c_double)),
        ("pitch_length", ctypes.c_int),
        ("gender_curve", ctypes.POINTER(ctypes.c_double)),
        ("tension_curve", ctypes.POINTER(ctypes.c_double)),
        ("breath_curve", ctypes.POINTER(ctypes.c_double)),
        ("vibrato_depth_curve", ctypes.POINTER(ctypes.c_double)),
        ("vibrato_rate_curve", ctypes.POINTER(ctypes.c_double)),
        ("vibrato_curve_length", ctypes.c_int),
    ]


def as_c_double_array(values: Iterable[float]) -> ctypes.Array[ctypes.c_double]:
    """Python iterable を C の `double[]` に変換する。"""

    seq = tuple(float(v) for v in values)
    return (ctypes.c_double * len(seq))(*seq)

def validate_note_event_layout():
    """CNoteEvent のレイアウト検証。

    C++ 側の NoteEvent は 64bit 環境で pointer x 7 + int x 2 の
    8-byte alignment になるため 72 bytes になる。
    """

    pointer_size = ctypes.sizeof(ctypes.c_void_p)
    if pointer_size == 8 and ctypes.sizeof(CNoteEvent) != 72:
        raise RuntimeError(
            f"CNoteEvent ABI mismatch: expected 72 bytes, "
            f"got {ctypes.sizeof(CNoteEvent)} bytes"
        )



__all__ = ["CNoteEvent", "as_c_double_array", "validate_note_event_layout"]
