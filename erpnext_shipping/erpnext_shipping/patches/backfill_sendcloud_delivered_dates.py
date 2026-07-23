# Copyright (c) 2026, Frappe Technologies and contributors
# For license information, please see license.txt
"""Fix historical shipments where the SendCloud delivered date (day-first,
"DD-MM-YYYY") was misparsed as month-first, giving a wrong custom_delivered_at and
Transit Days. The raw string is still stored per parcel in custom_tracking_details,
so we re-parse it correctly and recompute the delivered date + transit days.
"""
import json

import frappe
from frappe.utils import date_diff, flt, get_datetime

from erpnext_shipping.erpnext_shipping.doctype.sendcloud.sendcloud import parse_sendcloud_datetime


def execute():
	names = frappe.get_all(
		"Shipment", filters={"custom_tracking_details": ["is", "set"]}, pluck="name"
	)
	for name in names:
		try:
			_fix_one(name)
		except Exception:
			frappe.log_error(
				title="Backfill delivered date failed",
				message=f"Shipment: {name}\n{frappe.get_traceback()}",
			)
	frappe.db.commit()


def _fix_one(name):
	doc = frappe.get_doc("Shipment", name)
	try:
		parcels = json.loads(doc.custom_tracking_details or "[]")
	except Exception:
		return
	if not parcels:
		return

	updates = {}
	details_changed = False
	delivered_times = []
	all_delivered = True

	for p in parcels:
		iso = parse_sendcloud_datetime(p.get("delivered_at"))
		if p.get("delivered_at") != iso:
			p["delivered_at"] = iso
			details_changed = True
		if (p.get("status") or "") == "Delivered":
			if iso:
				delivered_times.append(iso)
		else:
			all_delivered = False

	if details_changed:
		updates["custom_tracking_details"] = json.dumps(parcels)

	new_delivered = max(delivered_times) if (all_delivered and delivered_times) else None
	if new_delivered:
		dt = get_datetime(new_delivered)
		cur = doc.get("custom_delivered_at")
		# Only touch the delivered date when its calendar date is wrong (avoid nudging
		# correct records by the seconds that date_updated may have drifted).
		if not cur or get_datetime(cur).date() != dt.date():
			updates["custom_delivered_at"] = dt
		eff = updates.get("custom_delivered_at", cur)
		if eff and doc.get("pickup_date"):
			transit = date_diff(get_datetime(eff).date(), doc.pickup_date)
			new_transit = transit if (transit is not None and 0 <= transit <= 90) else 0
			if flt(new_transit) != flt(doc.get("custom_transit_days")):
				updates["custom_transit_days"] = new_transit

	if updates:
		doc.db_set(updates, update_modified=False)
