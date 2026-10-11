import frappe


def has_app_permission():
	"""Show the Hees Sola app on the apps screen to users who can read extractions."""
	if frappe.session.user == "Guest":
		return False
	return bool(frappe.has_permission("Vocal Extraction", "read"))
