# Copyright (c) 2026, Frappe Technologies and contributors
# For license information, please see license.txt
import frappe
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields

from erpnext_shipping.custom_fields import get_fields_for_patch


def execute():
	# Value of Goods koli (Shipment Parcel) bazlı. Kısa süre eklenen Delivery Note
	# satırı alanını (varsa) kaldır ve koli alanlarını oluştur. Idempotent — eski
	# patch adı bir sistemde çalışmış olsa bile bu ayrı ad kesin çalışır.
	if frappe.db.exists("Custom Field", "Shipment Delivery Note-custom_value_of_goods"):
		frappe.delete_doc(
			"Custom Field", "Shipment Delivery Note-custom_value_of_goods", ignore_permissions=True
		)

	create_custom_fields(
		get_fields_for_patch("Shipment Parcel", ["custom_value_of_goods", "custom_delivery_note"])
	)
