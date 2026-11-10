"""Shipping cards for the operations board (multichannel_core, /app/operations-board).

Listed in hooks.py under `operations_board_sources`. The board calls `cards(ctx)` and shows what
it returns; ctx carries `today`, `now` and the board's limits (`draft_after_days`). A card is a
dict of group, key, title, count, tone (critical / warning / neutral), hint, and the doctype and
list filters a click opens. Contract: multichannel_core/operations/sources.py `card`, hub
docs/plans/operasyon-ekrani.md.

A card counts work that waits for a person, never work that may rightly wait. The stored flags
`custom_is_delayed` and "Breached" also cover parcels that came back, are presumed lost or are
returns; the late card leaves those out. Shipments older than 120 days are never refreshed by
the SLA job, so they are left out too.
"""

import contextlib

import frappe
from frappe import _
from frappe.utils import add_days

GROUP = "Shipping"
SLA_FRESH_DAYS = 120
CLAIM_WARN_DAYS = 3
CLAIM_FILED = [
	"Submitted to Carrier",
	"Under Review",
	"Approved",
	"Rejected",
	"Paid",
	"Written Off",
	"Recovered",
]
# A return note booked within this many days of "delivered": the parcel never reached the customer.
BOUNCED_DAYS = 2
RETURNS_CACHE_KEY = "operations_board:shipping_returned_unflagged"
RETURNS_CACHE_SECONDS = 600


def card(key, title, count, tone, hint, doctype, filters, order=0):
	return {
		"group": GROUP,
		"key": key,
		"title": title,
		"count": count,
		"tone": tone,
		"hint": hint,
		"doctype": doctype,
		"filters": filters,
		"order": order,
	}


def count(doctype, filters):
	"""Counted as the list view counts (get_all keeps empty values for "!=" and "not in")."""
	rows = frappe.get_all(doctype, filters=filters, fields=["count(name) as n"])
	return int(rows[0].n or 0) if rows else 0


def cards(ctx):
	return [
		waiting_for_label(ctx),
		drafts(ctx),
		late(ctx),
		lost_without_claim(),
		claim_deadlines(ctx),
		disputes_late(ctx),
		returned_unflagged(),
	]


def waiting_for_label(ctx):
	filters = {
		"docstatus": 1,
		"status": ["!=", "Cancelled"],
		"shipment_id": ["is", "not set"],
		"awb_number": ["is", "not set"],
		# Own vehicle or a hired transport company needs no label.
		"shipping_transport_mode": ["is", "not set"],
		"pickup_date": ["<=", str(ctx.today)],
	}
	return card(
		"shipping.no_label",
		_("Shipments waiting for a label"),
		count("Shipment", filters),
		"warning",
		_("Submitted, pickup day reached, no label yet. Create the label or change the pickup date."),
		"Shipment",
		filters,
		order=1,
	)


def drafts(ctx):
	days = ctx.get("draft_after_days") or 1
	filters = {"docstatus": 0, "creation": ["<", str(add_days(ctx.today, -days))]}
	return card(
		"shipping.drafts",
		_("Shipments still in draft"),
		count("Shipment", filters),
		"warning",
		_("Saved but never submitted. Complete and submit them, or delete them."),
		"Shipment",
		filters,
		order=2,
	)


def late(ctx):
	filters = {
		"docstatus": 1,
		"status": ["!=", "Cancelled"],
		"custom_sla_status": "Breached",
		"custom_returned_to_sender": 0,
		"custom_presumed_lost": 0,
		"custom_is_return": 0,
		"tracking_status": ["not in", ["Delivered", "Returned", "Lost"]],
		"pickup_date": [">=", str(add_days(ctx.today, -SLA_FRESH_DAYS))],
	}
	return card(
		"shipping.late",
		_("Late deliveries"),
		count("Shipment", filters),
		"warning",
		_(
			"Past the carrier's usual delivery time and still on the way. Check the tracking and ask the carrier."
		),
		"Shipment",
		filters,
		order=3,
	)


def lost_without_claim():
	"""Presumed lost, and no loss claim of any status: nobody has started on it."""
	claimed = set(
		frappe.get_all("Shipment Loss Claim", filters={"shipment": ["is", "set"]}, pluck="shipment")
	)
	names = []
	for name in frappe.get_all(
		"Shipment",
		filters={
			"docstatus": 1,
			"status": ["!=", "Cancelled"],
			"custom_presumed_lost": 1,
			"custom_returned_to_sender": 0,
		},
		pluck="name",
		order_by="pickup_date asc",
	):
		if name not in claimed:
			names.append(name)
	return card(
		"shipping.lost_no_claim",
		_("Presumed lost, no claim opened"),
		len(names),
		"critical",
		_("No news from the carrier for weeks. Open a loss claim before the carrier's deadline passes."),
		"Shipment",
		{"name": ["in", names]},
		order=4,
	)


def claim_deadlines(ctx):
	filters = {
		"status": ["not in", CLAIM_FILED],
		"claim_deadline": ["<=", str(add_days(ctx.today, CLAIM_WARN_DAYS))],
		"submitted_date": ["is", "not set"],
	}
	return card(
		"shipping.claim_deadline",
		_("Loss claims due within {0} days").format(CLAIM_WARN_DAYS),
		count("Shipment Loss Claim", filters),
		"critical",
		_("Not filed with the carrier yet and the deadline is near or past. File them now."),
		"Shipment Loss Claim",
		filters,
		order=5,
	)


def disputes_late(ctx):
	today = str(ctx.today)
	names = frappe.get_all(
		"Carrier Invoice Dispute", filters={"status": "Draft", "deadline": ["<", today]}, pluck="name"
	)
	for name in frappe.get_all(
		"Carrier Invoice Dispute", filters={"status": "Sent", "answer_due": ["<", today]}, pluck="name"
	):
		if name not in names:
			names.append(name)
	return card(
		"shipping.disputes_late",
		_("Carrier invoice disputes running late"),
		len(names),
		"warning",
		_(
			"A draft past its deadline, or a sent dispute the carrier has not answered in time. Send it, or chase the carrier."
		),
		"Carrier Invoice Dispute",
		{"name": ["in", names]},
		order=6,
	)


def returned_unflagged():
	names = cached_returned_shipments()
	return card(
		"shipping.returned_unflagged",
		_("Returned parcels still marked delivered"),
		len(names),
		"warning",
		_(
			"A return note was booked within two days of delivery: the parcel came back. Mark the shipment Returned to Us."
		),
		"Shipment",
		{"name": ["in", names]},
		order=7,
	)


def cached_returned_shipments():
	"""The warehouse-side match, kept for 10 minutes: it walks up to 200 return notes."""
	try:
		hit = frappe.cache.get_value(RETURNS_CACHE_KEY)
	except Exception:  # noqa: BLE001 - no cache, then compute
		hit = None
	if hit is not None:
		return hit
	from erpnext_shipping.erpnext_shipping.loss import find_returns_without_shipment_flag

	names = []
	for row in find_returns_without_shipment_flag(limit=200):
		gap = row.get("gap_days")
		if gap is not None and 0 <= gap <= BOUNCED_DAYS and row["shipment"] not in names:
			names.append(row["shipment"])
	with contextlib.suppress(Exception):  # the card still works without the cache
		frappe.cache.set_value(RETURNS_CACHE_KEY, names, expires_in_sec=RETURNS_CACHE_SECONDS)
	return names
