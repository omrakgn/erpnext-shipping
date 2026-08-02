# Copyright (c) 2026, Frappe Technologies and contributors
# For license information, please see license.txt
"""SLA Performance: on-time delivery vs the SLA date, grouped by sales channel
and carrier. On-Time = delivered on/before the SLA date (status Met); Late =
delivered after (Missed); At Risk / Breached are still-open shipments."""
import frappe
from frappe.utils import flt


def execute(filters=None):
	filters = filters or {}
	return get_columns(), get_data(filters)


def get_columns():
	return [
		{"label": "Channel", "fieldname": "channel", "fieldtype": "Data", "width": 120},
		{"label": "Carrier", "fieldname": "carrier", "fieldtype": "Data", "width": 130},
		{"label": "Shipments", "fieldname": "shipments", "fieldtype": "Int", "width": 90},
		{"label": "Delivered", "fieldname": "delivered", "fieldtype": "Int", "width": 90},
		{"label": "On-Time", "fieldname": "on_time", "fieldtype": "Int", "width": 80},
		{"label": "Late", "fieldname": "late", "fieldtype": "Int", "width": 70},
		{"label": "At Risk", "fieldname": "at_risk", "fieldtype": "Int", "width": 80},
		{"label": "Breached", "fieldname": "breached", "fieldtype": "Int", "width": 80},
		{"label": "On-Time %", "fieldname": "on_time_pct", "fieldtype": "Percent", "width": 100},
	]


def get_conditions(filters):
	conds = ["docstatus = 1", "custom_sla_date is not null"]
	values = []
	if filters.get("from_date"):
		conds.append("pickup_date >= %s")
		values.append(filters["from_date"])
	if filters.get("to_date"):
		conds.append("pickup_date <= %s")
		values.append(filters["to_date"])
	if filters.get("channel"):
		conds.append("ifnull(custom_sales_channel, '') = %s")
		values.append(filters["channel"])
	if filters.get("carrier"):
		conds.append("carrier like %s")
		values.append(f"%{filters['carrier']}%")
	return " where " + " and ".join(conds), values


def get_data(filters):
	where, values = get_conditions(filters)
	rows = frappe.db.sql(
		f"""
		select
			coalesce(nullif(trim(custom_sales_channel), ''), '(unset)') as channel,
			coalesce(nullif(trim(carrier), ''), 'Unknown') as carrier,
			count(*) as shipments,
			sum(case when custom_sla_status in ('Met', 'Missed') then 1 else 0 end) as delivered,
			sum(case when custom_sla_status = 'Met' then 1 else 0 end) as on_time,
			sum(case when custom_sla_status = 'Missed' then 1 else 0 end) as late,
			sum(case when custom_sla_status = 'At Risk' then 1 else 0 end) as at_risk,
			sum(case when custom_sla_status = 'Breached' then 1 else 0 end) as breached
		from `tabShipment`
		{where}
		group by channel, carrier
		order by channel, breached desc, late desc
		""",
		values,
		as_dict=True,
	)
	for r in rows:
		r["on_time_pct"] = (flt(r.on_time) / r.delivered * 100) if r.delivered else 0
	return rows
