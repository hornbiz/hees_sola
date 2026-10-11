frappe.ui.form.on("Vocal Extraction", {
	refresh(frm) {
		if (frm.is_new()) return;

		const running = ["Queued", "Downloading", "Separating"].includes(frm.doc.status);

		if (running) {
			frm.dashboard.set_headline(__("Extraction in progress..."));
			frm.add_custom_button(__("Refresh"), () => frm.reload_doc());
			setTimeout(() => frm.reload_doc(), 5000);
		}

		if (frm.doc.status === "Failed") {
			frm.add_custom_button(__("Retry"), () => {
				frm.call("retry").then(() => frm.reload_doc());
			});
		}
	},
});
