"""Whitelisted REST endpoints for vocal extraction.

Two explicit stages, used by the React front-end served at ``/hees_sola``:

1. ``download_audio``    - fetch the audio from a social media URL.
2. ``separate_vocals``   - remove the music, keeping only the words.

Read back the results (playable / downloadable file URLs) with
``get_extraction``.
"""

import frappe

FIELD_LIST = [
	"name",
	"status",
	"platform",
	"title",
	"source_url",
	"audio_file",
	"vocals_file",
	"instrumental_file",
	"error_log",
]


def _serialize(doc):
	return {field: doc.get(field) for field in FIELD_LIST}


@frappe.whitelist(allow_guest=True)
def get_csrf_token():
	"""Hand the browser a CSRF token so it can POST to the API."""
	return {"csrf_token": frappe.sessions.get_csrf_token()}


@frappe.whitelist()
def download_audio(source_url):
	"""Stage 1: create an extraction and download the audio in the background."""
	if not source_url:
		frappe.throw("source_url is required")

	doc = frappe.get_doc({"doctype": "Vocal Extraction", "source_url": source_url}).insert()
	return _serialize(doc)


@frappe.whitelist()
def separate_vocals(name):
	"""Stage 2: remove the music from the already-downloaded audio."""
	doc = frappe.get_doc("Vocal Extraction", name)
	status = doc.separate()
	return {"name": doc.name, "status": status}


@frappe.whitelist()
def get_extraction(name):
	"""Return the current state of an extraction, including output file URLs."""
	return _serialize(frappe.get_doc("Vocal Extraction", name))


@frappe.whitelist()
def list_extractions(limit=8):
	"""Recent extractions for the signed-in user."""
	return frappe.get_list(
		"Vocal Extraction",
		fields=["name", "title", "source_url", "status", "platform", "creation"],
		order_by="creation desc",
		limit_page_length=int(limit) or 8,
	)


@frappe.whitelist()
def whoami():
	"""Small helper so the SPA can tell whether the visitor is signed in."""
	user = frappe.session.user
	return {"user": user, "is_guest": user == "Guest"}
