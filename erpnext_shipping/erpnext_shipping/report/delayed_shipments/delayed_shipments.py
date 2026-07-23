# Copyright (c) 2026, Frappe and contributors
# For license information, please see license.txt

import frappe
from frappe import _

from erpnext_shipping.erpnext_shipping.delay import get_delayed_shipments


def execute(filters=None):
	filters = frappe._dict(filters or {})
	return get_columns(), get_data(filters)


def get_columns():
	return [
		{"label": _("Shipment"), "fieldname": "shipment", "fieldtype": "Link", "options": "Shipment", "width": 150},
		{"label": _("Carrier"), "fieldname": "carrier", "fieldtype": "Data", "width": 90},
		# Tek satır = tek (teslim olmamış) parça; her satır o parçanın kendi tracking'i.
		{"label": _("Tracking No"), "fieldname": "awb_number", "fieldtype": "Data", "width": 160},
		{"label": _("Status"), "fieldname": "tracking_status", "fieldtype": "Data", "width": 120},
		{"label": _("Pickup Date"), "fieldname": "pickup_date", "fieldtype": "Date", "width": 110},
		{"label": _("Days Elapsed"), "fieldname": "days_elapsed", "fieldtype": "Int", "width": 110},
		{"label": _("Delivery To"), "fieldname": "delivery_to", "fieldtype": "Data", "width": 200},
		# Number Card (Report tipi) Count desteklemediği için Sum(cnt) ile sayım;
		# cnt yalnızca her gönderinin İLK parça satırında 1 -> Sum = ayrı gönderi adedi.
		{"label": _("#"), "fieldname": "cnt", "fieldtype": "Int", "width": 50},
	]


def get_data(filters):
	# Shared with the "Delayed" number card and the daily flag/digest scheduler so
	# all three agree on what "delayed" means. min_days blank -> Shipment Settings.
	return get_delayed_shipments(min_days=filters.get("min_days"), carrier=filters.get("carrier"))
