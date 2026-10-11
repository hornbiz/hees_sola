"""Core pipeline: download audio from a social media URL and isolate the vocals.

The work happens in two explicit stages so the UI can show them separately:

1. ``process_download``   - fetch the audio track with ``yt-dlp`` and attach it.
2. ``process_separation`` - split the downloaded audio with ``demucs`` into
   ``vocals`` (the words) and ``no_vocals`` (the music) stems.
"""

import os
import shutil
import subprocess
import sys
import traceback
from urllib.parse import urlparse

import frappe
from frappe.utils import now_datetime

DEMUCS_MODEL = "htdemucs"
DOWNLOAD_TIMEOUT = 900
SEPARATION_TIMEOUT = 2400

_PLATFORM_HOSTS = {
	"youtube.com": "YouTube",
	"youtu.be": "YouTube",
	"music.youtube.com": "YouTube",
	"tiktok.com": "TikTok",
	"instagram.com": "Instagram",
	"facebook.com": "Facebook",
	"fb.watch": "Facebook",
	"twitter.com": "X (Twitter)",
	"x.com": "X (Twitter)",
	"soundcloud.com": "SoundCloud",
	"vimeo.com": "Vimeo",
}


def detect_platform(url):
	"""Best-effort platform label derived from the URL host."""
	if not url:
		return "Unknown"
	host = (urlparse(url).hostname or "").lower()
	if host.startswith("www."):
		host = host[4:]
	if not host:
		return "Unknown"
	for domain, label in _PLATFORM_HOSTS.items():
		if host == domain or host.endswith("." + domain):
			return label
	return "Other"


def _workdir(docname):
	path = frappe.get_site_path("private", "files", "hees_sola", docname)
	os.makedirs(path, exist_ok=True)
	return path


def _require(module, package):
	try:
		return __import__(module)
	except ImportError:
		frappe.throw(
			f"'{module}' is required to run extractions. Install it with: pip install {package}"
		)


def _run(cmd, timeout):
	proc = subprocess.run(
		cmd,
		stdout=subprocess.PIPE,
		stderr=subprocess.STDOUT,
		text=True,
		timeout=timeout,
	)
	if proc.returncode != 0:
		raise RuntimeError(
			f"Command failed ({proc.returncode}): {' '.join(cmd)}\n{proc.stdout[-4000:]}"
		)
	return proc.stdout


def _download_audio(url, workdir):
	"""Download the best available audio track and normalise it to WAV."""
	_require("yt_dlp", "yt-dlp")

	import yt_dlp

	opts = {
		"format": "bestaudio/best",
		"outtmpl": os.path.join(workdir, "source.%(ext)s"),
		"noplaylist": True,
		"quiet": True,
		"no_warnings": True,
		"restrictfilenames": True,
		"socket_timeout": DOWNLOAD_TIMEOUT,
		"postprocessors": [
			{"key": "FFmpegExtractAudio", "preferredcodec": "wav", "preferredquality": "0"}
		],
	}

	with yt_dlp.YoutubeDL(opts) as ydl:
		info = ydl.extract_info(url, download=True)

	audio_path = os.path.join(workdir, "source.wav")
	if not os.path.exists(audio_path):
		candidates = [
			os.path.join(workdir, name) for name in os.listdir(workdir) if name.startswith("source.")
		]
		if not candidates:
			raise RuntimeError("Download finished but no audio file was produced.")
		audio_path = candidates[0]

	return audio_path, info.get("title")


def _separate_vocals(audio_path, workdir):
	"""Run Demucs two-stem separation and return (vocals, instrumental) paths."""
	_require("demucs", "demucs")

	output_dir = os.path.join(workdir, "separated")
	cmd = [
		sys.executable,
		"-m",
		"demucs",
		"--two-stems=vocals",
		"-n",
		DEMUCS_MODEL,
		"--out",
		output_dir,
		audio_path,
	]
	_run(cmd, timeout=SEPARATION_TIMEOUT)

	track = os.path.splitext(os.path.basename(audio_path))[0]
	stem_dir = os.path.join(output_dir, DEMUCS_MODEL, track)
	vocals = os.path.join(stem_dir, "vocals.wav")
	instrumental = os.path.join(stem_dir, "no_vocals.wav")

	if not os.path.exists(vocals):
		raise RuntimeError(f"Demucs did not produce a vocals stem in {stem_dir}.")

	return vocals, instrumental if os.path.exists(instrumental) else None


def _attach(doc, fieldname, path, filename):
	if not path or not os.path.exists(path):
		return None
	with open(path, "rb") as handle:
		content = handle.read()
	file_doc = frappe.get_doc(
		{
			"doctype": "File",
			"file_name": filename,
			"is_private": 1,
			"attached_to_doctype": doc.doctype,
			"attached_to_name": doc.name,
			"content": content,
		}
	).insert(ignore_permissions=True)
	doc.db_set(fieldname, file_doc.file_url)
	return file_doc.file_url


def _local_path(file_url):
	name = frappe.db.get_value("File", {"file_url": file_url}, "name")
	if not name:
		raise RuntimeError(f"Attached file not found: {file_url}")
	return frappe.get_doc("File", name).get_full_path()


def _set_status(docname, status, **fields):
	frappe.db.set_value("Vocal Extraction", docname, {"status": status, **fields})
	frappe.db.commit()


def process_download(docname):
	"""Stage 1: download the audio from the source URL and attach it."""
	doc = frappe.get_doc("Vocal Extraction", docname)
	workdir = _workdir(docname)

	try:
		_set_status(docname, "Downloading", started_at=now_datetime(), error_log=None)
		audio_path, title = _download_audio(doc.source_url, workdir)
		if title:
			frappe.db.set_value("Vocal Extraction", docname, "title", title)
		_attach(doc, "audio_file", audio_path, f"{docname}-original.wav")
		_set_status(docname, "Downloaded")
	except Exception:
		error = traceback.format_exc()
		frappe.log_error(title=f"Vocal Extraction download failed: {docname}", message=error)
		_set_status(docname, "Failed", error_log=error, completed_at=now_datetime())
	finally:
		shutil.rmtree(workdir, ignore_errors=True)


def process_separation(docname):
	"""Stage 2: remove the music from the previously downloaded audio."""
	doc = frappe.get_doc("Vocal Extraction", docname)
	workdir = _workdir(docname)

	try:
		if not doc.audio_file:
			raise RuntimeError("No downloaded audio found. Run the download step first.")

		_set_status(docname, "Separating", error_log=None)
		source = _local_path(doc.audio_file)
		audio_path = os.path.join(workdir, "source.wav")
		shutil.copy(source, audio_path)

		vocals_path, instrumental_path = _separate_vocals(audio_path, workdir)

		_attach(doc, "vocals_file", vocals_path, f"{docname}-vocals.wav")
		_attach(doc, "instrumental_file", instrumental_path, f"{docname}-instrumental.wav")
		_set_status(docname, "Completed", completed_at=now_datetime())
	except Exception:
		error = traceback.format_exc()
		frappe.log_error(title=f"Vocal Extraction separation failed: {docname}", message=error)
		_set_status(docname, "Failed", error_log=error, completed_at=now_datetime())
	finally:
		shutil.rmtree(workdir, ignore_errors=True)
