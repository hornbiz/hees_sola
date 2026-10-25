"""Core pipeline: download audio from a social media URL and isolate the vocals.

The work happens in two explicit stages so the UI can show them separately:

1. ``process_download``   - fetch the audio track with ``yt-dlp`` and attach it.
2. ``process_separation`` - split the downloaded audio with ``demucs`` into
   ``vocals`` (the words) and ``no_vocals`` (the music) stems.
"""

import fcntl
import ipaddress
import os
import re
import shutil
import socket
import subprocess
import sys
import time
import traceback
from contextlib import contextmanager
from urllib.parse import urljoin, urlparse

import frappe
from frappe.utils import now_datetime

DEMUCS_MODEL = "htdemucs"
DOWNLOAD_TIMEOUT = 900
SEPARATION_TIMEOUT = 3600
SEPARATION_LOCK_TIMEOUT = 2400
LOW_MEMORY_SEPARATION_TIMEOUT = 5400
FAST_MEMORY_THRESHOLD = 3 * 1024**3 - 128 * 1024**2
FALLBACK_API = "https://loader.to/ajax/download.php"
FALLBACK_TIMEOUT = 300
FALLBACK_MAX_BYTES = 500 * 1024 * 1024
SILENCE_THRESHOLD_DB = -40  # frames quieter than this count as silence
SILENCE_MIN_SECONDS = 1.0  # only gaps longer than this are cut
SILENCE_KEEP_SECONDS = 0.4  # natural pause left in place of a cut gap
CHUNK_SECONDS = 60
CHUNK_OVERLAP = 2.0

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


def ffmpeg_location():
	"""Path to an ffmpeg binary: prefer the system one, else the bundled one.

	``imageio-ffmpeg`` ships a static ffmpeg so the app works without root.
	"""
	system = shutil.which("ffmpeg")
	if system:
		return system
	try:
		import imageio_ffmpeg

		return imageio_ffmpeg.get_ffmpeg_exe()
	except Exception:
		return None


def _cgroup_memory_limit():
	"""Container memory limit in bytes, or None when it cannot be determined."""
	for path in (
		"/sys/fs/cgroup/memory.max",
		"/sys/fs/cgroup/memory/memory.limit_in_bytes",
	):
		try:
			with open(path) as handle:
				value = handle.read().strip()
		except OSError:
			continue
		if value and value != "max":
			try:
				return int(value)
			except ValueError:
				continue
	return None


def _demucs_profile():
	"""Pick split settings that fit the container's memory limit.

	Demucs keeps the whole output tensor in memory, so large chunks need far more
	than the 2 GB a small bench may have. The fast profile is used when the
	container has at least ~3 GB; otherwise use a 2 second split with malloc
	trimming, which is slower but stays below 2 GB.
	"""
	limit = _cgroup_memory_limit()
	forced = (frappe.conf.get("hees_sola_demucs_profile") or "").strip().lower()
	if forced == "lowmem" or (forced != "fast" and limit is not None and limit < FAST_MEMORY_THRESHOLD):
		return {
			"name": "lowmem",
			"segment": 2,
			"overlap": 0,
			"shifts": 0,
			"timeout": LOW_MEMORY_SEPARATION_TIMEOUT,
			"env": {
				"OMP_NUM_THREADS": "1",
				"MKL_NUM_THREADS": "1",
				"MALLOC_ARENA_MAX": "1",
				"MALLOC_TRIM_THRESHOLD_": "131072",
				"MALLOC_MMAP_THRESHOLD_": "131072",
			},
		}
	return {
		"name": "fast",
		"segment": 4,
		"overlap": 0.25,
		"shifts": 0,
		"timeout": SEPARATION_TIMEOUT,
		"env": {
			"OMP_NUM_THREADS": "1",
			"MKL_NUM_THREADS": "1",
			"MALLOC_ARENA_MAX": "1",
		},
	}


@contextmanager
def _demucs_lock(timeout):
	"""Only one Demucs process per container; two would exhaust the memory limit."""
	path = "/tmp/hees_sola_demucs.lock"
	handle = os.open(path, os.O_CREAT | os.O_RDWR, 0o600)
	deadline = time.monotonic() + timeout
	try:
		while True:
			try:
				fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
				break
			except BlockingIOError:
				if time.monotonic() >= deadline:
					raise RuntimeError(
						"Another vocal extraction is already running. Please retry in a few minutes."
					)
				time.sleep(5)
		yield
	finally:
		try:
			fcntl.flock(handle, fcntl.LOCK_UN)
		finally:
			os.close(handle)


