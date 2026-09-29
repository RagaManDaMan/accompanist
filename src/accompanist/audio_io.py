"""Live audio and WAV files. With midi_io.py, the only modules that touch hardware.

Devices are found by (case-insensitive) substring of their name, as MIDI ports are; the
channel of a multi-input interface is 1-based, as printed on it.
"""
from __future__ import annotations

import queue
import time
import wave
from pathlib import Path
from typing import Optional

import numpy as np

from .config import ConfigError, InputCfg


class AudioError(RuntimeError):
    pass


def _sd():
    try:
        import sounddevice
    except (ImportError, OSError) as e:
        raise AudioError("No audio input library: pip install sounddevice") from e
    return sounddevice


def list_inputs() -> list[tuple[str, int, float]]:
    """[(name, input channels, default sample rate)] of devices that can record."""
    sd = _sd()
    return [(d["name"], d["max_input_channels"], d["default_samplerate"])
            for d in sd.query_devices() if d["max_input_channels"] > 0]


def find_input(wanted: str) -> tuple[int, str, int, float]:
    """(device index, name, input channels, default sample rate) matching `wanted`."""
    sd = _sd()
    devices = [(i, d) for i, d in enumerate(sd.query_devices()) if d["max_input_channels"] > 0]
    matches = [(i, d) for i, d in devices if wanted.lower() in d["name"].lower()]
    listing = "\n  ".join(d["name"] for _, d in devices) or "(none found)"
    if not matches:
        raise AudioError(f"No audio input matching '{wanted}'. Available:\n  {listing}")
    if len(matches) > 1:
        exact = [m for m in matches if m[1]["name"].lower() == wanted.lower()]
        if len(exact) != 1:
            raise AudioError(f"'{wanted}' matches several audio inputs; be more specific:\n  "
                             + "\n  ".join(d["name"] for _, d in matches))
        matches = exact
    i, d = matches[0]
    return i, d["name"], d["max_input_channels"], d["default_samplerate"]


class AudioInput:
    """One channel of an audio interface. Blocks land on `q` as (time of first sample, samples)."""

    def __init__(self, icfg: InputCfg, q: "queue.Queue", block: int = 256) -> None:
        sd = _sd()
        index, self.name, channels, rate = find_input(icfg.audio)
        ch = icfg.audio_channel
        if not 1 <= ch <= channels:
            raise AudioError(f"'{self.name}' has {channels} input channel(s); audio_channel = {ch}")
        self.sample_rate = float(rate)
        self.icfg = icfg

        def callback(indata, frames, time_info, status):
            # adc time is on PortAudio's clock; convert to ours by its offset from 'now'.
            lag = time_info.currentTime - time_info.inputBufferAdcTime
            q.put((time.monotonic() - max(lag, 0.0), icfg, indata[:, ch - 1].copy()))

        self._stream = sd.InputStream(device=index, channels=channels, samplerate=rate,
                                      blocksize=block, dtype="float32", callback=callback)
        self._stream.start()

    def close(self) -> None:
        self._stream.stop()
        self._stream.close()


def read_wav(path: str | Path, channel: int = 1) -> tuple[np.ndarray, float]:
    """(mono float32 samples of `channel` (1-based), sample rate). PCM 16/24/32-bit."""
    p = Path(path)
    if not p.is_file():
        raise ConfigError(f"audio file not found: {p}")
    try:
        with wave.open(str(p), "rb") as w:
            n_ch, width, rate, n = w.getnchannels(), w.getsampwidth(), w.getframerate(), w.getnframes()
            raw = w.readframes(n)
    except (wave.Error, EOFError) as e:
        raise ConfigError(f"{p}: not a PCM WAV file ({e}); export as 16- or 24-bit WAV") from None
    if not 1 <= channel <= n_ch:
        raise ConfigError(f"{p} has {n_ch} channel(s); asked for channel {channel}")
    if width == 3:                                        # 24-bit: widen to 32
        b = np.frombuffer(raw, dtype=np.uint8).reshape(-1, 3)
        data = (b[:, 0].astype(np.int32) | (b[:, 1].astype(np.int32) << 8)
                | (b[:, 2].astype(np.int32) << 16))
        data = np.where(data >= 1 << 23, data - (1 << 24), data).astype(np.float32) / (1 << 23)
    elif width in (1, 2, 4):
        dtype = {1: np.uint8, 2: np.int16, 4: np.int32}[width]
        data = np.frombuffer(raw, dtype=dtype).astype(np.float32)
        data = (data - 128) / 128 if width == 1 else data / float(2 ** (8 * width - 1))
    else:
        raise ConfigError(f"{p}: unsupported sample width {width * 8} bits")
    return data.reshape(-1, n_ch)[:, channel - 1].copy(), float(rate)


class WavWriter:
    """Mono 16-bit WAV, written as it comes in (so a crash loses little)."""

    def __init__(self, path: str | Path, rate: float) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._w = wave.open(str(self.path), "wb")
        self._w.setnchannels(1)
        self._w.setsampwidth(2)
        self._w.setframerate(int(rate))

    def write(self, samples: np.ndarray) -> None:
        self._w.writeframes((np.clip(samples, -1, 1) * 32767).astype("<i2").tobytes())

    def close(self) -> None:
        self._w.close()


def write_wav(path: str | Path, samples: np.ndarray, rate: float) -> None:
    w = WavWriter(path, rate)
    w.write(samples)
    w.close()
