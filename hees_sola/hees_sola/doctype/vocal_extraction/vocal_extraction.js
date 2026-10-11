frappe.ui.form.on("Vocal Extraction", {
	refresh(frm) {
		if (frm.is_new()) return;

		const running = ["Queued", "Downloading", "Separating"].includes(frm.doc.status);

		if (running) {
			frm.dashboard.set_headline(__("Working..."));
			frm.add_custom_button(__("Refresh"), () => frm.reload_doc());
			setTimeout(() => frm.reload_doc(), 5000);
		}

		if (frm.doc.status === "Downloaded" && frm.doc.audio_file) {
			frm.add_custom_button(__("Remove music"), () => {
				frm.call("separate").then(() => frm.reload_doc());
			});
		}

		if (frm.doc.status === "Failed") {
			frm.add_custom_button(__("Retry download"), () => {
				frm.call("download").then(() => frm.reload_doc());
			});
		}
	},
});
