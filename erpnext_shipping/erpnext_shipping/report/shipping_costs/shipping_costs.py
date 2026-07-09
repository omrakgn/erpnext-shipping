# Copyright (c) 2026, Frappe and contributors
# For license information, please see license.txt

import frappe
from frappe import _


def execute(filters=None):
	filters = frappe._dict(filters or {})
	group_by = filters.get("group_by") or "Parcel"
	columns = get_columns(group_by)
	data = get_data(filters, group_by)
	return columns, data


def get_columns(group_by):
	if group_by == "Shipment":
		key = [
			{"label": _("Shipment"), "fieldname": "shipment", "fieldtype": "Link", "options": "Shipment", "width": 150},
			{"label": _("Delivery Note"), "fieldname": "delivery_note", "fieldtype": "Link", "options": "Delivery Note", "width": 150},
		]
	elif group_by == "Invoice":
		key = [{"label": _("Invoice"), "fieldname": "invoice_number", "fieldtype": "Data", "width": 130}]
	else:  # Parcel
		key = [
			{"label": _("Parcel Number"), "fieldname": "parcel_number", "fieldtype": "Data", "width": 150},
			{"label": _("Shipment"), "fieldname": "shipment", "fieldtype": "Link", "options": "Shipment", "width": 140},
		]
	return key + [
		{"label": _("Carrier"), "fieldname": "carrier", "fieldtype": "Data", "width": 80},
		{"label": _("Lines"), "fieldname": "lines", "fieldtype": "Int", "width": 70},
		{"label": _("Net Cost"), "fieldname": "net_cost", "fieldtype": "Currency", "options": "currency", "width": 120},
		{"label": _("Currency"), "fieldname": "currency", "fieldtype": "Data", "width": 80},
		{"label": _("Matched"), "fieldname": "matched", "fieldtype": "Check", "width": 80},
	]


def _conditions(filters):
	conds = []
	values = {}
	if filters.get("from_date"):
		conds.append("scan_date >= %(from_date)s")
		values["from_date"] = filters.from_date
	if filters.get("to_date"):
		conds.append("scan_date <= %(to_date)s")
		values["to_date"] = filters.to_date
	if filters.get("invoice_number"):
		conds.append("invoice_number = %(invoice_number)s")
		values["invoice_number"] = filters.invoice_number
	if filters.get("carrier"):
		conds.append("carrier = %(carrier)s")
		values["carrier"] = filters.carrier
	if filters.get("shipment"):
		conds.append("shipment = %(shipment)s")
		values["shipment"] = filters.shipment
	if filters.get("product_name"):
		conds.append("product_name = %(product_name)s")
		values["product_name"] = filters.product_name
	if filters.get("matched") in ("0", "1", 0, 1):
		conds.append("matched = %(matched)s")
		values["matched"] = filters.matched
	if filters.get("only_corrections"):
		conds.append("is_correction = 1")
	where = (" where " + " and ".join(conds)) if conds else ""
	return where, values


def get_data(filters, group_by):
	where, values = _conditions(filters)

	if group_by == "Shipment":
		group_col = "shipment"
		select_extra = "shipment, any_value(delivery_note) as delivery_note,"
	elif group_by == "Invoice":
		group_col = "invoice_number"
		select_extra = "invoice_number,"
	else:
		group_col = "parcel_number"
		select_extra = "parcel_number, any_value(shipment) as shipment,"

	rows = frappe.db.sql(
		f"""
		select
			{select_extra}
			any_value(carrier) as carrier,
			count(*) as lines,
			sum(total_net_amount) as net_cost,
			any_value(currency) as currency,
			min(matched) as matched
		from `tabShipping Cost Entry`
		{where}
		group by {group_col}
		order by net_cost desc
		""",
		values,
		as_dict=True,
	)
	return rows