def _run(cmd, timeout, env_extra=None):
	env = os.environ.copy()
	env["TORCHAUDIO_USE_BACKEND_DISPATCHER"] = "0"
	env["TORCHAUDIO_BACKEND"] = "soundfile"
	if env_extra:
		env.update(env_extra)
	proc = subprocess.run(
		cmd,
		stdout=subprocess.PIPE,
		stderr=subprocess.STDOUT,
		text=True,
		timeout=timeout,
		env=env,
	)
	if proc.returncode != 0:
		raise RuntimeError(
			f"Command failed ({proc.returncode}): {' '.join(cmd)}\n{proc.stdout[-4000:]}"
		)
	return proc.stdout


def _cookies_path():
	return frappe.conf.get("hees_sola_cookies_file") or frappe.get_site_path(
		"private", "hees_sola_cookies.txt"
	)


_COOKIE_RECORD = re.compile(
	r"(#HttpOnly_)?(\.?[A-Za-z0-9.-]+)\s+(TRUE|FALSE)\s+(\S+)\s+(TRUE|FALSE)\s+(\d+)\s+(\S+)\s+(\S+)"
)


def _normalise_cookies(text):
	"""Rebuild a Netscape cookies.txt whose line breaks were flattened to spaces.

	Config editors often collapse the pasted file onto a single line while
	leaving the tabs between fields intact; that makes yt-dlp treat the whole
	file as one comment and ignore every cookie. If there are fewer lines than
	cookie records, re-emit one record per line.
	"""
	if "\n" not in text:
		text = text.replace("\\n", "\n")
	records = _COOKIE_RECORD.findall(text)
	if not records or text.count("\n") >= len(records):
		return text
	lines = ["# Netscape HTTP Cookie File"]
	for prefix, domain, subdomains, path, secure, expiry, name, value in records:
		lines.append("\t".join([(prefix or "") + domain, subdomains, path, secure, expiry, name, value]))
	return "\n".join(lines) + "\n"


def _cookies_file(workdir):
	"""Netscape-format cookies.txt for sites that demand a signed-in session, if configured.

	Either the text itself in site config ``hees_sola_cookies`` (works on managed
	hosting without file access) or a file at ``hees_sola_cookies_file``.
	"""
	text = frappe.conf.get("hees_sola_cookies")
	if text:
		path = os.path.join(workdir, "cookies.txt")
		with open(path, "w") as handle:
			handle.write(_normalise_cookies(text))
		return path
	path = _cookies_path()
	return path if os.path.exists(path) else None


def _loader_fallback_enabled():
	return str(frappe.conf.get("hees_sola_loader_fallback", "1")).lower() not in (
		"0",
		"false",
		"no",
		"off",
	)


def _public_https_url(url):
	parsed = urlparse(url)
	if parsed.scheme != "https" or not parsed.hostname:
		return False
	try:
		addresses = {entry[4][0] for entry in socket.getaddrinfo(parsed.hostname, None)}
	except OSError:
		return False
	for address in addresses:
		try:
			ip = ipaddress.ip_address(address.split("%")[0])
		except ValueError:
			return False
		if not ip.is_global:
			return False
	return True


def _safe_get(url, **kwargs):
	import requests

	for _ in range(6):
		if not _public_https_url(url):
			raise RuntimeError(f"Refusing unsafe fallback downloader URL: {url}")
		response = requests.get(url, allow_redirects=False, **kwargs)
		if not response.is_redirect:
			return response
		target = urljoin(url, response.headers.get("Location", ""))
		response.close()
		url = target
	raise RuntimeError("The fallback downloader redirected too many times.")


def _is_bot_block(message):
	lowered = message.lower()
	return any(
		marker in lowered
		for marker in (
			"confirm you",
			"sign in",
			"login_required",
			"not a bot",
			"http error 403",
		)
	)


