# Copyright (c) 2026, Frappe and contributors
# For license information, please see license.txt
import json

import frappe
from frappe import _
from frappe.utils import flt


def execute(filters=None):
	filters = frappe._dict(filters or {})
	return get_columns(), get_data(filters)


def get_columns():
	return [
		{"label": _("Scan Date"), "fieldname": "scan_date", "fieldtype": "Date", "width": 100},
		{"label": _("Carrier"), "fieldname": "carrier", "fieldtype": "Data", "width": 80},
		{"label": _("Shipment"), "fieldname": "shipment", "fieldtype": "Link", "options": "Shipment", "width": 140},
		{"label": _("Parcel / Tracking"), "fieldname": "parcel_number", "fieldtype": "Data", "width": 140},
		{"label": _("Total"), "fieldname": "total_net_amount", "fieldtype": "Currency", "options": "currency", "width": 90},
		{"label": _("Base"), "fieldname": "base_amount", "fieldtype": "Currency", "options": "currency", "width": 90},
		{"label": _("Surcharge"), "fieldname": "surcharge_amount", "fieldtype": "Currency", "options": "currency", "width": 100},
		{"label": _("Surcharge %"), "fieldname": "surcharge_pct", "fieldtype": "Percent", "width": 100},
		{"label": _("Inv. Wt"), "fieldname": "invoicing_weight", "fieldtype": "Float", "precision": "2", "width": 80},
		{"label": _("Corr. Wt"), "fieldname": "corrected_weight", "fieldtype": "Float", "precision": "2", "width": 80},
		{"label": _("Re-weighed"), "fieldname": "reweigh", "fieldtype": "Check", "width": 90},
		{"label": _("Wt/Size"), "fieldname": "weight_surcharge", "fieldtype": "Check", "width": 70},
		{"label": _("Surcharges"), "fieldname": "surcharges", "fieldtype": "Data", "width": 280},
		{"label": _("Country"), "fieldname": "country", "fieldtype": "Data", "width": 70},
		{"label": _("Currency"), "fieldname": "currency", "fieldtype": "Data", "width": 70, "hidden": 1},
	]


def get_data(filters):
	conds = ["ifnull(is_correction, 0) = 0"]
	values = {}
	if filters.get("carrier"):
		conds.append("carrier = %(carrier)s")
		values["carrier"] = filters.carrier
	if filters.get("from_date"):
		conds.append("scan_date >= %(from_date)s")
		values["from_date"] = filters.from_date
	if filters.get("to_date"):
		conds.append("scan_date <= %(to_date)s")
		values["to_date"] = filters.to_date
	if filters.get("only_surcharged"):
		conds.append("ifnull(surcharge_amount, 0) > 0")
	if filters.get("only_weight_surcharge"):
		conds.append("(weight_surcharge = 1 or reweigh = 1)")
	where = " and ".join(conds)

	rows = frappe.db.sql(
		f"""
		select scan_date, carrier, shipment, parcel_number, total_net_amount, surcharge_amount,
			invoicing_weight, corrected_weight, reweigh, weight_surcharge, country, currency,
			charge_breakdown
		from `tabShipping Cost Entry`
		where {where}
		order by surcharge_amount desc, scan_date desc
		""",
		values,
		as_dict=True,
	)

	for r in rows:
		r["base_amount"] = flt(r.total_net_amount) - flt(r.surcharge_amount)
		r["surcharge_pct"] = (flt(r.surcharge_amount) / flt(r.total_net_amount) * 100) if r.total_net_amount else 0
		r["surcharges"] = _surcharge_names(r.get("charge_breakdown"))
	return rows


def _surcharge_names(charge_breakdown):
	"""Human list of the surcharge components present (name: amount), base excluded."""
	try:
		breakdown = json.loads(charge_breakdown or "{}")
	except Exception:
		return ""
	parts = [
		f"{k} {flt(v):g}"
		for k, v in breakdown.items()
		if k != "Product Net Amount" and flt(v)
	]
	return ", ".join(parts)
