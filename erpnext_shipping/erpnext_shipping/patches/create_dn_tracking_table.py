# Copyright (c) 2026, Frappe Technologies and contributors
# For license information, please see license.txt
import frappe
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields

from erpnext_shipping.custom_fields import get_custom_fields


def execute():
	# Eski düz tracking alanlarını kaldır (artık DN'de canlı tablo gösteriliyor)
	for fieldname in (
		"tracking_number",
		"tracking_url",
		"tracking_status",
		"tracking_status_info",
		"shipping_col_break",
	):
		frappe.delete_doc_if_exists("Custom Field", f"Delivery Note-{fieldname}")

	# Yeni alanları oluştur: DN tracking tablosu (HTML) + Shipment delivered_at / tracking_details
	all_fields = get_custom_fields()
	create_custom_fields(
		{
			"Delivery Note": all_fields.get("Delivery Note", []),
			"Shipment": all_fields.get("Shipment", []),
		}
	)
