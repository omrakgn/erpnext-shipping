# Copyright (c) 2026, Frappe and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.utils import add_days, cint, nowdate


def execute(filters=None):
	filters = frappe._dict(filters or {})
	return get_columns(), get_data(filters)


def get_columns():
	return [
		{"label": _("Shipment"), "fieldname": "shipment", "fieldtype": "Link", "options": "Shipment", "width": 150},
		{"label": _("Carrier"), "fieldname": "carrier", "fieldtype": "Data", "width": 90},
		{"label": _("Pickup Date"), "fieldname": "pickup_date", "fieldtype": "Date", "width": 110},
		{"label": _("Days Elapsed"), "fieldname": "days_elapsed", "fieldtype": "Int", "width": 110},
		{"label": _("Status"), "fieldname": "tracking_status", "fieldtype": "Data", "width": 110},
		{"label": _("Tracking No"), "fieldname": "awb_number", "fieldtype": "Data", "width": 150},
		{"label": _("Delivery To"), "fieldname": "delivery_to", "fieldtype": "Data", "width": 200},
		# Number Card (Report tipi) Count desteklemediği için Sum(cnt) ile sayım.
		{"label": _("#"), "fieldname": "cnt", "fieldtype": "Int", "width": 50},
	]


def get_data(filters):
	min_days = cint(filters.get("min_days")) or 5
	cutoff = add_days(nowdate(), -abs(min_days))

	conds = [
		"docstatus < 2",
		"ifnull(tracking_status, '') != 'Delivered'",
		"custom_delivered_at is null",
		"pickup_date is not null",
		"pickup_date <= %(cutoff)s",
		"ifnull(status, '') not in ('Cancelled', 'Completed')",
		"ifnull(awb_number, '') != ''",
	]
	values = {"cutoff": cutoff}
	if filters.get("carrier"):
		conds.append("carrier = %(carrier)s")
		values["carrier"] = filters.carrier
	where = " and ".join(conds)

	return frappe.db.sql(
		f"""
		select
			name as shipment,
			carrier,
			pickup_date,
			datediff(curdate(), pickup_date) as days_elapsed,
			tracking_status,
			awb_number,
			coalesce(delivery_customer, delivery_supplier, delivery_company) as delivery_to,
			1 as cnt
		from `tabShipment`
		where {where}
		order by pickup_date asc
		""",
		values,
		as_dict=True,
	)
