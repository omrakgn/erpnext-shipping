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
		previous = None
		for s in history["statuses"]:
			stage = s["parent_status"]
			if stage == "delivery-failed":
				# Taşıyıcı aynı denemeyi iki-üç kez bildiriyor. Her satırı saymak
				# "4 deneme" gibi görünüp aslında 2 olan bir tabloya yol açıyordu —
				# ve deneme sayısı, iade olup olmadığına karar verirken bakılan
				# şeylerden biri.
				if previous != "delivery-failed":
					attempts += 1
				last_failed = s["at"] or last_failed
			elif stage == "delivered" and not delivered_at:
				delivered_at = s["at"]
			previous = stage

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


RETURN_REASONS = (
	"Refused by Customer",
	"Not Collected",
	"Address Problem",
	"Carrier Failure",
	"Other",
)


@frappe.whitelist()
def mark_returned_to_sender(shipment, reason=None, note=None):
	"""Record that a parcel came back to us, and stop tracking overriding it.

	`reason` matters beyond bookkeeping: a Carrier Failure can be claimed and a
	customer refusal cannot, and an Address Problem points at the order data
	rather than the carrier. Tracking cannot supply it — the carrier's failed
	attempts carry no reason — so it comes from whoever handled the case.
	"""
	if reason and reason not in RETURN_REASONS:
		frappe.throw(
			frappe._("Unknown return reason {0}. Expected one of: {1}").format(
				reason, ", ".join(RETURN_REASONS)
			)
		)

	values = {
		"custom_returned_to_sender": 1,
		"tracking_status": "Returned",
		# Teslim zamanı ve transit süresi paketin BİZE dönüşünü ölçüyordu; SLA ve
		# kargo performansı raporlarını bozmasınlar diye temizleniyor.
		# transit_days Float: sütun NOT NULL, None yazılamıyor — 0 kullanılıyor.
		# Alan zaten custom_delivered_at'e bağlı görünüyor, o boşken gizli kalır.
		"custom_delivered_at": None,
		"custom_transit_days": 0,
	}
	if reason:
		values["custom_return_reason"] = reason

	frappe.db.set_value("Shipment", shipment, values, update_modified=False)
	if note:
		frappe.get_doc("Shipment", shipment).add_comment("Comment", text=note)
	frappe.db.commit()
	return {"shipment": shipment, "status": "Returned", "reason": reason}


@frappe.whitelist()
def backfill_status_history(limit=200, only_missing=True, shipment=None):
	"""Store each shipment's full carrier ladder from SendCloud.

	The webhook only records what arrives from now on, so every shipment booked
	before it started has an empty history — including the returns we most need
	to look at. SendCloud's tracking endpoint serves the whole ladder
	retroactively, so it is fetched once and kept.

	Beyond readability this makes find_suspect_deliveries() work from local data
	instead of one API call per shipment.

	Returns {"checked", "stored", "empty", "errors"}.
	"""
	import json

	from erpnext_shipping.erpnext_shipping.doctype.sendcloud.sendcloud import SendCloudUtils

	sc = SendCloudUtils()
	results = {"checked": 0, "stored": 0, "empty": 0, "errors": []}

	filters = {"docstatus": 1, "awb_number": ["!=", ""]}
	if shipment:
		# Tek gönderi için form düğmesinden çağrılıyor: kayıtlı geçmiş olsa da
		# tazelensin, "eksikse getir" kuralı burada uygulanmaz.
		filters = {"name": shipment}
	elif only_missing and cint(only_missing):
		filters["custom_status_history"] = ["is", "not set"]

	rows = frappe.get_all(
		"Shipment", filters=filters, fields=["name", "awb_number"],
		order_by="creation desc", limit=limit,
	)

	for row in rows:
		results["checked"] += 1
		entries = []
		for awb in (row.awb_number or "").split(","):
			awb = awb.strip()
			if not awb:
				continue
			try:
				history = sc.get_tracking_history(awb)
			except Exception as e:
				results["errors"].append(f"{row.name} ({awb}): {e}")
				continue
			if not history:
				continue
			# Aynı olay birkaç satırda tekrar ediyor (biri mesajlı, biri boş).
			# Durum değişmediyse ve mesaj yoksa atla — merdiven okunur kalsın.
			previous = None
			for s in history["statuses"]:
				if not s["message"] and s["parent_status"] == previous:
					continue
				previous = s["parent_status"]
				entries.append({
					"parcel_id": "",
					"tracking": awb,
					"status": s["message"] or s["parent_status"],
					"parent_status": s["parent_status"],
					"at": s["at"],
				})

		if not entries:
			results["empty"] += 1
			continue

		entries.sort(key=lambda e: e["at"])
		frappe.db.set_value(
			"Shipment", row.name, "custom_status_history",
			json.dumps(entries[-200:]), update_modified=False,
		)
		results["stored"] += 1
		frappe.db.commit()

	if results["errors"]:
		frappe.log_error("Shipment status history backfill", "\n".join(results["errors"])[:100000])
	return results
