import frappe
from frappe.tests.utils import FrappeTestCase

from hees_sola.audio import detect_platform


class TestVocalExtraction(FrappeTestCase):
	def test_detect_platform(self):
		self.assertEqual(detect_platform("https://www.youtube.com/watch?v=abc"), "YouTube")
		self.assertEqual(detect_platform("https://youtu.be/abc"), "YouTube")
		self.assertEqual(detect_platform("https://www.tiktok.com/@user/video/123"), "TikTok")
		self.assertEqual(detect_platform("https://x.com/user/status/1"), "X (Twitter)")
		self.assertEqual(detect_platform("https://example.org/song"), "Other")
		self.assertEqual(detect_platform(""), "Unknown")

	def test_document_defaults(self):
		doc = frappe.get_doc({"doctype": "Vocal Extraction", "source_url": "https://youtu.be/abc"})
		self.assertEqual(doc.status, "Queued")
