# Copyright (c) 2026, Frappe Technologies and contributors
# For license information, please see license.txt
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields

from erpnext_shipping.custom_fields import get_fields_for_patch


def execute():
	create_custom_fields(
		get_fields_for_patch(
			"Shipment",
			[
				"custom_shipping_cost",
				"custom_shipping_cost_currency",
				"custom_shipping_cost_updated",
			],
		)
	)
	create_custom_fields(get_fields_for_patch("Delivery Note", ["custom_shipping_cost"]))
