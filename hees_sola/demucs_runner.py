"""Run Demucs with soundfile-backed torchaudio I/O when torchaudio is available.

Demucs separation reads audio through ffmpeg and soundfile. When torchaudio is
installed, patch its load/save helpers to use soundfile as well, because
torchaudio >= 2.9 routes them through TorchCodec, which needs system FFmpeg
shared libraries. The patch is skipped when torchaudio is not installed.
"""

import os
import runpy
import sys

import soundfile as sf
import torch

try:
	torch.set_num_threads(int(os.environ.get("DEMUCS_THREADS", "1")))
except Exception:
	pass
try:
	torch.set_num_interop_threads(1)
except Exception:
	pass

try:
	import torchaudio
except ImportError:
	torchaudio = None


def _load(uri, *args, **kwargs):
	data, sr = sf.read(str(uri), dtype="float32", always_2d=True)
	return torch.from_numpy(data.T.copy()), sr


def _save(uri, src, sample_rate, *args, **kwargs):
	data = src.detach().cpu().numpy().T
	bits = kwargs.get("bits_per_sample")
	subtype = "PCM_24" if bits == 24 else "FLOAT" if bits == 32 and kwargs.get("encoding") == "PCM_F" else "PCM_16"
	sf.write(str(uri), data, sample_rate, subtype=subtype)


if torchaudio is not None:
	torchaudio.load = _load
	torchaudio.save = _save

if __name__ == "__main__":
	sys.argv[0] = "demucs"
	runpy.run_module("demucs", run_name="__main__")
