# Copyright (c) 2026, Frappe and contributors
# For license information, please see license.txt

import re

import frappe
from frappe import _

# Transit süresi bu günden büyükse bozuk veri say (yanlış pickup_date/delivered_at).
MAX_TRANSIT_DAYS = 90

# Carrier adlarını tek biçime indir (dpd/DPD -> DPD, fedex -> FedEx) ki büyük/küçük
# harf ve alt-servis farkları ayrı satır oluşturmasın.
CARRIER_CANONICAL = {
	"dpd": "DPD",
	"fedex": "FedEx",
	"ups": "UPS",
	"dhl": "DHL",
	"dhl_express": "DHL Express",
	"gls": "GLS",
	"postnl": "PostNL",
	"bpost": "bpost",
	"colissimo": "Colissimo",
	"chronopost": "Chronopost",
	"sendcloud": "SendCloud",
}


def _canon(name):
	name = (name or "").strip()
	if not name:
		return ""
	key = name.lower().split(":", 1)[0].strip()  # "dpd:classic" -> "dpd"
	return CARRIER_CANONICAL.get(key, CARRIER_CANONICAL.get(name.lower(), name))


def execute(filters=None):
	filters = frappe._dict(filters or {})
	return get_columns(), get_data(filters)


def get_columns():
	return [
		{"label": _("Carrier"), "fieldname": "carrier", "fieldtype": "Data", "width": 160},
		{"label": _("Delivered"), "fieldname": "delivered", "fieldtype": "Int", "width": 100},
		{"label": _("Avg Transit (days)"), "fieldname": "avg_days", "fieldtype": "Float", "precision": "1", "width": 150},
		{"label": _("Min (days)"), "fieldname": "min_days", "fieldtype": "Float", "precision": "1", "width": 100},
		{"label": _("Max (days)"), "fieldname": "max_days", "fieldtype": "Float", "precision": "1", "width": 100},
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
	where = " and ".join(conds)

	rows = frappe.db.sql(
		f"""
		select carrier, custom_transit_days as days, custom_delivered_at as delivered_at,
			tracking_status
		from `tabShipment`
		where {where}
		""",
		values,
		as_dict=True,
	)

	carrier_filter = _canon(filters.get("carrier")) if filters.get("carrier") else None

	agg = {}
	for r in rows:
		# Bir Shipment birden çok kargo taşıyabilir (ör. "dpd, fedex"); her kuryeye böl.
		carriers = {_canon(c) for c in re.split(r"[,;]", r.carrier or "") if c.strip()}
		for carrier in carriers:
			if not carrier or (carrier_filter and carrier != carrier_filter):
				continue
			a = agg.setdefault(
				carrier,
				{"carrier": carrier, "days": [], "in_transit": 0, "returned": 0, "lost": 0},
			)
			# Transit süresi yalnızca GERÇEK teslimlerde (delivered_at dolu) ve makul
			# aralıkta (0..MAX) sayılır. custom_transit_days=0 + delivered_at yok =
			# "hesaplanmadı" (NOT NULL DEFAULT 0), bunları alma.
			if r.delivered_at and r.days is not None and 0 <= r.days <= MAX_TRANSIT_DAYS:
				a["days"].append(r.days)
			if r.tracking_status == "In Progress":
				a["in_transit"] += 1
			elif r.tracking_status == "Returned":
				a["returned"] += 1
			elif r.tracking_status == "Lost":
				a["lost"] += 1

	data = []
	for a in agg.values():
		days = a["days"]
		data.append(
			{
				"carrier": a["carrier"],
				"delivered": len(days),
				"avg_days": round(sum(days) / len(days), 1) if days else None,
				"min_days": min(days) if days else None,
				"max_days": max(days) if days else None,
				"in_transit": a["in_transit"],
				"returned": a["returned"],
				"lost": a["lost"],
			}
		)
	data.sort(key=lambda x: x["delivered"], reverse=True)
	return data
