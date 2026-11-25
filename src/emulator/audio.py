"""Real-time audio output for the libretro core (optional, toggleable).

The core produces 16-bit signed stereo PCM via its audio callback; this plays it
through an SDL2 output device (pull model: SDL calls us to fill the buffer, we
drain a ring buffer the emulator fills). Kept simple on purpose — game audio only
sounds clean at 1x speed, so the viewer mutes it during fast-forward.

Gracefully no-ops if SDL audio / a device isn't available (self.ok == False).
"""

from __future__ import annotations

import threading

try:
    from pygame._sdl2 import AudioDevice, AUDIO_S16LSB, get_audio_device_names
    _HAVE_SDL_AUDIO = True
except Exception:
    _HAVE_SDL_AUDIO = False


class AudioOut:
    def __init__(self, sample_rate: float, channels: int = 2,
                 chunksize: int = 1024, max_ms: int = 150):
        self.ok = False
        self._enabled = False
        self._buf = bytearray()
        self._lock = threading.Lock()
        bpf = channels * 2                              # bytes per stereo frame
        self._max = max(1, int(sample_rate * max_ms / 1000)) * bpf
        if not _HAVE_SDL_AUDIO:
            return
        try:
            names = get_audio_device_names(False)
            # pygame-ce AudioDevice is positional:
            # (devicename, iscapture, frequency, audioformat, numchannels,
            #  chunksize, allowed_changes, callback)
            self._dev = AudioDevice(
                names[0] if names else None, False,
                int(round(sample_rate)) or 32040, AUDIO_S16LSB, channels,
                chunksize, 0, self._fill)
            self._dev.pause(1)                          # start muted
            self.ok = True
        except Exception:
            self.ok = False

    def _fill(self, _device, mv):
        """SDL pull callback (audio thread): fill `mv` (a byte buffer, itemsize 1)
        from the ring buffer, silence on underrun. Must never raise — an exception
        here crashes SDL's audio thread."""
        try:
            n = len(mv)
            take = 0
            with self._lock:
                take = min(n, len(self._buf))
                if take:
                    mv[:take] = bytes(self._buf[:take])
                    del self._buf[:take]
            if take < n:
                mv[take:] = bytes(n - take)             # zero-fill (silence)
        except Exception:
            try:
                mv[:] = bytes(len(mv))
            except Exception:
                pass

    def feed(self, data: bytes) -> None:
        """Emulator (main thread) pushes PCM here each frame. Dropped unless
        enabled; capped so latency can't grow unbounded if we outrun playback."""
        if not (self.ok and self._enabled) or not data:
            return
        with self._lock:
            self._buf += data
            extra = len(self._buf) - self._max
            if extra > 0:
                del self._buf[:extra]                   # drop oldest, keep fresh

    def set_enabled(self, on: bool) -> None:
        if not self.ok:
            return
        on = bool(on)
        if on == self._enabled:
            return
        self._enabled = on
        if on:
            self._dev.pause(0)
        else:
            self._dev.pause(1)
            with self._lock:
                self._buf.clear()

    def close(self) -> None:
        if self.ok:
            try:
                self._dev.close()
            except Exception:
                pass
