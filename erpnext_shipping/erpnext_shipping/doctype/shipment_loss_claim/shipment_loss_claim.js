// Copyright (c) 2026, Frappe Technologies and contributors
// For license information, please see license.txt

frappe.ui.form.on("Shipment Loss Claim", {
	refresh(frm) {
		if (frm.is_new()) return;

		// Formu PDF olarak indir/yazdır — pazaryeri siparişlerinde (e-posta yoksa)
		// müşteriye manuel iletmek için ana yol.
		frm.add_custom_button(
			__("Print / Download Form"),
			() => {
				open_url_post(
					"/api/method/erpnext_shipping.erpnext_shipping.doctype.shipment_loss_claim.shipment_loss_claim.download_claim_form",
					{ claim: frm.doc.name }
				);
			},
			__("Form")
		);

		// Müşteriye e-posta (adres varsa) — formu imza için yollar.
		if (frm.doc.receiver_email && ["Draft", "Form Sent to Customer"].includes(frm.doc.status)) {
			frm.add_custom_button(
				__("Email Form to Customer"),
				() => {
					frappe.call({
						method: "erpnext_shipping.erpnext_shipping.doctype.shipment_loss_claim.shipment_loss_claim.email_form_to_customer",
						args: { claim: frm.doc.name },
						freeze: true,
						callback: (r) => {
							if (!r.exc) {
								frappe.show_alert({ message: __("Form emailed"), indicator: "green" });
								frm.reload_doc();
							}
						},
					});
				},
				__("Form")
			);
		}

		// İmzalı form geldiyse carrier'a gönder (imzalı form + satınalma faturası).
		if (frm.doc.signed_form && frm.doc.status !== "Submitted to Carrier") {
			frm.add_custom_button(
				__("Submit to Carrier"),
				() => {
					frappe.call({
						method: "erpnext_shipping.erpnext_shipping.doctype.shipment_loss_claim.shipment_loss_claim.submit_claim_to_carrier",
						args: { claim: frm.doc.name },
						freeze: true,
						callback: (r) => {
							if (!r.exc) {
								frappe.show_alert({ message: __("Submitted to carrier"), indicator: "green" });
								frm.reload_doc();
							}
						},
					});
				},
				__("Claim")
			);
		}
	},
});
