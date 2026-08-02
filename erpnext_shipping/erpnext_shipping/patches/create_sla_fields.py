# Copyright (c) 2026, Frappe Technologies and contributors
# For license information, please see license.txt
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields

from erpnext_shipping.custom_fields import get_fields_for_patch


def execute():
	create_custom_fields(
		get_fields_for_patch(
			"Shipment",
			[
				"custom_sla_section",
				"custom_sales_channel",
				"custom_promised_delivery_date",
				"custom_sla_date",
				"custom_sla_status",
				"custom_sla_risk_notified",
				"custom_sla_breach_notified",
			],
		)
	)
