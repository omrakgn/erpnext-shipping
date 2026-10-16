# Copyright (c) 2026, Frappe and contributors
# For license information, please see license.txt
"""Product Shipping Cost: what shipping a product costs, per unit.

Reads the product lines of Shipping Cost Entries: each line already holds the
product's share of that parcel's cost, split by declared weight
(docs/plans/kargo-maliyet-ekrani-ve-itiraz.md §4.2).

Cost adds up line by line. Units and parcels do not: one parcel is usually
billed on more than one line (the carrier's freight and SendCloud's fee per
parcel), and a multi-parcel shipment whose parcels cannot be told apart lists
the whole shipment on every parcel's line. Counting them per line would count
the same mattress two or three times, so units are counted once per parcel (or
per shipment, or per delivery note, whichever the line's contents came from).
"""

import frappe
from frappe import _
from frappe.utils import flt


def execute(filters=None):
	filters = frappe._dict(filters or {})
	rows = _rows(filters)
	data = aggregate(rows)
	return get_columns(), data, None, None, report_summary(data)


def get_columns():
	return [
		{"label": _("Item"), "fieldname": "item_code", "fieldtype": "Link", "options": "Item", "width": 170},
		{"label": _("Item Name"), "fieldname": "item_name", "fieldtype": "Data", "width": 220},
		{"label": _("Units"), "fieldname": "units", "fieldtype": "Float", "precision": "0", "width": 80},
		{"label": _("Parcels"), "fieldname": "parcels", "fieldtype": "Int", "width": 80},
		{
			"label": _("Shipping Cost"),
			"fieldname": "cost",
			"fieldtype": "Currency",
			"options": "currency",
			"width": 120,
		},
		{
			"label": _("Per Unit"),
			"fieldname": "per_unit",
			"fieldtype": "Currency",
			"options": "currency",
			"width": 100,
		},
		{
			"label": _("Of Which Surcharges"),
			"fieldname": "surcharges",
			"fieldtype": "Currency",
			"options": "currency",
			"width": 140,
		},
		{
			"label": _("Parcels With Weight/Size Surcharge"),
			"fieldname": "weight_surcharge_parcels",
			"fieldtype": "Int",
			"width": 120,
		},
		{"label": _("Share of All Shipping"), "fieldname": "share", "fieldtype": "Percent", "width": 120},
		{"label": _("Currency"), "fieldname": "currency", "fieldtype": "Data", "hidden": 1},
	]


def _rows(filters):
	kosul = ["e.scan_date between %(from_date)s and %(to_date)s"]
	degerler = {
		"from_date": filters.get("from_date") or "1900-01-01",
		"to_date": filters.get("to_date") or "2999-12-31",
	}
	for alan, sutun in (
		("billed_via", "e.billed_via"),
		("carrier", "e.carrier"),
		("item_code", "i.item_code"),
	):
		if filters.get(alan):
			kosul.append(f"{sutun} = %({alan})s")
			degerler[alan] = filters.get(alan)
	if filters.get("sales_channel"):
		kosul.append("sh.custom_sales_channel = %(sales_channel)s")
		degerler["sales_channel"] = filters.get("sales_channel")
	return frappe.db.sql(
		f"""
		select i.item_code, i.item_name, i.qty, i.share, i.amount,
			e.name as entry, e.parcel_number, e.shipment, e.delivery_note, e.contents_source,
			e.surcharge_amount, e.weight_surcharge, e.currency
		from `tabShipping Cost Entry Item` i
		join `tabShipping Cost Entry` e on e.name = i.parent and i.parenttype = 'Shipping Cost Entry'
		left join `tabShipment` sh on sh.name = e.shipment
		where {" and ".join(kosul)}
		""",
		degerler,
		as_dict=True,
	)


def _parcel_key(row):
	numara = (row.get("parcel_number") or "").strip()
	return numara.lstrip("0") or numara or row.get("entry")


def _unit_key(row):
	"""The physical goods a line describes, so two lines about them count once."""
	kaynak = row.get("contents_source")
	if kaynak == "Parcel":
		return ("parcel", _parcel_key(row))
	if kaynak in ("Shipment", "Shipment (all parcels)") and row.get("shipment"):
		return ("shipment", row.get("shipment"))
	if kaynak == "Delivery Note" and row.get("delivery_note"):
		return ("delivery_note", row.get("delivery_note"))
	return ("entry", row.get("entry"))


def aggregate(rows):
	urunler = {}
	for r in rows:
		u = urunler.setdefault(
			r.get("item_code"),
			{
				"item_code": r.get("item_code"),
				"item_name": r.get("item_name"),
				"cost": 0.0,
				"surcharges": 0.0,
				"currency": r.get("currency") or "EUR",
				"_units": {},
				"_parcels": set(),
				"_weight_parcels": set(),
			},
		)
		u["cost"] += flt(r.get("amount"))
		u["surcharges"] += flt(r.get("surcharge_amount")) * flt(r.get("share")) / 100.0
		anahtar = _unit_key(r)
		u["_units"][anahtar] = max(u["_units"].get(anahtar, 0.0), flt(r.get("qty")))
		u["_parcels"].add(_parcel_key(r))
		if r.get("weight_surcharge"):
			u["_weight_parcels"].add(_parcel_key(r))

	toplam = sum(u["cost"] for u in urunler.values())
	sonuc = []
	for u in urunler.values():
		adet = sum(u.pop("_units").values())
		u["units"] = adet
		u["parcels"] = len(u.pop("_parcels"))
		u["weight_surcharge_parcels"] = len(u.pop("_weight_parcels"))
		u["cost"] = flt(u["cost"], 2)
		u["surcharges"] = flt(u["surcharges"], 2)
		u["per_unit"] = flt(u["cost"] / adet, 2) if adet else 0.0
		u["share"] = flt(u["cost"] / toplam * 100, 2) if toplam else 0.0
		sonuc.append(u)
	sonuc.sort(key=lambda u: u["cost"], reverse=True)
	return sonuc


def report_summary(data):
	if not data:
		return []
	para = data[0].get("currency") or "EUR"
	maliyet = sum(flt(d["cost"]) for d in data)
	adet = sum(flt(d["units"]) for d in data)
	return [
		{"value": maliyet, "label": _("Shipping Cost"), "datatype": "Currency", "currency": para},
		{"value": adet, "label": _("Units Shipped"), "datatype": "Float"},
		{
			"value": flt(maliyet / adet, 2) if adet else 0,
			"label": _("Average Per Unit"),
			"datatype": "Currency",
			"currency": para,
		},
		{"value": len(data), "label": _("Products"), "datatype": "Int"},
	]
