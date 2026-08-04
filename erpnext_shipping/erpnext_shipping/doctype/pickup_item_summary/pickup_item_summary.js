// Copyright (c) 2026, Frappe and contributors
// For license information, please see license.txt

frappe.ui.form.on("Pickup Item Summary", {
	refresh(frm) {
		frm.add_custom_button(__("Generate / Refresh Items"), () => generate_items(frm));
	},
	pickup_date(frm) {
		if (frm.is_new() && !(frm.doc.items || []).length) generate_items(frm);
	},
});

// Pickup tarihindeki tüm gönderilerin ürünlerini toplayıp tabloya doldur.
function generate_items(frm) {
	if (!frm.doc.pickup_date) {
		frappe.msgprint(__("Set the Pickup Date first."));
		return;
	}
	frappe.call({
		method: "erpnext_shipping.erpnext_shipping.doctype.pickup_item_summary.pickup_item_summary.get_pickup_items",
		args: { pickup_date: frm.doc.pickup_date },
		freeze: true,
		freeze_message: __("Aggregating items"),
		callback: (r) => {
			const rows = r.message || [];
			frm.clear_table("items");
			rows.forEach((it) => {
				const row = frm.add_child("items");
				row.item_code = it.item_code;
				row.item_name = it.item_name;
				row.qty = it.qty;
			});
			frm.refresh_field("items");
			frm.set_value("total_items", rows.length);
			frm.set_value(
				"total_qty",
				rows.reduce((s, it) => s + flt(it.qty), 0)
			);
			if (!rows.length) {
				frappe.show_alert({ message: __("No shipment items for this date."), indicator: "orange" });
			}
		},
	});
}
