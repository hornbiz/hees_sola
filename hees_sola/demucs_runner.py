"""Run Demucs with soundfile-backed torchaudio I/O.

torchaudio >= 2.9 routes load/save through TorchCodec, which needs system
FFmpeg shared libraries. Patch them with soundfile so Demucs works without root.
"""

import runpy
import sys

import soundfile as sf
import torch
import torchaudio


def _load(uri, *args, **kwargs):
	data, sr = sf.read(str(uri), dtype="float32", always_2d=True)
	return torch.from_numpy(data.T.copy()), sr


def _save(uri, src, sample_rate, *args, **kwargs):
	data = src.detach().cpu().numpy().T
	bits = kwargs.get("bits_per_sample")
	subtype = "PCM_24" if bits == 24 else "FLOAT" if bits == 32 and kwargs.get("encoding") == "PCM_F" else "PCM_16"
	sf.write(str(uri), data, sample_rate, subtype=subtype)


torchaudio.load = _load
torchaudio.save = _save

if __name__ == "__main__":
	sys.argv[0] = "demucs"
	runpy.run_module("demucs", run_name="__main__")
