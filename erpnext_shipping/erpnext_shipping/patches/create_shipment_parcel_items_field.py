# Copyright (c) 2026, Frappe Technologies and contributors
# For license information, please see license.txt
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields

from erpnext_shipping.custom_fields import get_custom_fields


def execute():
	all_fields = get_custom_fields()
	create_custom_fields(
		{
			"Shipment": all_fields.get("Shipment", []),
			"Item": all_fields.get("Item", []),
		}
	)
