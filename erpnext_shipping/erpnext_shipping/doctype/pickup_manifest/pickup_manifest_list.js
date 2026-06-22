// Copyright (c) 2026, Frappe and contributors
// For license information, please see license.txt

frappe.listview_settings["Pickup Manifest"] = {
	onload: function (listview) {
		listview.page.add_inner_button(__("Generate from Pickup Date"), function () {
			const d = new frappe.ui.Dialog({
				title: __("Generate Pickup Manifests"),
				fields: [
					{
						fieldtype: "Date",
						fieldname: "pickup_date",
						label: __("Pickup Date"),
						default: frappe.datetime.get_today(),
						reqd: 1,
					},
				],
				primary_action_label: __("Generate"),
				primary_action(values) {
					frappe.call({
						method: "erpnext_shipping.erpnext_shipping.doctype.pickup_manifest.pickup_manifest.generate_pickup_manifests",
						freeze: true,
						freeze_message: __("Generating Pickup Manifests"),
						args: { pickup_date: values.pickup_date },
						callback: function (r) {
							if (r.exc || !r.message) return;
							d.hide();
							const rows = r.message
								.map(
									(m) =>
										`<tr><td><a href="/app/pickup-manifest/${encodeURIComponent(
											m.name
										)}">${frappe.utils.escape_html(m.name)}</a></td>` +
										`<td>${frappe.utils.escape_html(m.carrier)}</td>` +
										`<td>${m.packages}</td></tr>`
								)
								.join("");
							frappe.msgprint({
								title: __("Manifests Created"),
								indicator: "green",
								message:
									`<table class="table table-bordered"><thead><tr><th>${__(
										"Manifest"
									)}</th><th>${__("Carrier")}</th><th>${__(
										"Packages"
									)}</th></tr></thead><tbody>${rows}</tbody></table>`,
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
