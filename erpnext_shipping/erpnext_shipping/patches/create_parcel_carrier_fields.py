# Copyright (c) 2026, Frappe Technologies and contributors
# For license information, please see license.txt
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields

from erpnext_shipping.custom_fields import get_fields_for_patch


def execute():
	create_custom_fields(
		get_fields_for_patch(
			"Shipment Parcel",
			[
				"custom_carrier_section",
				"custom_select_carrier",
				"custom_shipping_option_code",
				"custom_shipping_carrier",
				"custom_shipping_service",
				"custom_shipping_price",
			],
		)
	)
