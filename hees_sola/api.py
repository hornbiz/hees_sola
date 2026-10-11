"""Whitelisted REST endpoints for vocal extraction.

Example calls (POST required because they create documents)::

    POST /api/method/hees_sola.api.extract_vocals
         source_url=https://www.youtube.com/watch?v=...

    POST /api/method/hees_sola.api.get_extraction
         name=HS-00001
"""

import frappe


@frappe.whitelist()
def extract_vocals(source_url):
	"""Create an extraction job for the given media URL and queue it."""
	if not source_url:
		frappe.throw("source_url is required")

	doc = frappe.get_doc({"doctype": "Vocal Extraction", "source_url": source_url}).insert()

	return {"name": doc.name, "status": doc.status, "platform": doc.platform}


@frappe.whitelist()
def get_extraction(name):
	"""Return the current state of an extraction, including output file URLs."""
	doc = frappe.get_doc("Vocal Extraction", name)
	return {
		"name": doc.name,
		"status": doc.status,
		"platform": doc.platform,
		"title": doc.title,
		"vocals_file": doc.vocals_file,
		"instrumental_file": doc.instrumental_file,
		"audio_file": doc.audio_file,
		"error_log": doc.error_log,
	}
