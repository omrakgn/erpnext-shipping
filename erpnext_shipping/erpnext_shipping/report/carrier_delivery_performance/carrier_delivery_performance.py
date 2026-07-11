# Copyright (c) 2026, Frappe and contributors
# For license information, please see license.txt

import frappe
from frappe import _


def execute(filters=None):
	filters = frappe._dict(filters or {})
	return get_columns(), get_data(filters)


def get_columns():
	return [
		{"label": _("Carrier"), "fieldname": "carrier", "fieldtype": "Data", "width": 160},
		{"label": _("Delivered"), "fieldname": "delivered", "fieldtype": "Int", "width": 100},
		{"label": _("Avg Transit (days)"), "fieldname": "avg_days", "fieldtype": "Float", "precision": "1", "width": 140},
		{"label": _("Min"), "fieldname": "min_days", "fieldtype": "Float", "precision": "1", "width": 80},
		{"label": _("Max"), "fieldname": "max_days", "fieldtype": "Float", "precision": "1", "width": 80},
		{"label": _("In Transit"), "fieldname": "in_transit", "fieldtype": "Int", "width": 100},
		{"label": _("Returned"), "fieldname": "returned", "fieldtype": "Int", "width": 100},
		{"label": _("Lost"), "fieldname": "lost", "fieldtype": "Int", "width": 80},
	]


def get_data(filters):
	conds = ["docstatus < 2", "ifnull(carrier, '') != ''"]
	values = {}
	if filters.get("from_date"):
		conds.append("pickup_date >= %(from_date)s")
		values["from_date"] = filters.from_date
	if filters.get("to_date"):
		conds.append("pickup_date <= %(to_date)s")
		values["to_date"] = filters.to_date
	if filters.get("carrier"):
		conds.append("carrier = %(carrier)s")
		values["carrier"] = filters.carrier
	where = " and ".join(conds)

	return frappe.db.sql(
		f"""
		select
			carrier,
			sum(case when custom_transit_days is not null then 1 else 0 end) as delivered,
			round(avg(custom_transit_days), 1) as avg_days,
			min(custom_transit_days) as min_days,
			max(custom_transit_days) as max_days,
			sum(case when tracking_status = 'In Progress' then 1 else 0 end) as in_transit,
			sum(case when tracking_status = 'Returned' then 1 else 0 end) as returned,
			sum(case when tracking_status = 'Lost' then 1 else 0 end) as lost
		from `tabShipment`
		where {where}
		group by carrier
		order by delivered desc
		""",
		values,
		as_dict=True,
	)
