# modules/gui/voice_mixin.py
"""
VoiceManagerMixin — MainWindow への VoiceManager 機能注入
実体は modules/audio/voice_manager.py の VoiceManager クラスに委譲します。
"""
from __future__ import annotations

try:
    from modules.audio.voice_manager import VoiceManager
except ImportError:
    from ..audio.voice_manager import VoiceManager


class VoiceManagerMixin:
    """MainWindow に音声管理機能を追加する Mixin。"""

    def _init_voice_manager(self) -> None:
        self.voice_manager = VoiceManager()

    def get_available_voices(self) -> list:
        if hasattr(self, "voice_manager"):
            return self.voice_manager.get_available_voices()
        return []

    def set_voice(self, voice_name: str) -> None:
        if hasattr(self, "voice_manager"):
            self.voice_manager.set_voice(voice_name)
