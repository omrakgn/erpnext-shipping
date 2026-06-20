// Copyright (c) 2026, Frappe and contributors
// For license information, please see license.txt

frappe.ui.form.on("Delivery Note", {
	refresh: function (frm) {
		if (frm.is_new()) return;
		frappe.call({
			method: "erpnext_shipping.erpnext_shipping.shipping.get_delivery_note_shipment_tracking",
			args: { delivery_note: frm.doc.name },
			callback: function (r) {
				render_shipment_tracking(frm, r.message || []);
			},
		});
	},
});

function render_shipment_tracking(frm, rows) {
	const field = frm.get_field("custom_shipment_tracking");
	if (!field) return;

	if (!rows.length) {
		field.$wrapper.html(
			`<div class="text-muted">${__("No shipment tracking information yet.")}</div>`
		);
		return;
	}

	const esc = frappe.utils.escape_html;
	let html = `<table class="table table-bordered" style="margin-top:8px;">
		<thead><tr>
			<th>${__("SKU")}</th>
			<th>${__("Carrier")}</th>
			<th>${__("Tracking")}</th>
			<th>${__("Status")}</th>
			<th>${__("Delivered")}</th>
		</tr></thead><tbody>`;

	rows.forEach((p) => {
		const tracking = p.tracking_url
			? `<a href="${encodeURI(p.tracking_url)}" target="_blank">${esc(
					p.tracking_number || __("Track")
			  )}</a>`
			: esc(p.tracking_number || "");
		const delivered = p.delivered_at ? esc(p.delivered_at) : "—";
		html += `<tr>
			<td>${esc(p.sku || "")}</td>
			<td>${esc(p.carrier || "")}</td>
			<td>${tracking}</td>
			<td>${esc(p.status || "")}</td>
			<td>${delivered}</td>
		</tr>`;
	});

	html += `</tbody></table>`;
	field.$wrapper.html(html);
}