def _download_via_loader(url, workdir):
	"""Fetch a YouTube track through the loader.to fallback API."""
	headers = {
		"User-Agent": (
			"Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
			"(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
		),
		"Accept": "application/json, text/plain, */*",
	}

	try:
		response = _safe_get(
			FALLBACK_API,
			params={"format": "mp3", "url": url},
			headers=headers,
			timeout=(10, 30),
		)
		response.raise_for_status()
		payload = response.json()
	except Exception as exc:
		raise RuntimeError(f"The fallback downloader could not start: {exc}") from exc

	if not payload.get("success"):
		detail = payload.get("message") or payload.get("text") or payload
		raise RuntimeError(f"The fallback downloader rejected the URL: {detail}")

	progress_url = payload.get("progress_url")
	if not progress_url:
		raise RuntimeError("The fallback downloader did not return a progress URL.")

	title = (payload.get("info") or {}).get("title") or payload.get("title")
	deadline = time.monotonic() + FALLBACK_TIMEOUT
	download_url = None
	while time.monotonic() < deadline:
		try:
			progress = _safe_get(progress_url, headers=headers, timeout=(10, 30)).json()
		except Exception:
			progress = {}
		if progress.get("success") == 1 and progress.get("download_url"):
			download_url = progress["download_url"]
			title = (progress.get("info") or {}).get("title") or title
			break
		text = str(progress.get("text") or "").lower()
		if "error" in text or "fail" in text:
			raise RuntimeError(f"The fallback downloader failed: {progress.get('text')}")
		time.sleep(2)

	if not download_url:
		raise RuntimeError("The fallback downloader did not finish in time.")

	path = os.path.join(workdir, "fallback.audio")
	response = None
	try:
		response = _safe_get(download_url, headers=headers, stream=True, timeout=(10, 60))
		response.raise_for_status()
		length = response.headers.get("Content-Length")
		if length and length.isdigit() and int(length) > FALLBACK_MAX_BYTES:
			raise RuntimeError("The fallback audio file is too large.")
		written = 0
		with open(path, "wb") as handle:
			for chunk in response.iter_content(1024 * 256):
				if not chunk:
					continue
				written += len(chunk)
				if written > FALLBACK_MAX_BYTES:
					raise RuntimeError("The fallback audio file is too large.")
				handle.write(chunk)
	finally:
		if response is not None:
			response.close()

	ffmpeg = ffmpeg_location()
	if not ffmpeg:
		raise RuntimeError("ffmpeg is required to convert the fallback audio.")

	output = os.path.join(workdir, "source.wav")
	_run(
		[
			ffmpeg,
			"-y",
			"-hide_banner",
			"-loglevel",
			"error",
			"-i",
			path,
			"-vn",
			"-acodec",
			"pcm_s16le",
			output,
		],
		timeout=300,
	)
	os.remove(path)
	frappe.logger("hees_sola").info("Downloaded %s using the loader.to fallback", url)
	return output, title


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
		"extractor_args": {"youtube": {"player_client": ["web", "mweb", "android"]}},
		"geo_bypass": True,
		"nocheckcertificate": True,
		"postprocessors": [
			{"key": "FFmpegExtractAudio", "preferredcodec": "wav", "preferredquality": "0"}
		],
	}

	ffmpeg = ffmpeg_location()
	if ffmpeg:
		opts["ffmpeg_location"] = ffmpeg

	cookies = _cookies_file(workdir)
	if cookies:
		opts["cookiefile"] = cookies
	proxy = _proxy_url()
	if proxy:
		opts["proxy"] = proxy
	node = shutil.which("node")
	if node:
		opts["js_runtimes"] = {"node": {"path": node}}

	try:
		with yt_dlp.YoutubeDL(opts) as ydl:
			info = ydl.extract_info(url, download=True)
	except yt_dlp.utils.DownloadError as exc:
		message = str(exc)
		if _loader_fallback_enabled() and detect_platform(url) == "YouTube" and _is_bot_block(message):
			try:
				return _download_via_loader(url, workdir)
			except Exception as fallback_exc:
				raise RuntimeError(
					"YouTube blocked this server as a bot and the fallback downloader also "
					f"failed: {fallback_exc}. Set hees_sola_proxy or fresh hees_sola_cookies, "
					"or disable hees_sola_loader_fallback."
				) from exc
		if "confirm you" in message and "bot" in message:
			if cookies:
				raise RuntimeError(
					"YouTube blocked this server as a bot. The configured cookies could not "
					"authenticate the request; they may be expired, revoked or not from a "
					"signed-in YouTube session. Re-export them into hees_sola_cookies or set "
					"hees_sola_proxy."
				) from exc
			raise RuntimeError(
				"YouTube blocked this server as a bot. An admin must add YouTube cookies "
				"(site config key hees_sola_cookies), set hees_sola_proxy, or enable the "
				"loader.to fallback."
			) from exc
		raise

	audio_path = os.path.join(workdir, "source.wav")
	if not os.path.exists(audio_path):
		candidates = [
			os.path.join(workdir, name) for name in os.listdir(workdir) if name.startswith("source.")
		]
		if not candidates:
			raise RuntimeError("Download finished but no audio file was produced.")
		audio_path = candidates[0]

	return audio_path, info.get("title")


