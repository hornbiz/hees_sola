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

DOCTYPE = "Vocal Extraction"
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
	return {
		"name": doc.name,
		"key": doc.access_key,
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
def download_audio(source_url):
	"""Stage 1: create an extraction and download the audio in the background."""
	source_url = (source_url or "").strip()
	parsed = urlparse(source_url)
	if parsed.scheme not in ("http", "https") or not parsed.hostname:
		frappe.throw(_("Enter a valid link starting with http:// or https://"))

	doc = frappe.get_doc({"doctype": DOCTYPE, "source_url": source_url}).insert(ignore_permissions=True)
	return _serialize(doc)


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
