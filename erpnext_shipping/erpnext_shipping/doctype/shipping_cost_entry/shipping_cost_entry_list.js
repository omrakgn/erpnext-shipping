// Copyright (c) 2026, Frappe and contributors
// For license information, please see license.txt

frappe.listview_settings["Shipping Cost Entry"] = {
	onload: function (listview) {
		listview.page.add_inner_button(__("Import DPD Invoice"), function () {
			const d = new frappe.ui.Dialog({
				title: __("Import DPD Invoice"),
				fields: [
					{
						fieldtype: "Attach",
						fieldname: "file",
						label: __("Invoice Detail File (.xlsx)"),
						reqd: 1,
					},
					{
						fieldtype: "Data",
						fieldname: "carrier",
						label: __("Carrier"),
						default: "DPD",
					},
				],
				primary_action_label: __("Import"),
				primary_action(values) {
					frappe.call({
						method: "erpnext_shipping.erpnext_shipping.shipping_cost.import_dpd_invoice",
						freeze: true,
						freeze_message: __("Importing shipping costs..."),
						args: { file_url: values.file, carrier: values.carrier || "DPD" },
						callback: function (r) {
							if (r.exc || !r.message) return;
							d.hide();
							const m = r.message;
							frappe.msgprint({
								title: __("Import Complete"),
								indicator: "green",
								message: __(
									"Created: {0}, Updated: {1}<br>Matched shipments: {2} (by tracking: {3}, by order no: {4})<br>Ambiguous: {5}, Unmatched parcels: {6}",
									[
										m.created,
										m.updated,
										m.matched_shipments,
										m.matched_by_tracking,
										m.matched_by_order,
										m.ambiguous,
										m.unmatched_parcels,
									]
								),
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