def _audio_duration(path):
	try:
		import soundfile as sf

		return sf.info(path).duration
	except Exception:
		return None


def _demucs_cmd(audio_path, output_dir, profile):
	return [
		sys.executable,
		os.path.join(os.path.dirname(os.path.abspath(__file__)), "demucs_runner.py"),
		"--two-stems=vocals",
		"-n",
		DEMUCS_MODEL,
		"--segment",
		str(profile["segment"]),
		"--overlap",
		str(profile["overlap"]),
		"--shifts",
		str(profile["shifts"]),
		"--out",
		output_dir,
		"--device",
		"cpu",
		audio_path,
	]


def _stitch(chunk_paths, output, ffmpeg):
	"""Join Demucs chunk stems with a short crossfade at each boundary."""
	os.makedirs(os.path.dirname(output), exist_ok=True)
	if len(chunk_paths) == 1:
		shutil.copyfile(chunk_paths[0], output)
		return

	cmd = [ffmpeg, "-y", "-hide_banner", "-loglevel", "error"]
	for path in chunk_paths:
		cmd.extend(["-i", path])
	parts = []
	previous = "[0:a]"
	for index in range(1, len(chunk_paths)):
		label = f"[a{index}]"
		parts.append(
			f"{previous}[{index}:a]acrossfade=d={CHUNK_OVERLAP}:c1=tri:c2=tri{label}"
		)
		previous = label
	cmd.extend(
		[
			"-filter_complex",
			";".join(parts),
			"-map",
			previous,
			"-c:a",
			"pcm_s16le",
			output,
		]
	)
	_run(cmd, timeout=600)


def _separate_vocals(audio_path, workdir):
	"""Run Demucs two-stem separation and return (vocals, instrumental) paths.

	Long tracks are split into overlapping chunks. Demucs keeps the whole output
	tensor in memory, so a single long run can exceed a 3 GB container even when a
	short run fits; separate processes keep the peak bounded by one chunk.
	"""
	_require("demucs", "demucs")

	profile = _demucs_profile()
	frappe.logger("hees_sola").info(
		"Demucs profile %s (cgroup limit %s)", profile["name"], _cgroup_memory_limit()
	)

	output_dir = os.path.join(workdir, "separated")
	track = os.path.splitext(os.path.basename(audio_path))[0]
	stem_dir = os.path.join(output_dir, DEMUCS_MODEL, track)
	vocals = os.path.join(stem_dir, "vocals.wav")
	instrumental = os.path.join(stem_dir, "no_vocals.wav")

	duration = _audio_duration(audio_path)
	ffmpeg = ffmpeg_location()
	chunk_paths = []
	if ffmpeg and duration and duration > CHUNK_SECONDS + CHUNK_OVERLAP:
		starts = []
		start = 0.0
		while start < duration:
			starts.append(start)
			if start + CHUNK_SECONDS >= duration:
				break
			start += CHUNK_SECONDS - CHUNK_OVERLAP

		for index, chunk_start in enumerate(starts):
			length = min(CHUNK_SECONDS, duration - chunk_start)
			chunk_path = os.path.join(workdir, f"chunk_{index:03d}.wav")
			_run(
				[
					ffmpeg,
					"-y",
					"-hide_banner",
					"-loglevel",
					"error",
					"-ss",
					f"{chunk_start:.3f}",
					"-t",
					f"{length:.3f}",
					"-i",
					audio_path,
					"-c:a",
					"pcm_s16le",
					"-ar",
					"44100",
					"-ac",
					"2",
					chunk_path,
				],
				timeout=300,
			)
			chunk_paths.append(chunk_path)

	frappe.logger("hees_sola").info(
		"Separating %s (%.1fs) in %s chunk(s)", audio_path, duration or 0, len(chunk_paths) or 1
	)

	with _demucs_lock(SEPARATION_LOCK_TIMEOUT):
		if not chunk_paths:
			_run(
				_demucs_cmd(audio_path, output_dir, profile),
				timeout=profile["timeout"],
				env_extra=profile["env"],
			)
		else:
			for chunk_path in chunk_paths:
				_run(
					_demucs_cmd(chunk_path, output_dir, profile),
					timeout=profile["timeout"],
					env_extra=profile["env"],
				)

			chunk_names = [os.path.splitext(os.path.basename(path))[0] for path in chunk_paths]
			chunk_vocals = [
				os.path.join(output_dir, DEMUCS_MODEL, name, "vocals.wav")
				for name in chunk_names
			]
			chunk_instrumentals = [
				os.path.join(output_dir, DEMUCS_MODEL, name, "no_vocals.wav")
				for name in chunk_names
			]
			_stitch(chunk_vocals, vocals, ffmpeg)
			if all(os.path.exists(path) for path in chunk_instrumentals):
				_stitch(chunk_instrumentals, instrumental, ffmpeg)

	if not os.path.exists(vocals):
		raise RuntimeError(f"Demucs did not produce a vocals stem in {stem_dir}.")

	return vocals, instrumental if os.path.exists(instrumental) else None


