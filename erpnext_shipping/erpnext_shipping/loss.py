# Copyright (c) 2026, Frappe Technologies and contributors
# For license information, please see license.txt
"""Presumed-lost shipment detection.

Carriers do not reliably mark parcels as "Lost", so we flag shipments that stay
undelivered beyond a (configurable) threshold as PRESUMED LOST — surfaced in a
report/card for a human to decide whether to open a Shipment Loss Claim.
"""
import frappe
from frappe.utils import add_days, cint, date_diff, getdate, nowdate

DEFAULT_PRESUMED_LOST_DAYS = 20


def presumed_lost_days():
	return cint(frappe.db.get_single_value("Shipment Settings", "presumed_lost_days")) or DEFAULT_PRESUMED_LOST_DAYS


def _presumed_lost_names(min_days):
	cutoff = add_days(nowdate(), -abs(min_days))
	return set(
		frappe.db.sql_list(
			"""
			select name from `tabShipment`
			where docstatus < 2
				and ifnull(tracking_status, '') != 'Delivered'
				and custom_delivered_at is null
				and pickup_date is not null
				and pickup_date <= %s
				and ifnull(status, '') not in ('Cancelled', 'Completed')
				and ifnull(awb_number, '') != ''
			""",
			cutoff,
		)
	)


def flag_presumed_lost():
	"""Daily job: keep the custom_presumed_lost flag in sync with the threshold."""
	names = _presumed_lost_names(presumed_lost_days())
	for name in names:
		if not frappe.db.get_value("Shipment", name, "custom_presumed_lost"):
			frappe.db.set_value("Shipment", name, "custom_presumed_lost", 1, update_modified=False)
	for name in frappe.get_all("Shipment", filters={"custom_presumed_lost": 1}, pluck="name"):
		if name not in names:
			frappe.db.set_value("Shipment", name, "custom_presumed_lost", 0, update_modified=False)
	frappe.db.commit()


# Bir teslimat denemesi başarısız olduktan sonra paketin bize dönmüş olabileceğini
# düşündüren gecikme. DPD iki-üç denemeden sonra iade ediyor; ikinci denemeden
# sonraki uzun sessizlik dönüş bacağıdır.
RETURN_SUSPECT_DAYS = 3


@frappe.whitelist()
def find_suspect_deliveries(limit=200, min_gap_days=RETURN_SUSPECT_DAYS):
	"""List shipments that read Delivered but may have come back to us.

	The carrier closes the return leg as "delivered" and its status vocabulary
	has no word for a return — the ladder reads at-sorting-centre,
	driver-on-route, delivered, exactly like a real delivery. Nothing in the data
	distinguishes the two, so this cannot decide; it produces a list for someone
	to check against what actually arrived in the warehouse.

	The signal is a failed delivery attempt followed by a long silence before the
	final "delivered". A genuine retry lands the next working day.

	Returns [{shipment, delivered_at, failed_at, gap_days, attempts}].
	"""
	from erpnext_shipping.erpnext_shipping.doctype.sendcloud.sendcloud import SendCloudUtils

	sc = SendCloudUtils()
	out = []

	rows = frappe.get_all(
		"Shipment",
		filters={
			"docstatus": 1,
			"tracking_status": "Delivered",
			"custom_returned_to_sender": 0,
			"awb_number": ["!=", ""],
		},
		fields=["name", "awb_number", "custom_delivered_at", "pickup_date"],
		order_by="custom_delivered_at desc",
		limit=limit,
	)

	for row in rows:
		awb = (row.awb_number or "").split(",")[0].strip()
		history = sc.get_tracking_history(awb)
		if not history:
			continue

		last_failed = None
		attempts = 0
		delivered_at = None
		for s in history["statuses"]:
			if s["parent_status"] == "delivery-failed":
				attempts += 1
				last_failed = s["at"] or last_failed
			elif s["parent_status"] == "delivered" and not delivered_at:
				delivered_at = s["at"]

		if not (last_failed and delivered_at):
			continue

		try:
			gap = date_diff(getdate(delivered_at), getdate(last_failed))
		except Exception:
			continue
		if gap is None or gap < cint(min_gap_days):
			continue

		out.append({
			"shipment": row.name,
			"delivered_at": delivered_at,
			"failed_at": last_failed,
			"gap_days": gap,
			"attempts": attempts,
		})

	return out


@frappe.whitelist()
def mark_returned_to_sender(shipment, note=None):
	"""Record that a parcel came back to us, and stop tracking overriding it."""
	values = {
		"custom_returned_to_sender": 1,
		"tracking_status": "Returned",
		# Teslim zamanı ve transit süresi paketin BİZE dönüşünü ölçüyordu; SLA ve
		# kargo performansı raporlarını bozmasınlar diye temizleniyor.
		"custom_delivered_at": None,
		"custom_transit_days": None,
	}
	frappe.db.set_value("Shipment", shipment, values, update_modified=False)
	if note:
		frappe.get_doc("Shipment", shipment).add_comment("Comment", text=note)
	frappe.db.commit()
	return {"shipment": shipment, "status": "Returned"}
