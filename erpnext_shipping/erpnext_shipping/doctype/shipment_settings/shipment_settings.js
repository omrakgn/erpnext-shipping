// Copyright (c) 2026, Frappe and contributors
// For license information, please see license.txt

frappe.ui.form.on("Shipment Settings", {
	refresh(frm) {
		const company = frappe.defaults.get_default("company");

		// Pickup adresi = şirkete bağlı adresler.
		frm.set_query("default_pickup_address", () => ({
			query: "frappe.contacts.doctype.address.address.address_query",
			filters: { link_doctype: "Company", link_name: company },
		}));

		// Pickup contact = User (ERPNext Company pickup akışı User bekler).
		frm.set_query("default_pickup_contact_person", () => ({
			filters: { enabled: 1 },
		}));

		// Gönderen = yalnızca giden (outgoing) e-posta hesapları.
		frm.set_query("delay_email_account", () => ({
			filters: { enable_outgoing: 1 },
		}));
	},
});
