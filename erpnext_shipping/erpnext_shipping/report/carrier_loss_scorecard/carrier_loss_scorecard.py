# Copyright (c) 2026, Frappe Technologies and contributors
# For license information, please see license.txt
"""Carrier Loss Scorecard: loss-claim performance grouped by carrier — how many
claims, total goods value at risk, how much was recovered (compensation) and the
resulting net loss, plus a recovery rate to compare carriers."""
import frappe
from frappe.utils import flt

# Statuses that count as "closed" (a decision was reached).
CLOSED = ("Approved", "Rejected", "Paid", "Written Off", "Recovered")


def execute(filters=None):
	filters = filters or {}
	return get_columns(), get_data(filters)


def get_columns():
	return [
		{"label": "Carrier", "fieldname": "carrier", "fieldtype": "Data", "width": 160},
		{"label": "Claims", "fieldname": "claims", "fieldtype": "Int", "width": 80},
		{"label": "Open", "fieldname": "open_claims", "fieldtype": "Int", "width": 70},
		{"label": "Goods Value", "fieldname": "goods_value", "fieldtype": "Currency", "width": 120},
		{"label": "Claimed", "fieldname": "claim_amount", "fieldtype": "Currency", "width": 120},
		{"label": "Compensation", "fieldname": "compensation", "fieldtype": "Currency", "width": 120},
		{"label": "Net Loss", "fieldname": "net_loss", "fieldtype": "Currency", "width": 120},
		{"label": "Recovery %", "fieldname": "recovery_pct", "fieldtype": "Percent", "width": 100},
	]


def get_conditions(filters):
	conds = []
	values = []
	if filters.get("from_date"):
		conds.append("ifnull(incident_date, creation) >= %s")
		values.append(filters["from_date"])
	if filters.get("to_date"):
		conds.append("ifnull(incident_date, creation) <= %s")
		values.append(filters["to_date"])
	if filters.get("carrier"):
		conds.append("carrier like %s")
		values.append(f"%{filters['carrier']}%")
	return (" where " + " and ".join(conds)) if conds else "", values


def get_data(filters):
	where, values = get_conditions(filters)
	rows = frappe.db.sql(
		f"""
		select
			coalesce(nullif(trim(carrier), ''), 'Unknown') as carrier,
			count(*) as claims,
			sum(case when status not in ({",".join(["%s"] * len(CLOSED))}) then 1 else 0 end) as open_claims,
			sum(ifnull(goods_value, 0)) as goods_value,
			sum(ifnull(claim_amount, 0)) as claim_amount,
			sum(ifnull(compensation_amount, 0)) as compensation,
			sum(ifnull(net_loss, 0)) as net_loss
		from `tabShipment Loss Claim`
		{where}
		group by coalesce(nullif(trim(carrier), ''), 'Unknown')
		order by net_loss desc
		""",
		list(CLOSED) + values,
		as_dict=True,
	)
	for r in rows:
		claimed = flt(r.claim_amount)
		r["recovery_pct"] = (flt(r.compensation) / claimed * 100) if claimed else 0
	return rows
