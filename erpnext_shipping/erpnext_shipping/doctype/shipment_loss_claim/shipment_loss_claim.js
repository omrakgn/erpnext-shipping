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

		// Müşteriye e-posta — formu imza için yollar. Adres alanı boş olabilir:
		// pazaryeri siparişlerinde gelen adres çoğu zaman anonim proxy oluyor ve
		// müşterinin gerçek adresi sonradan öğreniliyor. O yüzden buton her zaman
		// görünür; adres dialog'daki "To" alanına yazılır.
		if (["Draft", "Form Sent to Customer"].includes(frm.doc.status)) {
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

		// Reship: kayıp parsel için yeni DN + yeni Shipment (taslak) oluştur.
		if (frm.doc.replacement_shipment) {
			frm.add_custom_button(
				__("Open Replacement Shipment"),
				() => frappe.set_route("Form", "Shipment", frm.doc.replacement_shipment),
				__("Loss")
			);
		} else {
			frm.add_custom_button(
				__("Create Replacement Shipment"),
				() => {
					frappe.confirm(
						__(
							"Create a replacement Delivery Note and Shipment (both as drafts) for this lost parcel?"
						),
						() => {
							frappe.call({
								method: "erpnext_shipping.erpnext_shipping.doctype.shipment_loss_claim.shipment_loss_claim.create_replacement_shipment",
								args: { claim: frm.doc.name },
								freeze: true,
								freeze_message: __("Creating replacement"),
								callback: (r) => {
									if (!r.exc && r.message && r.message.shipment) {
										frappe.set_route("Form", "Shipment", r.message.shipment);
									}
								},
							});
						}
					);
				},
				__("Loss")
			);
		}

		// Otomatik doldurma sadece oluşturulurken çalışır (silinen alan geri
		// gelmesin diye). Sonradan Shipment'a bilgi eklendiyse elle çekme yolu.
		frm.add_custom_button(
			__("Refresh from Shipment"),
			() => {
				frappe.confirm(
					__(
						"Fill empty fields from the Shipment again? Fields you cleared on purpose (e.g. an unusable marketplace e-mail) may come back."
					),
					() => {
						frappe.call({
							method: "erpnext_shipping.erpnext_shipping.doctype.shipment_loss_claim.shipment_loss_claim.refresh_from_shipment",
							args: { claim: frm.doc.name },
							freeze: true,
							callback: (r) => {
								if (!r.exc) frm.reload_doc();
							},
						});
					}
				);
			},
			__("Form")
		);

		// Yalnız DPD talebi e-posta ile alıyor; SendCloud ve FedEx kendi
		// portallarından. O yüzden "gönderildi" demenin e-postadan bağımsız bir
		// yolu olmalı — yoksa portal üzerinden açılmış bir talep sonsuza kadar
		// "gönderilmedi" görünür ve süre hatırlatması boşuna öter.
		if (!frm.doc.submitted_date) {
			frm.add_custom_button(
				__("Mark as Filed with Carrier"),
				() => {
					const d = new frappe.ui.Dialog({
						title: __("Filed with Carrier"),
						fields: [
							{
								fieldname: "submitted_date",
								label: __("Filed On"),
								fieldtype: "Date",
								default: frappe.datetime.get_today(),
								reqd: 1,
							},
							{
								fieldname: "carrier_claim_ref",
								label: __("Carrier Claim Reference"),
								fieldtype: "Data",
								description: __("The reference the carrier's portal gave you."),
							},
							{ fieldname: "notes", label: __("Notes"), fieldtype: "Small Text" },
							{
								fieldname: "info",
								fieldtype: "HTML",
								options: `<div class="text-muted small">${__(
									"Use this when the claim was filed through the carrier's portal instead of by e-mail. No mail is sent; the claim is recorded as submitted and the deadline reminder stops."
								)}</div>`,
							},
						],
						primary_action_label: __("Record"),
						primary_action(values) {
							frappe.call({
								method: "erpnext_shipping.erpnext_shipping.doctype.shipment_loss_claim.shipment_loss_claim.mark_claim_filed",
								args: { claim: frm.doc.name, ...values },
								freeze: true,
								callback: (r) => {
									if (!r.exc) {
										d.hide();
										frappe.show_alert({
											message: __("Recorded as filed"),
											indicator: "green",
										});
										frm.reload_doc();
									}
								},
							});
						},
					});
					d.show();
				},
				__("Loss")
			);
		}

		// Kayıp sanılan parsel sonradan (bazen aylar sonra) çıkabiliyor. Statüsü
		// ne olursa olsun işaretlenebilmeli — carrier'a gönderilmiş, hatta ödenmiş
		// bir talep de bulunabilir.
		if (!frm.doc.found_outcome) {
			frm.add_custom_button(
				__("Mark as Found"),
				() => {
					const d = new frappe.ui.Dialog({
						title: __("Parcel Found"),
						fields: [
							{
								fieldname: "outcome",
								label: __("Where did it end up?"),
								fieldtype: "Select",
								options: ["Delivered to Customer", "Returned to Us"],
								reqd: 1,
							},
							{
								fieldname: "found_date",
								label: __("Found On"),
								fieldtype: "Date",
								default: frappe.datetime.get_today(),
								reqd: 1,
							},
							{ fieldname: "notes", label: __("Notes"), fieldtype: "Small Text" },
							{
								fieldname: "info",
								fieldtype: "HTML",
								options: `<div class="text-muted small">${__(
									"The claim is closed as Recovered. The goods value stops counting as a loss unless a replacement was already shipped."
								)}</div>`,
							},
						],
						primary_action_label: __("Mark as Found"),
						primary_action(values) {
							frappe.call({
								method: "erpnext_shipping.erpnext_shipping.doctype.shipment_loss_claim.shipment_loss_claim.mark_as_found",
								args: { claim: frm.doc.name, ...values },
								freeze: true,
								callback: (r) => {
									if (!r.exc) {
										d.hide();
										frappe.show_alert({
											message: __("Marked as found"),
											indicator: "green",
										});
										frm.reload_doc();
									}
								},
							});
						},
					});
					d.show();
				},
				__("Loss")
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