def _trim_silence(path):
	"""Cut long silent gaps out of a wav file in place, keeping a short pause."""
	import numpy as np
	import soundfile as sf

	data, rate = sf.read(path, dtype="float32", always_2d=True)
	frame = max(1, int(rate * 0.05))
	count = len(data) // frame
	if count == 0:
		return

	frames = data[: count * frame].reshape(count, frame, -1)
	rms = np.sqrt((frames**2).mean(axis=(1, 2)))
	silent = rms < 10 ** (SILENCE_THRESHOLD_DB / 20)

	min_frames = int(SILENCE_MIN_SECONDS / 0.05)
	keep_frames = int(SILENCE_KEEP_SECONDS / 0.05)
	keep = np.ones(count, dtype=bool)

	start = None
	for i in range(count + 1):
		if i < count and silent[i]:
			if start is None:
				start = i
			continue
		if start is not None and i - start > min_frames:
			keep[start + keep_frames : i] = False
		start = None

	if keep.all():
		return

	out = frames[keep].reshape(-1, data.shape[1])
	out = np.concatenate([out, data[count * frame :]])
	sf.write(path, out, rate, subtype="PCM_16")


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
	)
	try:
		file_doc.check_max_file_size = lambda: None
	except Exception:
		pass
	file_doc.flags.ignore_max_file_size = True
	file_doc.insert(ignore_permissions=True)
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


def ingest_upload(doc, upload_path, title=None):
	"""Convert audio fetched by the user's own device to WAV and attach it."""
	workdir = _workdir(doc.name)
	try:
		wav_path = os.path.join(workdir, "uploaded.wav")
		ffmpeg = ffmpeg_location()
		if not ffmpeg:
			raise RuntimeError("ffmpeg is not available on the server.")
		proc = subprocess.run(
			[ffmpeg, "-y", "-i", upload_path, "-vn", "-ac", "2", "-ar", "44100", wav_path],
			stdout=subprocess.PIPE,
			stderr=subprocess.PIPE,
			timeout=600,
		)
		if proc.returncode != 0 or not os.path.exists(wav_path):
			raise RuntimeError("The uploaded file is not valid audio.")
		if title:
			frappe.db.set_value("Vocal Extraction", doc.name, "title", title[:140])
		_attach(doc, "audio_file", wav_path, f"{doc.name}-original.wav")
		_set_status(doc.name, "Downloaded", started_at=now_datetime(), error_log=None)
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

		_trim_silence(vocals_path)
		_attach(doc, "vocals_file", vocals_path, f"{docname}-vocals.wav")
		_attach(doc, "instrumental_file", instrumental_path, f"{docname}-instrumental.wav")
		_set_status(docname, "Completed", completed_at=now_datetime())
	except Exception:
		error = traceback.format_exc()
		frappe.log_error(title=f"Vocal Extraction separation failed: {docname}", message=error)
		_set_status(docname, "Failed", error_log=error, completed_at=now_datetime())
	finally:
		shutil.rmtree(workdir, ignore_errors=True)
