# Copyright (c) 2026, Frappe Technologies and contributors
# For license information, please see license.txt
"""Populate the new surcharge_amount / weight_surcharge / reweigh fields on existing
Shipping Cost Entries from their stored charge_breakdown + weights."""
import json

import frappe

from erpnext_shipping.erpnext_shipping.shipping_cost import _surcharge_fields, recompute_shipment_cost


def execute():
	shipments = set()
	for row in frappe.get_all(
		"Shipping Cost Entry",
		fields=["name", "charge_breakdown", "invoicing_weight", "corrected_weight", "shipment"],
	):
		try:
			breakdown = json.loads(row.charge_breakdown or "{}")
		except Exception:
			breakdown = {}
		frappe.db.set_value(
			"Shipping Cost Entry",
			row.name,
			_surcharge_fields(breakdown, row.invoicing_weight, row.corrected_weight),
			update_modified=False,
		)
		if row.shipment:
			shipments.add(row.shipment)

	# Re-roll the surcharge totals onto the matched Shipments.
	for shipment in shipments:
		recompute_shipment_cost(shipment)

	frappe.db.commit()
