import ctypes
import os
import platform
from typing import Optional

from modules.ffi import CNoteEvent, validate_note_event_layout


class VoseCoreManager:
    """DLLロードと C 関数シグネチャ管理を行うシングルトン。"""

    _instance: Optional["VoseCoreManager"] = None
    lib: Optional[ctypes.CDLL] = None
    _initialized: bool
    _disabled_reason: Optional[str]

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super(VoseCoreManager, cls).__new__(cls)
            cls._instance._initialized = False
            cls._instance._disabled_reason = None
        return cls._instance

    def _library_names(self) -> tuple[str, ...]:
        system = platform.system()
        if system == "Windows":
            return ("vose_core.dll",)
        if system == "Darwin":
            return ("libvose_core.dylib", "vose_core.dylib")
        return ("libvose_core.so", "vose_core.so")


    def _candidate_paths(self) -> list[str]:
        repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
        base_dirs = [
            os.path.join(repo_root, "bin"),
            os.path.join(os.path.dirname(__file__), "..", "bin"),
            os.path.join(os.getcwd(), "bin"),
        ]
        return [os.path.join(base_dir, lib_name) for base_dir in base_dirs for lib_name in self._library_names()]

    def _init_engine(self):
        if self._initialized:
            return

        disable_native = os.getenv("VOSE_DISABLE_NATIVE_CORE", "").lower() in {"1", "true", "yes", "on"}
        if disable_native:
            self._disabled_reason = "VOSE_DISABLE_NATIVE_CORE is enabled"
            self._initialized = True
            self.lib = None
            print(f"⚠️ VOSE Core disabled: {self._disabled_reason}")
            return

        load_errors: list[str] = []
        for path in self._candidate_paths():
            if not os.path.exists(path):
                continue
            try:
                self.lib = ctypes.CDLL(path)
                self._setup_prototypes()
                print(f"[OK] VOSE Core Engine Loaded: {path}")
                self._initialized = True
                return
            except Exception as e:
                load_errors.append(f"  {path}: {e}")
                print(f"[Error] Load Error: {path} ({e})")

        # [FIX-1] DLL未検出時は理由を記録し、後から get_lib() で参照できるようにする
        reason = "DLL not found in any candidate path"
        if load_errors:
            reason = "DLL found but failed to load:\n" + "\n".join(load_errors)
        self._disabled_reason = reason
        print(f"[Warning] VOSE Core DLL not found. Engine is offline.\n  Reason: {reason}")
        self.lib = None
        self._initialized = True

    def _setup_prototypes(self) -> None:
        if not self.lib:
            return

        validate_note_event_layout()

        # [FIX-2] 各シンボルの存在確認を try/except で確実に行う
        try:
            self.lib.execute_render.argtypes = [
                ctypes.POINTER(CNoteEvent),
                ctypes.c_int,
                ctypes.c_char_p,
                ctypes.c_int,
            ]
            self.lib.execute_render.restype = None
        except AttributeError:
            print("[Warning] execute_render not found in DLL — skipping prototype setup")

        try:
            self.lib.init_official_engine.argtypes = []
            self.lib.init_official_engine.restype = None
        except AttributeError:
            pass  # オプションのシンボルなので警告不要

        try:
            self.lib.synthesize_by_name.argtypes = [ctypes.c_char_p, ctypes.c_float]
            self.lib.synthesize_by_name.restype = ctypes.POINTER(ctypes.c_float)
        except AttributeError:
            pass  # オプションのシンボルなので警告不要

    def get_lib(self) -> Optional[ctypes.CDLL]:
        if not self._initialized:
            self._init_engine()

        # [FIX-3] None の理由をログに残し、呼び出し側のデバッグを助ける
        if self.lib is None:
            reason = self._disabled_reason or "unknown reason"
            print(f"[Warning] VOSE Core Engine is not available ({reason}).")

        return self.lib

    def is_available(self) -> bool:
        """エンジンが利用可能かどうかを返す。get_lib() の None チェックの代替。"""
        if not self._initialized:
            self._init_engine()
        return self.lib is not None

    def disabled_reason(self) -> Optional[str]:
        """エンジンが無効になっている理由を返す（デバッグ用）。"""
        return self._disabled_reason


vose_manager = VoseCoreManager()
__all__ = ["VoseCoreManager", "vose_manager", "CNoteEvent"]
