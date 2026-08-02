# Copyright (c) 2026, Frappe Technologies and contributors
# For license information, please see license.txt
"""Re-sync Shipment custom fields so the decluttering layout changes propagate:
new collapsible sections (Delivery Status, Shipping Cost & Margin), the moved
anchors, and the now-hidden delay guard fields."""
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields

from erpnext_shipping.custom_fields import get_custom_fields


def execute():
	create_custom_fields({"Shipment": get_custom_fields()["Shipment"]}, update=True)
