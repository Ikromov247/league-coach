"""Text-to-speech through the macOS `say` command."""

import subprocess
import threading

from .config import SPEECH_RATE

_lock = threading.Lock()


def say(text: str) -> None:
    """Speaks and blocks until done, so two lines never overlap."""
    with _lock:
        subprocess.run(["say", "-r", str(SPEECH_RATE), text], check=False)
