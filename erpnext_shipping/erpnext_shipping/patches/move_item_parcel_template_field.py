# Copyright (c) 2026, Frappe Technologies and contributors
# For license information, please see license.txt
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields

from erpnext_shipping.custom_fields import get_fields_for_patch


def execute():
	# insert_after değişti (weight_per_unit -> stock_uom); create_custom_fields
	# mevcut alanı günceller, böylece Product Bundle gibi stok-takipsiz ürünlerde
	# de görünür.
	create_custom_fields(get_fields_for_patch("Item", ["custom_shipment_parcel_template"]))
