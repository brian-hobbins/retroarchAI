"""A minimal libretro frontend implemented with ctypes.

Loads a libretro core shared object (e.g. snes9x2010_libretro.so), loads a ROM,
and lets Python:
  * step the emulator one frame at a time (run)
  * read the current video framebuffer as RGB888 (get_frame_rgb)
  * inject per-port controller input (set_input / set_buttons)
  * save/restore state (serialize / unserialize)

Deliberately dependency-free (stdlib ctypes only) so it is immune to Python
version / wheel availability problems.

libretro API reference: libretro-common/include/libretro.h
"""

from __future__ import annotations

import ctypes as C
import os
from typing import Dict, Optional, Tuple

# --- libretro constants -----------------------------------------------------

RETRO_DEVICE_JOYPAD = 1

# retro_get_memory_data/size ids
RETRO_MEMORY_SAVE_RAM = 0
RETRO_MEMORY_RTC = 1
RETRO_MEMORY_SYSTEM_RAM = 2   # the console's main work RAM (SNES WRAM = 128 KB)
RETRO_MEMORY_VIDEO_RAM = 3

# Joypad button ids (RETRO_DEVICE_ID_JOYPAD_*)
SNES_BUTTONS = {
    "B": 0,
    "Y": 1,
    "SELECT": 2,
    "START": 3,
    "UP": 4,
    "DOWN": 5,
    "LEFT": 6,
    "RIGHT": 7,
    "A": 8,
    "X": 9,
    "L": 10,
    "R": 11,
}

# retro_pixel_format
PIXEL_0RGB1555 = 0
PIXEL_XRGB8888 = 1
PIXEL_RGB565 = 2

# retro_environment commands (low 16 bits; experimental bit 0x10000 is masked off)
ENV_GET_OVERSCAN = 2
ENV_GET_CAN_DUPE = 3
ENV_SET_MESSAGE = 6
ENV_SET_PERFORMANCE_LEVEL = 8
ENV_GET_SYSTEM_DIRECTORY = 9
ENV_SET_PIXEL_FORMAT = 10
ENV_SET_INPUT_DESCRIPTORS = 11
ENV_GET_VARIABLE = 15
ENV_SET_VARIABLES = 16
ENV_GET_VARIABLE_UPDATE = 17
ENV_SET_SUPPORT_NO_GAME = 18
ENV_GET_LOG_INTERFACE = 27
ENV_GET_SAVE_DIRECTORY = 31
ENV_GET_CORE_OPTIONS_VERSION = 52
ENV_SET_CORE_OPTIONS = 53
ENV_SET_CORE_OPTIONS_INTL = 54
ENV_SET_CORE_OPTIONS_V2 = 67
ENV_SET_CORE_OPTIONS_V2_INTL = 68


# --- ctypes structs ---------------------------------------------------------

class retro_system_info(C.Structure):
    _fields_ = [
        ("library_name", C.c_char_p),
        ("library_version", C.c_char_p),
        ("valid_extensions", C.c_char_p),
        ("need_fullpath", C.c_bool),
        ("block_extract", C.c_bool),
    ]


class retro_game_info(C.Structure):
    _fields_ = [
        ("path", C.c_char_p),
        ("data", C.c_void_p),
        ("size", C.c_size_t),
        ("meta", C.c_char_p),
    ]


class retro_game_geometry(C.Structure):
    _fields_ = [
        ("base_width", C.c_uint),
        ("base_height", C.c_uint),
        ("max_width", C.c_uint),
        ("max_height", C.c_uint),
        ("aspect_ratio", C.c_float),
    ]


class retro_system_timing(C.Structure):
    _fields_ = [("fps", C.c_double), ("sample_rate", C.c_double)]


class retro_system_av_info(C.Structure):
    _fields_ = [("geometry", retro_game_geometry), ("timing", retro_system_timing)]


# --- callback function types ------------------------------------------------

ENV_CB = C.CFUNCTYPE(C.c_bool, C.c_uint, C.c_void_p)
VIDEO_CB = C.CFUNCTYPE(None, C.c_void_p, C.c_uint, C.c_uint, C.c_size_t)
AUDIO_SAMPLE_CB = C.CFUNCTYPE(None, C.c_int16, C.c_int16)
AUDIO_BATCH_CB = C.CFUNCTYPE(C.c_size_t, C.POINTER(C.c_int16), C.c_size_t)
INPUT_POLL_CB = C.CFUNCTYPE(None)
INPUT_STATE_CB = C.CFUNCTYPE(C.c_int16, C.c_uint, C.c_uint, C.c_uint, C.c_uint)


