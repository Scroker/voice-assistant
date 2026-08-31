"""
Audio module for Voice Assistant Daemon
"""
from .vad import SilenceDetector
from .player import AudioPlayer

__all__ = ['SilenceDetector', 'AudioPlayer']
