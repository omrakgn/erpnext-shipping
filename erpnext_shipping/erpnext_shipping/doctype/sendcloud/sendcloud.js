// Copyright (c) 2020, Frappe and contributors
// For license information, please see license.txt

frappe.ui.form.on("SendCloud", {
	sync_return_methods: function (frm) {
		frappe.call({
			method: "erpnext_shipping.erpnext_shipping.doctype.sendcloud.sendcloud.sync_sendcloud_return_methods",
			freeze: true,
			freeze_message: __("Fetching return products from SendCloud..."),
			callback: function (r) {
				if (r.exc) return;
				const res = r.message || {};
				frm.reload_doc();
				let msg = __("{0} return product(s) on the account, {1} new.", [
					res.total || 0,
					res.added || 0,
				]);
				// Bantlar ürünlere benziyor ve etiket alınırken reddediliyor;
				// hangisinin gerçek olduğunu ancak deneyerek ya da bilerek
				// anlayabiliyoruz, o yüzden seçim burada işaretleniyor.
				msg +=
					"<br><br>" +
					__("Tick the ones you actually use. Weight bands such as 'DPD Return 6-8kg' look like products but are contract price rows, and buying a label against one is refused.");
				if ((res.stale || []).length) {
					msg +=
						"<br><br>" +
						__("No longer on the account (kept for history): {0}", [
							res.stale.join(", "),
						]);
				}
				frappe.msgprint({
					message: msg,
					title: __("Return products synced"),
					indicator: "green",
				});
			},
		});
	},

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
