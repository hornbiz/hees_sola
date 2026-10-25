import frappe
from frappe import _
from frappe.model.document import Document

from hees_sola.audio import detect_platform


class VocalExtraction(Document):
	def validate(self):
		if self.source_url:
			self.platform = detect_platform(self.source_url)

	def before_insert(self):
		self.access_key = frappe.generate_hash(length=24)

	def after_insert(self):
		self.queue_download()

	def queue_download(self):
		"""Reset the record and download the audio in a background worker."""
		self.db_set(
			{
				"status": "Queued",
				"error_log": None,
				"started_at": None,
				"completed_at": None,
			}
		)
		frappe.enqueue(
			"hees_sola.audio.process_download",
			queue="long",
			enqueue_after_commit=True,
			timeout=3600,
			docname=self.name,
		)

	@frappe.whitelist()
	def download(self):
		"""(Re)run stage 1 for an extraction."""
		if self.status == "Downloading":
			frappe.throw(_("The download is already running."))
		self.queue_download()
		return self.status

	@frappe.whitelist()
	def separate(self):
		"""Stage 2: remove the music from the downloaded audio."""
		if not self.audio_file:
			frappe.throw(_("Download the audio first."))
		if self.status == "Separating":
			frappe.throw(_("Removing the music is already running."))

		self.db_set({"status": "Separating", "error_log": None, "completed_at": None})
		frappe.enqueue(
			"hees_sola.audio.process_separation",
			queue="long",
			enqueue_after_commit=True,
			timeout=7200,
			docname=self.name,
		)
		return self.status
