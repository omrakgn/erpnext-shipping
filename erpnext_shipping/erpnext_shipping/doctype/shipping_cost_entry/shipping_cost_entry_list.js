// Copyright (c) 2026, Frappe and contributors
// For license information, please see license.txt

frappe.listview_settings["Shipping Cost Entry"] = {
	onload: function (listview) {
		listview.page.add_inner_button(__("Import Invoice"), function () {
			const d = new frappe.ui.Dialog({
				title: __("Import Carrier Invoice"),
				fields: [
					{
						fieldtype: "Attach",
						fieldname: "file",
						label: __("Invoice File"),
						reqd: 1,
						description: __(
							"DPD detail (.xlsx), FedEx invoice (.xml), or a .zip containing many of them. Format is auto-detected."
						),
					},
				],
				primary_action_label: __("Import"),
				primary_action(values) {
					frappe.call({
						method: "erpnext_shipping.erpnext_shipping.shipping_cost.import_invoice",
						freeze: true,
						freeze_message: __("Importing shipping costs..."),
						args: { file_url: values.file },
						callback: function (r) {
							if (r.exc || !r.message) return;
							d.hide();
							const m = r.message;
							let msg = __(
								"Files: {0}, Created: {1}, Updated: {2}<br>Matched shipments: {3} (by tracking: {4}, by order no: {5})<br>Ambiguous: {6}, Unmatched parcels: {7}",
								[
									m.files,
									m.created,
									m.updated,
									m.matched_shipments,
									m.matched_by_tracking,
									m.matched_by_order,
									m.ambiguous,
									m.unmatched_parcels,
								]
							);
							if (m.errors && m.errors.length) {
								msg +=
									"<br><br><b>" +
									__("Errors ({0}):", [m.errors.length]) +
									"</b><br>" +
									m.errors
										.slice(0, 10)
										.map((e) => frappe.utils.escape_html(e))
										.join("<br>");
							}
							frappe.msgprint({
								title: __("Import Complete"),
								indicator: m.errors && m.errors.length ? "orange" : "green",
								message: msg,
							});
							listview.refresh();
						},
					});
				},
			});
			d.show();
		});

		listview.page.add_inner_button(__("Re-match Unmatched"), function () {
			frappe.call({
				method: "erpnext_shipping.erpnext_shipping.shipping_cost.rematch_unmatched",
				freeze: true,
				freeze_message: __("Re-matching..."),
				callback: function (r) {
					if (r.exc || !r.message) return;
					frappe.msgprint({
						title: __("Re-match Complete"),
						indicator: "blue",
						message: __("Matched: {0}, Still unmatched: {1}", [
							r.message.matched,
							r.message.remaining,
						]),
					});
					listview.refresh();
				},
			});
		});
	},
};
