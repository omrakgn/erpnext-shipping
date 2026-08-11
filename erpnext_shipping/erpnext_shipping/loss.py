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
				-- Takip beslemesi kesilmiş gönderi kayıp değildir. Nisan 2026'da
				-- 147 gönderinin 147'si bu yüzden kayıp adayı işaretlenmişti ve
				-- liste hiçbir işe yaramıyordu.
				and ifnull(custom_tracking_stalled, 0) = 0
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


@frappe.whitelist()
def find_returns_without_shipment_flag(limit=200):
	"""Return Delivery Notes whose shipment still reads Delivered.

	find_suspect_deliveries() starts from the carrier and only sees parcels that
	recorded a failed attempt. A parcel turned back for a bad address, or refused
	at the door and sent straight home, leaves no failed attempt at all — the
	ladder reads like a clean delivery and the scan never looks at it.

	This starts from the warehouse instead: goods came back, a return note was
	written, yet the shipment was never marked. That evidence is stronger than
	anything the carrier reports, because it is the parcel physically arriving.

	`gap_days` separates the two cases it finds: a return booked within a day or
	two of the "delivery" is the parcel coming home, while one booked weeks later
	is a customer who received the goods and sent them back — a real delivery
	followed by a real return, and not something to re-label.

	Returns [{shipment, delivered_at, return_note, return_date, gap_days, customer}].
	"""
	out = []

	returns = frappe.get_all(
		"Delivery Note",
		filters={"is_return": 1, "docstatus": 1},
		fields=["name", "posting_date", "return_against", "customer"],
		order_by="posting_date desc",
		limit=limit,
	)

	for ret in returns:
		originals = set()
		if ret.return_against:
			originals.add(ret.return_against)
		else:
			# return_against boşsa sipariş üzerinden orijinal irsaliyeleri bul.
			for so in frappe.get_all(
				"Delivery Note Item", filters={"parent": ret.name},
				pluck="against_sales_order",
			):
				if not so:
					continue
				for dn in frappe.get_all(
					"Delivery Note Item",
					filters={"against_sales_order": so, "docstatus": 1},
					pluck="parent",
				):
					if dn != ret.name:
						originals.add(dn)

		if not originals:
			continue

		for shipment in frappe.get_all(
			"Shipment Delivery Note",
			filters={"delivery_note": ["in", list(originals)]},
			pluck="parent",
			distinct=True,
		):
			sh = frappe.db.get_value(
				"Shipment", shipment,
				["name", "tracking_status", "custom_returned_to_sender", "custom_delivered_at", "docstatus"],
				as_dict=True,
			)
			if not sh or sh.docstatus != 1:
				continue
			if sh.tracking_status != "Delivered" or sh.custom_returned_to_sender:
				continue

			gap = None
			if sh.custom_delivered_at:
				try:
					gap = date_diff(getdate(ret.posting_date), getdate(sh.custom_delivered_at))
				except Exception:
					gap = None

			out.append({
				"shipment": sh.name,
				"delivered_at": str(sh.custom_delivered_at or ""),
				"return_note": ret.name,
				"return_date": str(ret.posting_date),
				"gap_days": gap,
				"customer": ret.customer,
			})

	# En küçük fark önce: dönüş bacağı olma ihtimali en yüksek olanlar başta.
	out.sort(key=lambda r: (r["gap_days"] is None, r["gap_days"]))
	return out


# Taşıyıcının son bildiriminden sonra bu kadar gün geçtiyse besleme kesilmiş
# sayılır. DPD'de teslimat birkaç gün sürüyor; üç hafta boyunca tek bir olay
# gelmemesi paketin hareketsiz olduğu anlamına gelmiyor, haberin kesildiği
# anlamına geliyor.
TRACKING_STALE_DAYS = 21


@frappe.whitelist()
def flag_stalled_tracking(older_than_days=TRACKING_STALE_DAYS, limit=500, dry_run=False):
	"""Mark shipments the carrier stopped reporting on.

	Their last status is not an outcome — "En route to sorting center" is where
	the feed stopped, not where the parcel is. Treating that as undelivered made
	every one of them a presumed-loss candidate: in April 2026 the DPD own-contract
	feed failed and 101 delivered parcels were flagged, which is the same as having
	no loss detection at all.

	Decided from the carrier's own ladder rather than our record: a parcel with no
	tracking data at all, or whose last event is older than the threshold, has
	stopped being reported on.

	Returns {"checked", "flagged", "still_moving", "names"}.
	"""
	from erpnext_shipping.erpnext_shipping.doctype.sendcloud.sendcloud import SendCloudUtils

	sc = SendCloudUtils()
	cutoff = add_days(nowdate(), -abs(cint(older_than_days)))
	results = {"checked": 0, "flagged": 0, "still_moving": 0, "names": []}

	rows = frappe.get_all(
		"Shipment",
		filters={
			"docstatus": 1,
			"tracking_status": ["not in", ["Delivered", "Returned", "Lost"]],
			"custom_tracking_stalled": 0,
			"custom_label_removed": 0,
			"awb_number": ["!=", ""],
			"pickup_date": ["<", cutoff],
		},
		fields=["name", "awb_number"],
		order_by="pickup_date asc",
		limit=limit,
	)

	for row in rows:
		results["checked"] += 1
		awb = (row.awb_number or "").split(",")[0].strip()
		history = sc.get_tracking_history(awb)

		last_event = None
		if history:
			for s in history["statuses"]:
				if s["at"]:
					last_event = s["at"]

		# Takip verisi hiç yoksa da besleme kesilmiş demektir — taşıyıcı kaydı
		# düşürmüş ve bir daha bilgi gelmeyecek.
		stale = True
		if last_event:
			try:
				stale = getdate(last_event) < getdate(cutoff)
			except Exception:
				stale = True

		if not stale:
			results["still_moving"] += 1
			continue

		results["flagged"] += 1
		results["names"].append(row.name)
		if not dry_run:
			frappe.db.set_value(
				"Shipment", row.name,
				{"custom_tracking_stalled": 1, "custom_presumed_lost": 0},
				update_modified=False,
			)

	if not dry_run:
		frappe.db.commit()
	return results
