// Copyright (c) 2026, Frappe and contributors
// For license information, please see license.txt

frappe.listview_settings["Pickup Item Summary"] = {
	onload: function (listview) {
		listview.page.add_inner_button(__("Generate from Pickup Date"), function () {
			const d = new frappe.ui.Dialog({
				title: __("Generate Pickup Item Summary"),
				fields: [
					{
						fieldtype: "Date",
						fieldname: "pickup_date",
						label: __("Pickup Date"),
						default: frappe.datetime.get_today(),
						reqd: 1,
					},
					{
						fieldtype: "HTML",
						fieldname: "note",
						options: `<div class="text-muted small">${__(
							"Product Bundles are counted by their components. An existing summary for the date is refreshed with the shipments booked since."
						)}</div>`,
					},
				],
				primary_action_label: __("Generate"),
				primary_action(values) {
					frappe.call({
						method: "erpnext_shipping.erpnext_shipping.doctype.pickup_item_summary.pickup_item_summary.create_for_date",
						freeze: true,
						freeze_message: __("Aggregating items"),
						args: { pickup_date: values.pickup_date },
						callback: function (r) {
							if (r.exc || !r.message) return;
							d.hide();
							const m = r.message;
							frappe.msgprint({
								title: m.refreshed ? __("Summary Refreshed") : __("Summary Created"),
								indicator: "green",
								message: __("{0} — {1} item(s), {2} unit(s) in total.", [
									`<a href="/app/pickup-item-summary/${encodeURIComponent(
										m.name
									)}">${frappe.utils.escape_html(m.name)}</a>`,
									m.total_items,
									format_number(m.total_qty),
								]),
							});
							listview.refresh();
						},
					});
				},
			});
			d.show();
		});
	},
};
