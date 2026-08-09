// Copyright (c) 2026, Scarnatti and contributors
// For license information, please see license.txt

frappe.ui.form.on("Shipping Cost Settings", {
    refresh: function (frm) {
        if (!frm.doc.enable_cost_invoicing) {
            return;
        }

        frm.add_custom_button(
            __("Invoice Pending Carrier Invoices"),
            function () {
                frappe.confirm(
                    __(
                        "Create Purchase Invoices for every carrier invoice that has not been billed yet?<br><br>Invoices are created as drafts unless auto-submit is on, and freight dated before the cutover is left alone."
                    ),
                    function () {
                        frappe.call({
                            method: "erpnext_shipping.erpnext_shipping.shipping_cost_accounting.process_pending",
                            freeze: true,
                            freeze_message: __("Creating purchase invoices..."),
                            callback: function (r) {
                                if (!r.message) {
                                    return;
                                }
                                const res = r.message;
                                let msg = __("Created: {0}", [res.created.length]) +
                                    "<br>" + __("Nothing to bill: {0}", [res.skipped.length]) +
                                    "<br>" + __("Failed: {0}", [res.failed.length]);
                                if (res.failed.length) {
                                    msg += "<hr><pre style='white-space:pre-wrap'>";
                                    res.failed.forEach(function (f) {
                                        msg += frappe.utils.escape_html(
                                            f.invoice_number + ": " + f.error
                                        ) + "\n";
                                    });
                                    msg += "</pre>";
                                }
                                frappe.msgprint({
                                    title: __("Carrier Invoice Accounting"),
                                    indicator: res.failed.length ? "orange" : "green",
                                    message: msg,
                                });
                            },
                        });
                    }
                );
            },
            __("Accounting")
        );
    },
});
