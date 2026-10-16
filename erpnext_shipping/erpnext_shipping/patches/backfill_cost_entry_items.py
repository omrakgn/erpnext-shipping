# Copyright (c) 2026, Frappe Technologies and contributors
# For license information, please see license.txt
"""Products and their cost shares on existing Shipping Cost Entries.

docs/plans/kargo-maliyet-ekrani-ve-itiraz.md, step 2. Rows are inserted directly
and the header written with set_value: saving every entry would re-roll every
Shipment's cost and write a version for each, for nothing.
"""

import frappe

from erpnext_shipping.erpnext_shipping.shipping_cost import cost_items


def execute():
	dolu = set(frappe.get_all("Shipping Cost Entry Item", pluck="parent", distinct=True))
	for name in frappe.get_all("Shipping Cost Entry", pluck="name"):
		if name in dolu:
			continue
		doc = frappe.get_doc("Shipping Cost Entry", name)
		sonuc = cost_items(doc)
		if not sonuc["items"]:
			continue
		for row in sonuc["items"]:
			doc.append("items", row).db_insert()
		frappe.db.set_value(
			"Shipping Cost Entry",
			name,
			{
				"allocation_basis": sonuc["allocation_basis"],
				"contents_source": sonuc["contents_source"],
				"contents_summary": sonuc["contents_summary"],
			},
			update_modified=False,
		)
