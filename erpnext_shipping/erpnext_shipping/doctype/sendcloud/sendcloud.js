// Copyright (c) 2020, Frappe and contributors
// For license information, please see license.txt

frappe.ui.form.on("SendCloud", {
	sync_contracts: function (frm) {
		frappe.call({
			method: "erpnext_shipping.erpnext_shipping.doctype.sendcloud.sendcloud.sync_sendcloud_contracts",
			freeze: true,
			freeze_message: __("Fetching contracts from SendCloud..."),
			callback: function (r) {
				if (r.exc) return;
				const res = r.message || {};
				frm.reload_doc();
				let msg = __("{0} contract(s) on the account, {1} new.", [
					res.total || 0,
					res.added || 0,
				]);
				if ((res.stale || []).length) {
					// Silmiyoruz: eski gönderiler bu id'lere atıfta bulunuyor ve
					// tazminat talebi yıllar sonra açılabiliyor.
					msg +=
						"<br>" +
						__("No longer on the account (kept for history): {0}", [
							res.stale.join(", "),
						]);
				}
				frappe.msgprint({ message: msg, title: __("Contracts synced"), indicator: "green" });
			},
		});
	},
});
