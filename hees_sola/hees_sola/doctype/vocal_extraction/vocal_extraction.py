import frappe
from frappe import _
from frappe.model.document import Document

from hees_sola.audio import detect_platform


class VocalExtraction(Document):
	def validate(self):
		if self.source_url:
			self.platform = detect_platform(self.source_url)

	def after_insert(self):
		self.queue_extraction()

	def queue_extraction(self):
		"""Reset the record and hand it off to a background worker."""
		self.db_set(
			{
				"status": "Queued",
				"error_log": None,
				"started_at": None,
				"completed_at": None,
			}
		)
		frappe.enqueue(
			"hees_sola.audio.process_extraction",
			queue="long",
			enqueue_after_commit=True,
			timeout=3600,
			docname=self.name,
		)

	@frappe.whitelist()
	def retry(self):
		"""Re-run an extraction that previously failed or was cancelled."""
		if self.status in ("Downloading", "Separating"):
			frappe.throw(_("Extraction is already running."))
		self.queue_extraction()
		return self.status
