# Copyright (c) 2026, Frappe Technologies and contributors
# For license information, please see license.txt
"""Complete the receiver of existing Shipping Cost Entries from their matched order.

SendCloud entries never had a name or street, FedEx entries no street or zip
(docs/plans/kargo-maliyet-ekrani-ve-itiraz.md, step 1). Only empty fields are
filled; what the carrier's invoice said stays. Written with set_value: saving the
entries would re-roll every Shipment's cost for nothing.

SendCloud's own street (now read on import) is not re-read from the old invoice
files here; the order's address is the same address. A re-import of the file
replaces it with the invoice's.
"""

import frappe

from erpnext_shipping.erpnext_shipping.shipping_cost import RECEIVER_FIELDS, fill_receiver_from_order


def execute():
	eksik = " or ".join(f"ifnull({f}, '') = ''" for f in RECEIVER_FIELDS)
	for row in frappe.db.sql(
		f"""select name, shipment, delivery_note, receiver_from_order, {", ".join(RECEIVER_FIELDS)}
		from `tabShipping Cost Entry`
		where ifnull(receiver_source, '') = '' or ({eksik})""",
		as_dict=True,
	):
		doc = frappe._dict(row)
		fill_receiver_from_order(doc)
		degisen = {f: doc.get(f) for f in (*RECEIVER_FIELDS, "receiver_from_order", "receiver_source")}
		frappe.db.set_value("Shipping Cost Entry", row.name, degisen, update_modified=False)
