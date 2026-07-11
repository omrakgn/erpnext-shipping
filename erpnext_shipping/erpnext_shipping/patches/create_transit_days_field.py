# Copyright (c) 2026, Frappe Technologies and contributors
# For license information, please see license.txt
import frappe
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields

from erpnext_shipping.custom_fields import get_fields_for_patch


def execute():
	create_custom_fields(get_fields_for_patch("Shipment", ["custom_transit_days"]))

	# Backfill: mevcut teslim edilmiş gönderiler için transit süresini doldur.
	frappe.db.sql(
		"""
		update `tabShipment`
		set custom_transit_days = datediff(date(custom_delivered_at), pickup_date)
		where custom_delivered_at is not null
			and pickup_date is not null
			and datediff(date(custom_delivered_at), pickup_date) >= 0
		"""
	)
