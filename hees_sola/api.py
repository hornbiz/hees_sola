"""Whitelisted REST endpoints for vocal extraction.

Used by the React front-end served at ``/hees_sola``. Everything is open to
guests: each extraction carries a secret ``access_key`` that is handed to the
visitor who created it and must accompany every later call.

1. ``download_audio``  - start an extraction from a social media URL.
2. ``separate_vocals`` - remove the music, keeping only the words.
3. ``get_extraction``  - poll status and get the playable / download URLs.
4. ``file``            - stream a finished audio file.
"""

import hmac
from urllib.parse import quote, urlparse

import frappe
from frappe import _
from frappe.rate_limiter import rate_limit

from hees_sola.audio import get_progress

DOCTYPE = "Vocal Extraction"
MAX_UPLOAD_BYTES = 150 * 1024 * 1024
FILE_KINDS = {"vocals": "vocals_file", "audio": "audio_file"}


def _load(name, key):
	"""Return the extraction if ``key`` matches its access key, else deny."""
	stored = frappe.db.get_value(DOCTYPE, name, "access_key") if name else None
	if not stored or not key or not hmac.compare_digest(str(stored), str(key)):
		frappe.throw(_("Extraction not found."), frappe.PermissionError)
	return frappe.get_doc(DOCTYPE, name)


def _file_url(doc, kind):
	if not doc.get(FILE_KINDS[kind]):
		return None
	return f"/api/method/hees_sola.api.file?name={quote(doc.name)}&key={quote(doc.access_key)}&kind={kind}"


def _serialize(doc):
	error = None
	if doc.status == "Failed":
		lines = (doc.error_log or "").strip().splitlines()
		error = lines[-1] if lines else _("Something went wrong.")
	progress = get_progress(doc.name)
	percent = {"Completed": 100}.get(doc.status, progress.get("percent", 0))
	return {
		"name": doc.name,
		"key": doc.access_key,
		"progress": percent,
		"message": progress.get("message"),
		"status": doc.status,
		"platform": doc.platform,
		"title": doc.title,
		"vocals_url": _file_url(doc, "vocals"),
		"error": error,
	}


@frappe.whitelist(allow_guest=True)
def get_csrf_token():
	"""Hand the browser a CSRF token so it can POST to the API."""
	return {"csrf_token": frappe.sessions.get_csrf_token()}


@frappe.whitelist(allow_guest=True, methods=["POST"])
@rate_limit(limit=10, seconds=3600)
def download_audio(source_url, client_download=0):
	"""Stage 1: create an extraction and download the audio in the background.

	With ``client_download`` the caller (the mobile app) fetches the audio itself
	and sends it to ``upload_audio``; call ``download_on_server`` to fall back.
	"""
	source_url = (source_url or "").strip()
	parsed = urlparse(source_url)
	if parsed.scheme not in ("http", "https") or not parsed.hostname:
		frappe.throw(_("Enter a valid link starting with http:// or https://"))

	doc = frappe.get_doc({"doctype": DOCTYPE, "source_url": source_url})
	doc.flags.client_download = bool(int(client_download or 0))
	doc.insert(ignore_permissions=True)
	return _serialize(doc)


@frappe.whitelist(allow_guest=True, methods=["POST"])
def download_on_server(name, key):
	"""Fall back to downloading on the server when the device could not."""
	doc = _load(name, key)
	if doc.status in ("Queued", "Failed") and not doc.audio_file:
		doc.queue_download()
	return _serialize(frappe.get_doc(DOCTYPE, name))


@frappe.whitelist(allow_guest=True, methods=["POST"])
@rate_limit(limit=20, seconds=3600)
def upload_audio(name, key, title=None):
	"""Receive audio the user's device downloaded (multipart field ``file``)."""
	import os
	import tempfile

	from hees_sola.audio import ingest_upload

	doc = _load(name, key)
	if doc.audio_file or doc.status not in ("Queued", "Failed"):
		frappe.throw(_("This extraction already has audio."))

	upload = frappe.request.files.get("file") if frappe.request else None
	if not upload:
		frappe.throw(_("No audio file received."))

	fd, path = tempfile.mkstemp(suffix=".upload")
	try:
		size = 0
		with os.fdopen(fd, "wb") as handle:
			while chunk := upload.stream.read(1024 * 1024):
				size += len(chunk)
				if size > MAX_UPLOAD_BYTES:
					frappe.throw(_("The audio file is too large."))
				handle.write(chunk)
		ingest_upload(doc, path, title)
	finally:
		if os.path.exists(path):
			os.remove(path)
	return _serialize(frappe.get_doc(DOCTYPE, name))


@frappe.whitelist(allow_guest=True, methods=["POST"])
def separate_vocals(name, key):
	"""Stage 2: remove the music from the already-downloaded audio."""
	doc = _load(name, key)
	doc.separate()
	return _serialize(frappe.get_doc(DOCTYPE, name))


@frappe.whitelist(allow_guest=True)
def get_extraction(name, key):
	"""Return the current state of an extraction, including output file URLs."""
	return _serialize(_load(name, key))


@frappe.whitelist(allow_guest=True)
def file(name, key, kind="vocals", download=0):
	"""Stream a finished audio file; inline for playback, attachment for download."""
	from hees_sola.audio import _local_path

	if kind not in FILE_KINDS:
		frappe.throw(_("Unknown file."), frappe.PermissionError)
	doc = _load(name, key)
	url = doc.get(FILE_KINDS[kind])
	if not url:
		frappe.throw(_("File not ready."), frappe.DoesNotExistError)

	with open(_local_path(url), "rb") as handle:
		frappe.response["filecontent"] = handle.read()
	frappe.response["filename"] = f"{doc.name}-{kind}.wav"
	frappe.response["content_type"] = "audio/wav"
	frappe.response["display_content_as"] = "attachment" if int(download) else "inline"
	frappe.response["type"] = "download"