class LibretroCore:
    def __init__(self, core_path: str, system_dir: str, save_dir: str):
        if not os.path.isfile(core_path):
            raise FileNotFoundError(core_path)
        self.core_path = core_path
        self._lib = C.CDLL(core_path)
        self._bind_signatures()

        self._system_dir = os.fspath(system_dir).encode()
        self._save_dir = os.fspath(save_dir).encode()
        os.makedirs(system_dir, exist_ok=True)
        os.makedirs(save_dir, exist_ok=True)

        self.pixel_format = PIXEL_0RGB1555  # libretro default until core sets it

        # latest frame
        self._fb: Optional[bytes] = None
        self._fb_w = 0
        self._fb_h = 0
        self._fb_pitch = 0

        # per-port input: {port: {button_id: int16}}
        self._input: Dict[int, Dict[int, int]] = {0: {}, 1: {}}

        self._loaded = False
        self._keep_alive = []  # keep ctypes resources from being GC'd

        self._install_callbacks()
        self._lib.retro_init()

    # -- low level binding ---------------------------------------------------

    def _bind_signatures(self):
        lib = self._lib
        lib.retro_api_version.restype = C.c_uint
        lib.retro_init.restype = None
        lib.retro_deinit.restype = None
        lib.retro_run.restype = None

        lib.retro_get_system_info.argtypes = [C.POINTER(retro_system_info)]
        lib.retro_get_system_av_info.argtypes = [C.POINTER(retro_system_av_info)]

        lib.retro_set_environment.argtypes = [ENV_CB]
        lib.retro_set_video_refresh.argtypes = [VIDEO_CB]
        lib.retro_set_audio_sample.argtypes = [AUDIO_SAMPLE_CB]
        lib.retro_set_audio_sample_batch.argtypes = [AUDIO_BATCH_CB]
        lib.retro_set_input_poll.argtypes = [INPUT_POLL_CB]
        lib.retro_set_input_state.argtypes = [INPUT_STATE_CB]

        lib.retro_load_game.argtypes = [C.POINTER(retro_game_info)]
        lib.retro_load_game.restype = C.c_bool
        lib.retro_unload_game.restype = None

        lib.retro_set_controller_port_device.argtypes = [C.c_uint, C.c_uint]

        lib.retro_get_memory_data.argtypes = [C.c_uint]
        lib.retro_get_memory_data.restype = C.c_void_p
        lib.retro_get_memory_size.argtypes = [C.c_uint]
        lib.retro_get_memory_size.restype = C.c_size_t

        lib.retro_serialize_size.restype = C.c_size_t
        lib.retro_serialize.argtypes = [C.c_void_p, C.c_size_t]
        lib.retro_serialize.restype = C.c_bool
        lib.retro_unserialize.argtypes = [C.c_void_p, C.c_size_t]
        lib.retro_unserialize.restype = C.c_bool

    def _install_callbacks(self):
        self._env_cb = ENV_CB(self._on_environment)
        self._video_cb = VIDEO_CB(self._on_video)
        self._audio_sample_cb = AUDIO_SAMPLE_CB(self._on_audio_sample)
        self._audio_batch_cb = AUDIO_BATCH_CB(self._on_audio_batch)
        self._input_poll_cb = INPUT_POLL_CB(self._on_input_poll)
        self._input_state_cb = INPUT_STATE_CB(self._on_input_state)

        # environment must be set before retro_init so the core can query us.
        self._lib.retro_set_environment(self._env_cb)
        self._lib.retro_set_video_refresh(self._video_cb)
        self._lib.retro_set_audio_sample(self._audio_sample_cb)
        self._lib.retro_set_audio_sample_batch(self._audio_batch_cb)
        self._lib.retro_set_input_poll(self._input_poll_cb)
        self._lib.retro_set_input_state(self._input_state_cb)

    # -- callbacks -----------------------------------------------------------

    def _on_environment(self, cmd: int, data: int) -> bool:
        c = cmd & 0xFFFF
        if c == ENV_SET_PIXEL_FORMAT:
            fmt = C.cast(data, C.POINTER(C.c_int)).contents.value
            self.pixel_format = fmt
            return True
        if c == ENV_GET_CAN_DUPE:
            C.cast(data, C.POINTER(C.c_bool))[0] = True
            return True
        if c == ENV_GET_SYSTEM_DIRECTORY:
            C.cast(data, C.POINTER(C.c_char_p))[0] = self._system_dir
            return True
        if c == ENV_GET_SAVE_DIRECTORY:
            C.cast(data, C.POINTER(C.c_char_p))[0] = self._save_dir
            return True
        if c == ENV_GET_VARIABLE_UPDATE:
            C.cast(data, C.POINTER(C.c_bool))[0] = False
            return True
        if c == ENV_GET_CORE_OPTIONS_VERSION:
            C.cast(data, C.POINTER(C.c_uint))[0] = 0
            return True
        if c in (
            ENV_SET_PERFORMANCE_LEVEL,
            ENV_SET_INPUT_DESCRIPTORS,
            ENV_SET_VARIABLES,
            ENV_SET_SUPPORT_NO_GAME,
            ENV_SET_CORE_OPTIONS,
            ENV_SET_CORE_OPTIONS_INTL,
            ENV_SET_CORE_OPTIONS_V2,
            ENV_SET_CORE_OPTIONS_V2_INTL,
            ENV_SET_MESSAGE,
            ENV_GET_OVERSCAN,
        ):
            return True
        # ENV_GET_VARIABLE, ENV_GET_LOG_INTERFACE, and anything else: decline,
        # which tells the core to use its defaults.
        return False

    def _on_video(self, data: int, width: int, height: int, pitch: int):
        if not data:
            return  # duped frame: keep previous framebuffer
        self._fb = C.string_at(data, pitch * height)
        self._fb_w = width
        self._fb_h = height
        self._fb_pitch = pitch

    def _on_audio_sample(self, left: int, right: int):
        pass

    def _on_audio_batch(self, data, frames: int) -> int:
        return frames

    def _on_input_poll(self):
        pass

    def _on_input_state(self, port: int, device: int, index: int, id: int) -> int:
        if device == RETRO_DEVICE_JOYPAD and index == 0:
            return self._input.get(port, {}).get(id, 0)
        return 0

    # -- public API ----------------------------------------------------------

    def system_info(self) -> retro_system_info:
        info = retro_system_info()
        self._lib.retro_get_system_info(C.byref(info))
        return info

    def av_info(self) -> retro_system_av_info:
        info = retro_system_av_info()
        self._lib.retro_get_system_av_info(C.byref(info))
        return info

    def load_rom(self, rom_path: str) -> None:
        info = self.system_info()
        gi = retro_game_info()
        gi.path = os.fspath(rom_path).encode()
        gi.meta = None
        if info.need_fullpath:
            gi.data = None
            gi.size = 0
        else:
            with open(rom_path, "rb") as f:
                blob = f.read()
            buf = C.create_string_buffer(blob, len(blob))
            self._keep_alive.append(buf)
            gi.data = C.cast(buf, C.c_void_p)
            gi.size = len(blob)
        ok = self._lib.retro_load_game(C.byref(gi))
        if not ok:
            raise RuntimeError(f"retro_load_game failed for {rom_path}")
        self._loaded = True
        for port in (0, 1):
            self._lib.retro_set_controller_port_device(port, RETRO_DEVICE_JOYPAD)

    def run(self, frames: int = 1) -> None:
        for _ in range(frames):
            self._lib.retro_run()

    # input -------------------------------------------------------------------

    def set_input(self, port: int, button_id: int, pressed: bool) -> None:
        self._input.setdefault(port, {})[button_id] = 1 if pressed else 0

    def set_buttons(self, port: int, buttons) -> None:
        """Set the full button state for a port from an iterable of names.

        Any button not listed is released.
        """
        state = {bid: 0 for bid in SNES_BUTTONS.values()}
        for name in buttons:
            key = name.upper()
            if key not in SNES_BUTTONS:
                raise KeyError(f"unknown SNES button: {name}")
            state[SNES_BUTTONS[key]] = 1
        self._input[port] = state

    def clear_input(self, port: Optional[int] = None) -> None:
        if port is None:
            self._input = {0: {}, 1: {}}
        else:
            self._input[port] = {}

    # video -------------------------------------------------------------------

    def get_frame_rgb(self) -> Optional[Tuple[int, int, bytes]]:
        """Return (width, height, rgb888_bytes) of the last frame, or None."""
        if self._fb is None:
            return None
        w, h, pitch, fb = self._fb_w, self._fb_h, self._fb_pitch, self._fb
        out = bytearray(w * h * 3)
        oi = 0
        if self.pixel_format == PIXEL_XRGB8888:
            for y in range(h):
                row = y * pitch
                for x in range(w):
                    p = row + x * 4
                    # little-endian XRGB8888: byte order B,G,R,X
                    out[oi] = fb[p + 2]
                    out[oi + 1] = fb[p + 1]
                    out[oi + 2] = fb[p]
                    oi += 3
        elif self.pixel_format == PIXEL_RGB565:
            for y in range(h):
                row = y * pitch
                for x in range(w):
                    p = row + x * 2
                    px = fb[p] | (fb[p + 1] << 8)
                    r = (px >> 11) & 0x1F
                    g = (px >> 5) & 0x3F
                    b = px & 0x1F
                    out[oi] = (r << 3) | (r >> 2)
                    out[oi + 1] = (g << 2) | (g >> 4)
                    out[oi + 2] = (b << 3) | (b >> 2)
                    oi += 3
        else:  # PIXEL_0RGB1555
            for y in range(h):
                row = y * pitch
                for x in range(w):
                    p = row + x * 2
                    px = fb[p] | (fb[p + 1] << 8)
                    r = (px >> 10) & 0x1F
                    g = (px >> 5) & 0x1F
                    b = px & 0x1F
                    out[oi] = (r << 3) | (r >> 2)
                    out[oi + 1] = (g << 3) | (g >> 2)
                    out[oi + 2] = (b << 3) | (b >> 2)
                    oi += 3
        return w, h, bytes(out)

    # state -------------------------------------------------------------------

    def serialize(self) -> bytes:
        size = self._lib.retro_serialize_size()
        buf = C.create_string_buffer(size)
        if not self._lib.retro_serialize(C.cast(buf, C.c_void_p), size):
            raise RuntimeError("retro_serialize failed")
        return buf.raw

    def unserialize(self, data: bytes) -> None:
        buf = C.create_string_buffer(data, len(data))
        if not self._lib.retro_unserialize(C.cast(buf, C.c_void_p), len(data)):
            raise RuntimeError("retro_unserialize failed")

    # memory (RAM) ----------------------------------------------------------

    def memory_size(self, mem_id: int = RETRO_MEMORY_SYSTEM_RAM) -> int:
        return int(self._lib.retro_get_memory_size(mem_id))

    def _memory_ptr(self, mem_id: int):
        return self._lib.retro_get_memory_data(mem_id)

    def read_ram(self, addr: int, length: int = 1,
                 mem_id: int = RETRO_MEMORY_SYSTEM_RAM) -> bytes:
        """Read `length` bytes of console RAM starting at `addr`."""
        size = self.memory_size(mem_id)
        ptr = self._memory_ptr(mem_id)
        if not ptr or addr < 0 or addr + length > size:
            return b""
        return C.string_at(ptr + addr, length)

    def read_u8(self, addr: int, mem_id: int = RETRO_MEMORY_SYSTEM_RAM) -> int:
        b = self.read_ram(addr, 1, mem_id)
        return b[0] if b else -1

    def read_u16(self, addr: int, mem_id: int = RETRO_MEMORY_SYSTEM_RAM) -> int:
        """Read a little-endian 16-bit value."""
        b = self.read_ram(addr, 2, mem_id)
        return (b[0] | (b[1] << 8)) if len(b) == 2 else -1

    def ram_snapshot(self, mem_id: int = RETRO_MEMORY_SYSTEM_RAM) -> bytes:
        """A full copy of the console's work RAM right now."""
        size = self.memory_size(mem_id)
        ptr = self._memory_ptr(mem_id)
        if not ptr or size <= 0:
            return b""
        return C.string_at(ptr, size)

    def save_state(self, path: str) -> None:
        with open(path, "wb") as f:
            f.write(self.serialize())

    def load_state(self, path: str) -> None:
        with open(path, "rb") as f:
            self.unserialize(f.read())
        # Advance a couple of frames so a fresh framebuffer is produced.
        self.run(2)

    # battery SRAM (the game's own in-game "Save" slots) ----------------------

    def save_sram(self, path: str) -> bool:
        """Write the cartridge's battery SAVE_RAM to disk (the in-game saves).
        Most libretro cores don't persist this themselves — the frontend must."""
        size = self.memory_size(RETRO_MEMORY_SAVE_RAM)
        ptr = self._memory_ptr(RETRO_MEMORY_SAVE_RAM)
        if not ptr or size <= 0:
            return False
        data = C.string_at(ptr, size)
        with open(path, "wb") as f:
            f.write(data)
        return True

    def load_sram(self, path: str) -> bool:
        """Load a previously saved .srm into the cartridge's SAVE_RAM, so the
        game's 'Continue' sees the player's in-game save slots. Call right after
        load_rom, before running."""
        try:
            with open(path, "rb") as f:
                data = f.read()
        except OSError:
            return False
        size = self.memory_size(RETRO_MEMORY_SAVE_RAM)
        ptr = self._memory_ptr(RETRO_MEMORY_SAVE_RAM)
        if not ptr or size <= 0 or not data:
            return False
        C.memmove(ptr, data, min(size, len(data)))
        return True

    # lifecycle ---------------------------------------------------------------

    def close(self) -> None:
        if getattr(self, "_loaded", False):
            self._lib.retro_unload_game()
            self._loaded = False
        self._lib.retro_deinit()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
