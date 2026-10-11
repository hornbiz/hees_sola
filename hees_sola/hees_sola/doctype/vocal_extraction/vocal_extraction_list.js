frappe.listview_settings["Vocal Extraction"] = {
	get_indicator(doc) {
		const colors = {
			Queued: "gray",
			Downloading: "orange",
			Separating: "blue",
			Completed: "green",
			Failed: "red",
		};
		return [__(doc.status), colors[doc.status] || "gray", "status,=," + doc.status];
	},
};
