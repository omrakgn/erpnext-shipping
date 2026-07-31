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

		// Müşteriye e-posta (adres varsa) — formu imza için yollar. Doğrudan
		// göndermek yerine onay + düzenlenebilir içerik dialog'u açar.
		if (frm.doc.receiver_email && ["Draft", "Form Sent to Customer"].includes(frm.doc.status)) {
			frm.add_custom_button(
				__("Email Form to Customer"),
				() => {
					compose_and_send(frm, {
						draft_method:
							"erpnext_shipping.erpnext_shipping.doctype.shipment_loss_claim.shipment_loss_claim.get_form_email_draft",
						send_method:
							"erpnext_shipping.erpnext_shipping.doctype.shipment_loss_claim.shipment_loss_claim.email_form_to_customer",
						title: __("Email Form to Customer"),
						note: __("The filled DPD form is attached automatically."),
						sent_msg: __("Form emailed"),
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
					compose_and_send(frm, {
						draft_method:
							"erpnext_shipping.erpnext_shipping.doctype.shipment_loss_claim.shipment_loss_claim.get_carrier_email_draft",
						send_method:
							"erpnext_shipping.erpnext_shipping.doctype.shipment_loss_claim.shipment_loss_claim.submit_claim_to_carrier",
						title: __("Submit Claim to Carrier"),
						note: __("The signed form and purchase invoice are attached automatically."),
						sent_msg: __("Submitted to carrier"),
					});
				},
				__("Claim")
			);
		}
	},
});

// Draft'ı sunucudan çek, düzenlenebilir dialog'da göster, onaydan sonra gönder.
function compose_and_send(frm, opts) {
	frappe.call({
		method: opts.draft_method,
		args: { claim: frm.doc.name },
		freeze: true,
		callback: (r) => {
			if (r.exc || !r.message) return;
			const draft = r.message;
			const d = new frappe.ui.Dialog({
				title: opts.title,
				fields: [
					{
						fieldname: "recipient",
						label: __("To"),
						fieldtype: "Data",
						reqd: 1,
						default: draft.recipient,
					},
					{ fieldname: "subject", label: __("Subject"), fieldtype: "Data", reqd: 1, default: draft.subject },
					{
						fieldname: "content",
						label: __("Message"),
						fieldtype: "Text Editor",
						reqd: 1,
						default: draft.content,
					},
					{ fieldname: "note", fieldtype: "HTML", options: `<div class="text-muted small">${opts.note}</div>` },
				],
				primary_action_label: __("Send"),
				primary_action(values) {
					frappe.call({
						method: opts.send_method,
						args: {
							claim: frm.doc.name,
							recipient: values.recipient,
							subject: values.subject,
							content: values.content,
						},
						freeze: true,
						freeze_message: __("Sending"),
						callback: (res) => {
							if (!res.exc) {
								d.hide();
								frappe.show_alert({ message: opts.sent_msg, indicator: "green" });
								frm.reload_doc();
							}
						},
					});
				},
			});
			d.show();
		},
	});
}
