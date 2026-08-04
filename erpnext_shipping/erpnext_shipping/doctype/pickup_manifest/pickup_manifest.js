// Copyright (c) 2026, Frappe and contributors
// For license information, please see license.txt

frappe.ui.form.on("Pickup Manifest", {
	refresh(frm) {
		render_item_summary(frm);
	},
	items_on_form_rendered(frm) {
		render_item_summary(frm);
	},
});

// Manifest'teki her üründen toplam kaç adet olduğunu 'Item Summary' alanına yaz.
function render_item_summary(frm) {
	const field = frm.get_field("item_summary");
	if (!field) return;
	if (frm.is_new()) {
		field.$wrapper.html(`<div class="text-muted">${__("Save to see the item summary.")}</div>`);
		return;
	}
	frappe.call({
		method: "erpnext_shipping.erpnext_shipping.doctype.pickup_manifest.pickup_manifest.get_manifest_item_summary",
		args: { manifest: frm.doc.name },
		callback: (r) => {
			const rows = r.message || [];
			if (!rows.length) {
				field.$wrapper.html(`<div class="text-muted">${__("No items.")}</div>`);
				return;
			}
			const esc = frappe.utils.escape_html;
			let html =
				'<table class="table table-bordered" style="font-size:12px; max-width:520px;"><thead><tr>' +
				`<th>${__("Item Code")}</th><th>${__("Description")}</th>` +
				`<th style="text-align:right;">${__("Total Qty")}</th></tr></thead><tbody>`;
			rows.forEach((s) => {
				html +=
					`<tr><td>${esc(s.item_code)}</td><td>${esc(s.item_name || "")}</td>` +
					`<td style="text-align:right;">${format_number(s.qty, null, 0)}</td></tr>`;
			});
			html += "</tbody></table>";
			field.$wrapper.html(html);
		},
	});
}
