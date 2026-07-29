# Copyright (c) 2026, Frappe Technologies and contributors
# For license information, please see license.txt
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields

from erpnext_shipping.custom_fields import get_fields_for_patch


def execute():
	fields = {}
	fields.update(
		get_fields_for_patch(
			"Shipment",
			[
				"custom_cost_variance",
				"custom_cost_variance_pct",
				"custom_customer_shipping_charge",
				"custom_shipping_margin",
			],
		)
	)
	fields.update(get_fields_for_patch("Delivery Note", ["custom_customer_shipping_charge"]))
	create_custom_fields(fields)
