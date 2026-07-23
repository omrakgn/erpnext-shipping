# Copyright (c) 2026, Frappe Technologies and contributors
# For license information, please see license.txt
"""Re-apply the custom_transit_days definition so its new depends_on
(custom_delivered_at) reaches sites where the field already exists — hides the
misleading 0.0 on shipments that are not delivered yet."""
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields

from erpnext_shipping.custom_fields import get_fields_for_patch


def execute():
	create_custom_fields(get_fields_for_patch("Shipment", ["custom_transit_days"]), update=True)
