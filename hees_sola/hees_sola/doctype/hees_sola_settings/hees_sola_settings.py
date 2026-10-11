"""Hees Sola settings (singleton).

Holds the YouTube cookies file and optional proxy used by the extractor. Both
exist because YouTube refuses downloads from datacenter IPs with a "confirm
you're not a bot" login wall; a logged-in cookies file (and/or a proxy with a
clean IP) is the only reliable way through.
"""

from frappe.model.document import Document


class HeesSolaSettings(Document):
	pass
