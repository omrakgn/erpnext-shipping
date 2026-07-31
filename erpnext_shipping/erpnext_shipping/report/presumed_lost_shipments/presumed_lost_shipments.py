# Copyright (c) 2026, Frappe and contributors
# For license information, please see license.txt
import frappe
from frappe import _
from frappe.utils import add_days, cint, nowdate

from erpnext_shipping.erpnext_shipping.loss import presumed_lost_days


def execute(filters=None):
	filters = frappe._dict(filters or {})
	return get_columns(), get_data(filters)


def get_columns():
	return [
		{"label": _("Shipment"), "fieldname": "shipment", "fieldtype": "Link", "options": "Shipment", "width": 150},
		{"label": _("Carrier"), "fieldname": "carrier", "fieldtype": "Data", "width": 90},
		{"label": _("Tracking No"), "fieldname": "awb_number", "fieldtype": "Data", "width": 150},
		{"label": _("Pickup Date"), "fieldname": "pickup_date", "fieldtype": "Date", "width": 100},
		{"label": _("Days"), "fieldname": "days_elapsed", "fieldtype": "Int", "width": 70},
		{"label": _("Status"), "fieldname": "tracking_status", "fieldtype": "Data", "width": 110},
		{"label": _("Delivery To"), "fieldname": "delivery_to", "fieldtype": "Data", "width": 180},
		{"label": _("Goods Value"), "fieldname": "value_of_goods", "fieldtype": "Currency", "width": 100},
		{"label": _("Loss Claim"), "fieldname": "claim", "fieldtype": "Link", "options": "Shipment Loss Claim", "width": 130},
		{"label": _("Claim Status"), "fieldname": "claim_status", "fieldtype": "Data", "width": 130},
	]


def get_data(filters):
	min_days = cint(filters.get("min_days")) or presumed_lost_days()
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
		conds.append("carrier like %(carrier)s")
		values["carrier"] = f"%{filters.carrier}%"

	rows = frappe.db.sql(
		f"""
		select name as shipment, carrier, awb_number, pickup_date,
			datediff(curdate(), pickup_date) as days_elapsed, tracking_status,
			coalesce(delivery_customer, delivery_supplier, delivery_company) as delivery_to,
			value_of_goods
		from `tabShipment`
		where {" and ".join(conds)}
		order by pickup_date asc
		""",
		values,
		as_dict=True,
	)

	# Var olan Loss Claim'i eşle.
	claims = {
		c.shipment: c
		for c in frappe.get_all(
			"Shipment Loss Claim",
			filters={"shipment": ["in", [r.shipment for r in rows] or [""]]},
			fields=["name", "shipment", "status"],
		)
	}
	for r in rows:
		c = claims.get(r.shipment)
		if c:
			r["claim"] = c.name
			r["claim_status"] = c.status
	return rows
