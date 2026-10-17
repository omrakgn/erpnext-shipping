# Copyright (c) 2026, Frappe and contributors
# For license information, please see license.txt
"""
Carrier invoice disputes.

A dispute goes to whoever invoiced the charge (`billed_via`), not to whoever
carried the parcel: a DPD parcel booked on SendCloud's contract is disputed
with SendCloud. One dispute per party and carrier invoice; a cost entry is in
at most one open dispute.

The carrier's invoice is already paid while a dispute runs. An accepted
dispute ends with the carrier's credit note, booked here as a return Purchase
Invoice (debit note) against the original. Nothing is put on hold.

Design: docs/plans/kargo-maliyet-ekrani-ve-itiraz.md §4.4, §4.5.
"""

import json
from contextlib import nullcontext

import frappe
from frappe import _
from frappe.utils import add_days, cint, flt, fmt_money, formatdate, getdate, today

DOCTYPE = "Carrier Invoice Dispute"
ENTRY = "Shipping Cost Entry"
SETTINGS = "Shipping Cost Settings"

# Not finished yet: a cost entry in one of these cannot join a second dispute.
OPEN_STATUSES = ("Draft", "Sent", "Accepted", "Partly Accepted")
DECISIONS = ("Accepted", "Partly Accepted", "Rejected")

# Candidate thresholds (plan §2, decision 7).
CANDIDATE_DAYS = 120
CANDIDATE_LIMIT = 500
WEIGHT_DIFF_KG = 1
WEIGHT_DIFF_PCT = 10
PRICE_VARIANCE_PCT = 15
# Only these contents describe one parcel; "Shipment (all parcels)" is the
# whole shipment and cannot be compared with one parcel's billed weight.
PARCEL_SOURCES = ("Parcel", "Shipment")

REMIND_BEFORE_DAYS = 3
DEFAULT_ANSWER_DAYS = 14

REASON_WEIGHT_SURCHARGE = "Weight or size surcharge"
REASON_WEIGHT = "Billed weight above declared"
REASON_DUPLICATE = "Duplicate charge"
REASON_PRICE = "Price above agreed rate"
REASON_CANCELLED = "Cancelled or unsent parcel"
REASON_OTHER = "Other"

# DPD names its weight surcharges as columns, SendCloud as line descriptions.
WEIGHT_COMPONENTS = ("Oversized/Overweight", "Heavy Weight parcels 20-31,5kg")
WEIGHT_HINTS = (
	"heavy weight",
	"overschrijden afmetingen",
	"oversized",
	"overweight",
	"weight surcharge",
	"dimensional",
)

ENTRY_FIELDS = [
	"name",
	"parcel_number",
	"invoice_number",
	"carrier",
	"billed_via",
	"scan_date",
	"total_net_amount",
	"surcharge_amount",
	"currency",
	"weight_surcharge",
	"reweigh",
	"is_correction",
	"invoicing_weight",
	"corrected_weight",
	"length",
	"width",
	"height",
	"sendcloud_weight",
	"contents_source",
	"contents_summary",
	"shipment",
	"delivery_note",
	"purchase_invoice",
	"charge_breakdown",
	"dispute",
	"dispute_status",
]


# ---------------------------------------------------------------------------
# Pure helpers (tested without a site)
# ---------------------------------------------------------------------------


def billed_by_of(entry):
	"""Who invoiced the line. Older DPD imports left `billed_via` empty: DPD itself."""
	return (entry.get("billed_via") or entry.get("carrier") or "").strip()


def same_party(a, b):
	return (a or "").strip().lower() == (b or "").strip().lower()


def invoiced_weight(entry):
	corrected = flt(entry.get("corrected_weight"))
	return corrected if corrected > 0 else flt(entry.get("invoicing_weight"))


def declared_weight(entry, items):
	"""
	(weight, source) we told the carrier, or (None, None).

	The label weight wins: it is what the carrier received. Otherwise the
	products' declared weights, but only when every product has one and the
	contents describe this one parcel.
	"""
	label = flt(entry.get("sendcloud_weight"))
	if label > 0:
		return label, "label"
	if entry.get("contents_source") not in PARCEL_SOURCES:
		return None, None
	rows = items or []
	if not rows or any(flt(r.get("unit_weight")) <= 0 for r in rows):
		return None, None
	return sum(flt(r.get("unit_weight")) * flt(r.get("qty")) for r in rows), "products"


def weight_excess(declared, billed):
	"""Billed weight over declared by more than the threshold, else None."""
	if not declared or declared <= 0 or not billed or billed <= 0:
		return None
	diff = billed - declared
	if diff <= 0:
		return None
	if diff > WEIGHT_DIFF_KG or diff / declared * 100 > WEIGHT_DIFF_PCT:
		return diff
	return None


def breakdown(entry):
	try:
		data = json.loads(entry.get("charge_breakdown") or "{}")
	except (TypeError, ValueError):
		return {}
	return data if isinstance(data, dict) else {}


def weight_surcharge_amount(entry):
	"""The weight or size surcharge lines on the invoice: the amount a dispute asks back."""
	total = 0.0
	for label, value in breakdown(entry).items():
		key = (label or "").lower()
		is_weight = label in WEIGHT_COMPONENTS or any(h in key for h in WEIGHT_HINTS)
		if is_weight and flt(value) > 0:
			total += flt(value)
	return flt(total, 2)


def base_charge(entry):
	"""The freight itself, without surcharges."""
	return flt(entry.get("total_net_amount")) - flt(entry.get("surcharge_amount"))


def entry_reasons(entry, items=None, shipment=None, duplicate_of=None, freight_lines=1):
	"""
	Every reason this charge looks wrong, most decisive first.

	Each reason is a dict: reason, suggested (amount to dispute), evidence
	(one sentence the carrier will read). `shipment` carries the label and
	quote facts, `duplicate_of` the other invoice that billed the same freight,
	`freight_lines` how many freight charges the shipment has.
	"""
	out = []
	total = flt(entry.get("total_net_amount"), 2)
	currency = entry.get("currency") or "EUR"
	shipment = shipment or {}

	if shipment.get("label_removed"):
		out.append(
			{
				"reason": REASON_CANCELLED,
				"suggested": total,
				"evidence": _("The label was cancelled and the parcel was not sent."),
			}
		)

	if duplicate_of and base_charge(entry) > 0:
		out.append(
			{
				"reason": REASON_DUPLICATE,
				"suggested": total,
				"evidence": _("The same parcel was already billed on invoice {0}.").format(duplicate_of),
			}
		)

	declared, source = declared_weight(entry, items)
	billed = invoiced_weight(entry)
	surcharge = weight_surcharge_amount(entry)
	weight_text = weight_evidence(entry, declared, source, billed)

	if entry.get("weight_surcharge") or entry.get("reweigh"):
		out.append(
			{
				"reason": REASON_WEIGHT_SURCHARGE,
				"suggested": surcharge,
				"evidence": weight_text,
			}
		)

	if weight_excess(declared, billed):
		out.append(
			{
				"reason": REASON_WEIGHT,
				"suggested": surcharge,
				"evidence": weight_text,
			}
		)

	pct = flt(shipment.get("variance_pct"))
	variance = flt(shipment.get("variance"))
	if pct > PRICE_VARIANCE_PCT and variance > 0 and base_charge(entry) > 0:
		# Only a shipment with one freight charge says how much of the excess is
		# this line's; otherwise the amount is left to the user.
		suggested = min(flt(variance, 2), total) if freight_lines == 1 else 0
		out.append(
			{
				"reason": REASON_PRICE,
				"suggested": suggested,
				"evidence": _("Quoted {0} for the shipment, billed {1} ({2}% more).").format(
					fmt_money(flt(shipment.get("quote")), currency=currency),
					fmt_money(flt(shipment.get("quote")) + variance, currency=currency),
					cint(round(pct)),
				),
			}
		)
	return out


def weight_evidence(entry, declared, source, billed):
	parts = []
	if declared and billed:
		if source == "label":
			parts.append(_("Our label says {0} kg, billed {1} kg.").format(_kg(declared), _kg(billed)))
		else:
			parts.append(
				_("The products weigh {0} kg as declared, billed {1} kg.").format(_kg(declared), _kg(billed))
			)
	elif billed:
		parts.append(_("Billed {0} kg.").format(_kg(billed)))
	dims = [flt(entry.get(f)) for f in ("length", "width", "height")]
	if all(dims):
		parts.append(_("Billed size {0} x {1} x {2} cm.").format(*[_num(d) for d in dims]))
	return " ".join(parts)


def _num(value):
	value = flt(value, 1)
	return str(int(value)) if value == int(value) else str(value)


def _kg(value):
	return _num(value)


def distribute(total, amounts):
	"""Split `total` over `amounts` in proportion, to the cent; the rounding rest goes to the largest."""
	total = flt(total, 2)
	base = sum(flt(a) for a in amounts)
	if base <= 0 or not amounts:
		return [0.0 for _a in amounts]
	out = [flt(total * flt(a) / base, 2) for a in amounts]
	rest = flt(total - sum(out), 2)
	if rest:
		largest = max(range(len(amounts)), key=lambda i: flt(amounts[i]))
		out[largest] = flt(out[largest] + rest, 2)
	return out


def allocate(per_cost_center, rows):
	"""
	Spread credited amounts over the original invoice's lines.

	`per_cost_center`: {cost center: amount}. `rows`: [{idx, cost_center,
	room}], room being what the original line charged. A line never gets more
	than it charged. An amount whose cost center has no line (the delivery's
	cost center changed after invoicing) goes to the lines with room left.
	Raises ValueError with the amount that does not fit.
	"""
	room = {r["idx"]: flt(r["room"], 2) for r in rows}
	plan = {}
	rest = 0.0

	def take(idx, amount):
		share = flt(min(amount, room[idx]), 2)
		if share > 0:
			plan[idx] = flt(plan.get(idx, 0) + share, 2)
			room[idx] = flt(room[idx] - share, 2)
		return flt(amount - share, 2)

	for cost_center, amount in sorted(per_cost_center.items(), key=lambda kv: -flt(kv[1])):
		amount = flt(amount, 2)
		for row in sorted(
			(r for r in rows if r["cost_center"] == cost_center), key=lambda r: -room[r["idx"]]
		):
			if amount <= 0:
				break
			amount = take(row["idx"], amount)
		rest = flt(rest + amount, 2)

	for row in sorted(rows, key=lambda r: -room[r["idx"]]):
		if rest <= 0:
			break
		rest = take(row["idx"], rest)

	if rest > 0.005:
		raise ValueError(rest)
	return plan


# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------


def contact_for(billed_by):
	"""The Invoice Disputes row for this party, or None."""
	for row in frappe.get_single(SETTINGS).get("dispute_contacts") or []:
		if same_party(row.billed_by, billed_by):
			return row
	return None


def _language(lang):
	try:
		from frappe.translate import print_language
	except ImportError:
		return nullcontext()
	return print_language(lang or "en")


# ---------------------------------------------------------------------------
# Candidates
# ---------------------------------------------------------------------------


def _shipment_facts(names):
	if not names:
		return {}
	meta = frappe.get_meta("Shipment")
	wanted = {
		"label_removed": "custom_label_removed",
		"variance_pct": "custom_cost_variance_pct",
		"variance": "custom_cost_variance",
		"quote": "shipment_amount",
	}
	fields = ["name"] + [f for f in wanted.values() if meta.has_field(f) or f == "shipment_amount"]
	out = {}
	for row in frappe.get_all("Shipment", filters={"name": ["in", list(names)]}, fields=fields):
		out[row.name] = {key: row.get(field) for key, field in wanted.items()}
	return out


def _items_by_entry(entries):
	"""Declared weights, read only for the entries that have no label weight."""
	names = [
		e.name
		for e in entries
		if flt(e.get("sendcloud_weight")) <= 0 and e.get("contents_source") in PARCEL_SOURCES
	]
	out = {}
	if not names:
		return out
	for row in frappe.get_all(
		"Shipping Cost Entry Item",
		filters={"parenttype": ENTRY, "parent": ["in", names]},
		fields=["parent", "qty", "unit_weight"],
	):
		out.setdefault(row.parent, []).append(row)
	return out


def _duplicates(entries):
	"""
	{entry: invoice that billed the same freight first}.

	Same parcel, same party, freight on more than one invoice. Surcharge-only
	lines (a redelivery, a weight correction billed later) are not freight and
	never count. Reads the whole table: the first invoice may be older than the
	candidate window.
	"""
	parcels = sorted({e.parcel_number for e in entries if e.get("parcel_number")})
	if not parcels:
		return {}
	rows = frappe.db.sql(
		"""
		SELECT name, parcel_number, invoice_number, carrier, billed_via, scan_date
		FROM `tabShipping Cost Entry`
		WHERE parcel_number IN %(parcels)s
		  AND is_correction = 0
		  AND IFNULL(total_net_amount, 0) - IFNULL(surcharge_amount, 0) > 0
		""",
		{"parcels": tuple(parcels)},
		as_dict=True,
	)
	groups = {}
	for row in rows:
		key = (row.parcel_number, billed_by_of(row).lower())
		groups.setdefault(key, []).append(row)
	out = {}
	for group in groups.values():
		if len({r.invoice_number for r in group}) < 2:
			continue
		group.sort(key=lambda r: (str(r.scan_date or ""), r.name))
		first = group[0]
		for row in group[1:]:
			if row.invoice_number != first.invoice_number:
				out[row.name] = first.invoice_number
	return out


def evaluate(entries):
	"""Reasons for each entry: {entry name: [reason, ...]}."""
	items = _items_by_entry(entries)
	shipments = _shipment_facts({e.shipment for e in entries if e.get("shipment")})
	duplicates = _duplicates(entries)
	freight = {}
	for e in entries:
		if e.get("shipment") and base_charge(e) > 0:
			freight[e.shipment] = freight.get(e.shipment, 0) + 1
	out = {}
	for e in entries:
		out[e.name] = entry_reasons(
			e,
			items=items.get(e.name),
			shipment=shipments.get(e.get("shipment")),
			duplicate_of=duplicates.get(e.name),
			freight_lines=freight.get(e.get("shipment"), 1),
		)
	return out


@frappe.whitelist()
def dispute_candidates(days=CANDIDATE_DAYS):
	"""Charges from the last `days` days that look wrong and are in no dispute yet."""
	frappe.has_permission(ENTRY, "read", throw=True)
	since = add_days(today(), -cint(days or CANDIDATE_DAYS))
	entries = frappe.get_all(
		ENTRY,
		filters={
			"scan_date": [">=", since],
			"total_net_amount": [">", 0],
			"is_correction": 0,
			"dispute": ["is", "not set"],
			"invoice_number": ["is", "set"],
		},
		fields=ENTRY_FIELDS,
		order_by="scan_date desc, name desc",
		limit_page_length=0,
	)
	reasons = evaluate(entries)
	out = []
	for e in entries:
		found = reasons.get(e.name) or []
		if not found:
			continue
		first = found[0]
		out.append(
			{
				"name": e.name,
				"parcel_number": e.parcel_number,
				"invoice_number": e.invoice_number,
				"billed_by": billed_by_of(e),
				"carrier": e.carrier,
				"scan_date": e.scan_date,
				"currency": e.currency or "EUR",
				"billed": flt(e.total_net_amount, 2),
				"reason": first["reason"],
				"reasons": [r["reason"] for r in found],
				"suggested": first["suggested"],
				"evidence": first["evidence"],
				"contents_summary": e.contents_summary,
				"shipment": e.shipment,
			}
		)
		if len(out) >= CANDIDATE_LIMIT:
			break
	return out


# ---------------------------------------------------------------------------
# Creating disputes
# ---------------------------------------------------------------------------


def _open_draft(billed_by, invoice_number):
	for row in frappe.get_all(
		DOCTYPE,
		filters={"invoice_number": invoice_number, "status": "Draft"},
		fields=["name", "billed_by"],
	):
		if same_party(row.billed_by, billed_by):
			return frappe.get_doc(DOCTYPE, row.name)
	return None


def line_values(entry, reasons, items=None):
	declared, source = declared_weight(entry, items)
	first = (reasons or [None])[0] or {
		"reason": REASON_OTHER,
		"suggested": 0,
		"evidence": weight_evidence(entry, declared, source, invoiced_weight(entry)),
	}
	return {
		"cost_entry": entry.name,
		"parcel_number": entry.parcel_number,
		"reason": first["reason"],
		"billed_amount": flt(entry.total_net_amount, 2),
		"disputed_amount": flt(first["suggested"], 2),
		"billed_weight": invoiced_weight(entry) or None,
		"declared_weight": flt(declared, 2) or None,
		"evidence": first["evidence"],
		"shipment": entry.shipment,
		"scan_date": entry.scan_date,
		"currency": entry.currency or "EUR",
	}


@frappe.whitelist()
def create_disputes(cost_entries):
	"""
	Put the chosen charges into disputes, one per party and carrier invoice.

	A charge joins the open draft for its invoice when there is one, so picking
	candidates twice does not open a second dispute for the same invoice.
	Returns the disputes and the charges left out, with why.
	"""
	frappe.has_permission(DOCTYPE, "create", throw=True)
	names = frappe.parse_json(cost_entries) if isinstance(cost_entries, str) else cost_entries
	names = list(dict.fromkeys(n for n in (names or []) if n))
	if not names:
		frappe.throw(_("Choose at least one charge."))

	entries = frappe.get_all(ENTRY, filters={"name": ["in", names]}, fields=ENTRY_FIELDS)
	groups, skipped = {}, []
	for e in sorted(entries, key=lambda x: names.index(x.name)):
		if e.dispute and e.dispute_status in OPEN_STATUSES:
			skipped.append({"name": e.name, "why": _("already in dispute {0}").format(e.dispute)})
			continue
		if not e.invoice_number:
			skipped.append({"name": e.name, "why": _("no carrier invoice number")})
			continue
		if not billed_by_of(e):
			skipped.append({"name": e.name, "why": _("not known who billed it")})
			continue
		groups.setdefault((billed_by_of(e).lower(), e.invoice_number), []).append(e)

	disputes = []
	for (_party, invoice_number), group in groups.items():
		billed_by = billed_by_of(group[0])
		doc = _open_draft(billed_by, invoice_number)
		if not doc:
			doc = frappe.new_doc(DOCTYPE)
			doc.billed_by = billed_by
			doc.invoice_number = invoice_number
		present = {r.cost_entry for r in doc.lines}
		fresh = [e for e in group if e.name not in present]
		items = _items_by_entry(fresh)
		contact = contact_for(billed_by)
		# The explanation is sent as written, so it is written in the party's language.
		with _language(contact.language if contact else doc.language):
			reasons = evaluate(fresh)
			for e in fresh:
				doc.append("lines", line_values(e, reasons.get(e.name), items.get(e.name)))
		doc.save()
		disputes.append(doc.name)
	return {"disputes": disputes, "skipped": skipped}


# ---------------------------------------------------------------------------
# Controller logic
# ---------------------------------------------------------------------------


def validate_dispute(doc):
	doc.billed_by = (doc.billed_by or "").strip()
	doc.invoice_number = (doc.invoice_number or "").strip()
	_check_lines(doc)
	_fill_from_entries(doc)
	if doc.is_new():
		_apply_contact(doc)
	_set_totals(doc)
	if not doc.deadline and doc.invoice_date:
		contact = contact_for(doc.billed_by)
		if contact and cint(contact.dispute_days) > 0:
			doc.deadline = add_days(doc.invoice_date, cint(contact.dispute_days))
	doc.title = f"{doc.billed_by} {doc.invoice_number}".strip()


def _check_lines(doc):
	seen = set()
	names = [r.cost_entry for r in doc.lines if r.cost_entry]
	entries = {}
	if names:
		for e in frappe.get_all(ENTRY, filters={"name": ["in", names]}, fields=ENTRY_FIELDS):
			entries[e.name] = e

	for row in doc.lines:
		if row.cost_entry in seen:
			frappe.throw(
				_("Row {0}: parcel {1} is listed twice.").format(row.idx, row.parcel_number or row.cost_entry)
			)
		seen.add(row.cost_entry)
		entry = entries.get(row.cost_entry)
		if not entry:
			continue
		if (
			not same_party(billed_by_of(entry), doc.billed_by)
			or (entry.invoice_number or "") != doc.invoice_number
		):
			frappe.throw(
				_(
					"Row {0}: parcel {1} was billed by {2} on invoice {3}. This dispute is for {4} invoice {5}."
				).format(
					row.idx,
					entry.parcel_number,
					billed_by_of(entry) or "?",
					entry.invoice_number or "?",
					doc.billed_by,
					doc.invoice_number,
				)
			)
		if entry.dispute and entry.dispute != doc.name and entry.dispute_status in OPEN_STATUSES:
			frappe.throw(
				_("Row {0}: parcel {1} is already in dispute {2}.").format(
					row.idx, entry.parcel_number, entry.dispute
				)
			)
		# Fetched values are written on insert only; keep them true to the entry.
		row.parcel_number = entry.parcel_number
		row.billed_amount = flt(entry.total_net_amount, 2)
		row.shipment = entry.shipment
		row.scan_date = entry.scan_date
		row.currency = entry.currency or "EUR"
		if flt(row.disputed_amount) < 0:
			frappe.throw(_("Row {0}: the disputed amount cannot be negative.").format(row.idx))
		if flt(row.billed_amount) > 0 and flt(row.disputed_amount) > flt(row.billed_amount) + 0.005:
			frappe.throw(
				_("Row {0}: parcel {1} disputes {2}, more than the {3} billed.").format(
					row.idx,
					row.parcel_number,
					fmt_money(row.disputed_amount, currency=row.currency),
					fmt_money(row.billed_amount, currency=row.currency),
				)
			)
		if flt(row.accepted_amount) < 0 or flt(row.accepted_amount) > flt(row.disputed_amount) + 0.005:
			frappe.throw(
				_("Row {0}: the accepted amount must be between 0 and the disputed amount.").format(row.idx)
			)
	doc._entries = entries


def _fill_from_entries(doc):
	entries = list(getattr(doc, "_entries", {}).values())
	if entries:
		doc.currency = entries[0].currency or doc.currency or "EUR"
		doc.carrier = ", ".join(sorted({e.carrier for e in entries if e.carrier}))
		if not doc.purchase_invoice:
			doc.purchase_invoice = next((e.purchase_invoice for e in entries if e.purchase_invoice), None)
	if doc.purchase_invoice and not doc.supplier:
		doc.supplier = frappe.db.get_value("Purchase Invoice", doc.purchase_invoice, "supplier")
	if not doc.invoice_date and doc.invoice_number:
		if doc.purchase_invoice:
			doc.invoice_date = frappe.db.get_value("Purchase Invoice", doc.purchase_invoice, "bill_date")
		if not doc.invoice_date:
			# The carrier invoices after the last scan on it: the closest date we hold.
			doc.invoice_date = frappe.db.sql(
				"SELECT MAX(scan_date) FROM `tabShipping Cost Entry` WHERE invoice_number = %s",
				doc.invoice_number,
			)[0][0]


def _apply_contact(doc):
	contact = contact_for(doc.billed_by)
	if not contact:
		return
	doc.method = doc.method or contact.method
	doc.dispute_email = doc.dispute_email or contact.email
	doc.portal_url = doc.portal_url or contact.portal_url
	doc.language = contact.language or doc.language or "en"


def _set_totals(doc):
	doc.total_billed = flt(sum(flt(r.billed_amount) for r in doc.lines), 2)
	doc.total_disputed = flt(sum(flt(r.disputed_amount) for r in doc.lines), 2)
	doc.total_accepted = flt(sum(flt(r.accepted_amount) for r in doc.lines), 2)


def sync_cost_entries(doc):
	"""Each charge shows the dispute it is in; charges taken out are released."""
	names = {r.cost_entry for r in doc.lines if r.cost_entry}
	for name in names:
		frappe.db.set_value(
			ENTRY, name, {"dispute": doc.name, "dispute_status": doc.status}, update_modified=False
		)
	for name in frappe.get_all(ENTRY, filters={"dispute": doc.name}, pluck="name"):
		if name not in names:
			frappe.db.set_value(ENTRY, name, {"dispute": None, "dispute_status": None}, update_modified=False)


def release_cost_entries(doc):
	if doc.status != "Draft":
		frappe.throw(_("Only a draft dispute can be deleted. Close this one instead."))
	for name in frappe.get_all(ENTRY, filters={"dispute": doc.name}, pluck="name"):
		frappe.db.set_value(ENTRY, name, {"dispute": None, "dispute_status": None}, update_modified=False)


def document_states(doc):
	"""What the form needs to say the next step: the invoices' states and the party's settings."""
	states = {"purchase_invoice": None, "debit_note": None, "contact": False}
	if doc.purchase_invoice:
		states["purchase_invoice"] = frappe.db.get_value(
			"Purchase Invoice", doc.purchase_invoice, "docstatus"
		)
	if doc.debit_note:
		states["debit_note"] = frappe.db.get_value("Purchase Invoice", doc.debit_note, "docstatus")
	states["contact"] = bool(contact_for(doc.billed_by))
	return states


def _set_status(name, status, **values):
	values["status"] = status
	frappe.db.set_value(DOCTYPE, name, values)
	frappe.db.sql(
		"UPDATE `tabShipping Cost Entry` SET dispute_status = %s WHERE dispute = %s",
		(status, name),
	)


# ---------------------------------------------------------------------------
# Sending
# ---------------------------------------------------------------------------


def _load(name, perm="write"):
	doc = frappe.get_doc(DOCTYPE, name)
	doc.check_permission(perm)
	return doc


def _check_ready(doc):
	if not doc.lines:
		frappe.throw(_("Add the charges you dispute first."))
	for row in doc.lines:
		if flt(row.disputed_amount) <= 0:
			frappe.throw(
				_("Row {0}: enter the amount disputed for parcel {1}.").format(row.idx, row.parcel_number)
			)


def _company_name():
	company = frappe.db.get_single_value(SETTINGS, "company") or frappe.defaults.get_global_default("company")
	return company or ""


def _money(doc, value):
	return fmt_money(flt(value), currency=doc.currency or "EUR")


@frappe.whitelist()
def email_draft(dispute):
	"""Recipient, subject and body for the e-mail, in the party's language."""
	doc = _load(dispute)
	_check_ready(doc)
	with _language(doc.language):
		subject = _("Dispute of invoice {0}: {1} parcels, {2}").format(
			doc.invoice_number, len(doc.lines), _money(doc, doc.total_disputed)
		)
		head = "".join(
			f"<th style='text-align:left;padding:4px 8px;border-bottom:1px solid #999'>{frappe.utils.escape_html(t)}</th>"
			for t in (_("Parcel"), _("Reason"), _("Billed"), _("Disputed"), _("Explanation"))
		)
		cell = "<td style='padding:4px 8px;border-bottom:1px solid #ddd;vertical-align:top'>{0}</td>"
		body = ""
		for row in doc.lines:
			body += (
				"<tr>"
				+ "".join(
					cell.format(frappe.utils.escape_html(v))
					for v in (
						row.parcel_number,
						_(row.reason),
						_money(doc, row.billed_amount),
						_money(doc, row.disputed_amount),
						row.evidence or "",
					)
				)
				+ "</tr>"
			)
		date = formatdate(doc.invoice_date) if doc.invoice_date else ""
		intro = (
			_("We dispute the charges below on your invoice {0} of {1}.").format(doc.invoice_number, date)
			if date
			else _("We dispute the charges below on your invoice {0}.").format(doc.invoice_number)
		)
		message = (
			f"<p>{_('Hello,')}</p>"
			f"<p>{frappe.utils.escape_html(intro)} "
			f"{_('Please credit the disputed amount, or explain why the charge applies.')}</p>"
			f"<table style='border-collapse:collapse'><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table>"
			f"<p><b>{_('Total disputed: {0}').format(_money(doc, doc.total_disputed))}</b></p>"
			f"<p>{_('Please quote our reference {0} in your reply.').format(doc.name)}</p>"
			f"<p>{_('Kind regards,')}<br>{frappe.utils.escape_html(_company_name())}</p>"
		)
	return {"recipients": doc.dispute_email or "", "subject": subject, "message": message}


@frappe.whitelist()
def portal_text(dispute):
	"""Plain text to paste into the party's portal, one paragraph per parcel."""
	doc = _load(dispute)
	_check_ready(doc)
	with _language(doc.language):
		date = formatdate(doc.invoice_date) if doc.invoice_date else ""
		lines = [
			_("Invoice {0} ({1})").format(doc.invoice_number, date)
			if date
			else _("Invoice {0}").format(doc.invoice_number),
			_("Our reference: {0}").format(doc.name),
			"",
		]
		for i, row in enumerate(doc.lines, 1):
			text = _("{0}. Parcel {1}: {2}. Billed {3}, disputed {4}.").format(
				i,
				row.parcel_number,
				_(row.reason),
				_money(doc, row.billed_amount),
				_money(doc, row.disputed_amount),
			)
			if row.evidence:
				text += " " + row.evidence
			lines.append(text)
		lines += [
			"",
			_("Total disputed: {0}").format(_money(doc, doc.total_disputed)),
			_("Please credit the disputed amount, or explain why the charge applies."),
		]
	return {"text": "\n".join(lines), "portal_url": doc.portal_url or ""}


def _answer_days(doc):
	contact = contact_for(doc.billed_by)
	days = cint(contact.answer_days) if contact else 0
	return days if days > 0 else DEFAULT_ANSWER_DAYS


@frappe.whitelist()
def mark_sent(dispute, method=None, portal_reference=None, sent_on=None):
	"""Record that the dispute reached the party, by e-mail or on their portal."""
	doc = _load(dispute)
	if doc.status not in ("Draft", "Sent"):
		frappe.throw(_("This dispute was already answered."))
	_check_ready(doc)
	sent = getdate(sent_on or today())
	values = {"sent_on": sent}
	if method in ("Email", "Portal"):
		values["method"] = method
	if portal_reference:
		values["portal_reference"] = portal_reference.strip()
	if doc.status == "Draft" or not doc.answer_due:
		values["answer_due"] = add_days(sent, _answer_days(doc))
	_set_status(doc.name, "Sent", **values)
	line = _("Sent to {0}").format(doc.billed_by)
	if portal_reference:
		line += ", " + _("reference {0}").format(portal_reference.strip())
	frappe.get_doc(DOCTYPE, doc.name).add_comment("Info", line)
	return True


def on_communication(doc, method=None):
	"""An e-mail sent from the dispute marks it sent; replies just land on its timeline."""
	if doc.get("reference_doctype") != DOCTYPE or not doc.get("reference_name"):
		return
	if doc.get("sent_or_received") != "Sent" or doc.get("communication_medium") != "Email":
		return
	try:
		row = frappe.db.get_value(
			DOCTYPE, doc.reference_name, ["status", "billed_by", "answer_due"], as_dict=True
		)
		if not row or row.status != "Draft":
			return
		sent = getdate(doc.get("communication_date") or today())
		contact = contact_for(row.billed_by)
		days = cint(contact.answer_days) if contact and cint(contact.answer_days) > 0 else DEFAULT_ANSWER_DAYS
		_set_status(doc.reference_name, "Sent", sent_on=sent, method="Email", answer_due=add_days(sent, days))
	except Exception:  # noqa: BLE001 - e-posta itiraz yüzünden düşmez
		# Never stand between the user and their e-mail.
		frappe.log_error(title="Carrier dispute: mark sent", message=frappe.get_traceback())


# ---------------------------------------------------------------------------
# Answer and credit
# ---------------------------------------------------------------------------


@frappe.whitelist()
def record_answer(
	dispute, decision, accepted_amount=None, carrier_credit_note=None, decided_on=None, notes=None
):
	"""
	Record the party's answer.

	Accepted credits every line in full, Rejected none. Partly Accepted takes
	the total and splits it over the lines in proportion; amounts already typed
	on the lines are kept when no total is given.
	"""
	doc = _load(dispute)
	if decision not in DECISIONS:
		frappe.throw(_("Unknown decision: {0}").format(decision))
	if doc.status not in ("Sent", "Accepted", "Partly Accepted", "Rejected"):
		frappe.throw(_("Mark the dispute as sent before recording an answer."))
	if doc.debit_note and frappe.db.get_value("Purchase Invoice", doc.debit_note, "docstatus") in (0, 1):
		frappe.throw(
			_("Debit note {0} already books this answer. Cancel or delete it first.").format(doc.debit_note)
		)

	disputed = [flt(r.disputed_amount) for r in doc.lines]
	if decision == "Accepted":
		accepted = disputed
	elif decision == "Rejected":
		accepted = [0.0 for _d in disputed]
	else:
		total = flt(accepted_amount, 2)
		if not total:
			accepted = [flt(r.accepted_amount) for r in doc.lines]
			total = flt(sum(accepted), 2)
		else:
			accepted = distribute(total, disputed)
		if total <= 0 or total >= flt(doc.total_disputed, 2):
			frappe.throw(
				_("For a partial acceptance the amount must be more than 0 and less than {0}.").format(
					_money(doc, doc.total_disputed)
				)
			)

	for row, value in zip(doc.lines, accepted):
		row.accepted_amount = flt(value, 2)
	doc.decision = decision
	doc.status = decision
	doc.decided_on = getdate(decided_on or today())
	if carrier_credit_note:
		doc.carrier_credit_note = carrier_credit_note.strip()
	doc.save()
	line = _("{0} answered: {1}, {2} accepted").format(
		doc.billed_by, _(decision), _money(doc, doc.total_accepted)
	)
	if notes:
		line += f". {notes}"
	doc.add_comment("Info", line)
	return doc.status


@frappe.whitelist()
def make_debit_note(dispute):
	"""
	Book the accepted amount as a draft return Purchase Invoice against the original.

	The original invoice has one freight line per cost center. Each accepted
	charge goes back to the line of its own cost center, so the credit lands
	where the freight was booked. Left as a draft for accounting to submit.
	"""
	doc = _load(dispute)
	if doc.status not in ("Accepted", "Partly Accepted"):
		frappe.throw(_("Only an accepted dispute is credited."))
	if flt(doc.total_accepted) <= 0:
		frappe.throw(_("Nothing was accepted."))
	if doc.debit_note and frappe.db.get_value("Purchase Invoice", doc.debit_note, "docstatus") in (0, 1):
		return doc.debit_note
	if not doc.purchase_invoice:
		frappe.throw(
			_("No Purchase Invoice is linked to this carrier invoice, so there is nothing to credit against.")
		)
	original = frappe.db.get_value(
		"Purchase Invoice", doc.purchase_invoice, ["docstatus", "company"], as_dict=True
	)
	if not original or original.docstatus != 1:
		frappe.throw(_("Submit Purchase Invoice {0} first.").format(doc.purchase_invoice))

	from erpnext.accounts.doctype.purchase_invoice.purchase_invoice import make_debit_note as return_of

	from erpnext_shipping.erpnext_shipping.shipping_cost_accounting import _cost_center_for

	fallback = frappe.db.get_single_value(SETTINGS, "default_cost_center") or frappe.db.get_value(
		"Company", original.company, "cost_center"
	)
	per_cost_center = {}
	for row in doc.lines:
		if flt(row.accepted_amount) <= 0:
			continue
		entry = frappe.db.get_value(ENTRY, row.cost_entry, ["delivery_note"], as_dict=True) or frappe._dict()
		cost_center = _cost_center_for(entry, fallback)
		per_cost_center[cost_center] = flt(per_cost_center.get(cost_center, 0) + flt(row.accepted_amount), 2)

	note = return_of(doc.purchase_invoice)
	rows = [
		{"idx": i, "cost_center": item.cost_center, "room": abs(flt(item.rate) * flt(item.qty))}
		for i, item in enumerate(note.items)
	]
	try:
		plan = allocate(per_cost_center, rows)
	except ValueError as exc:
		frappe.throw(
			_("The accepted amount is {0} more than Purchase Invoice {1} charged.").format(
				_money(doc, flt(exc.args[0])), doc.purchase_invoice
			)
		)

	keep = []
	for i, item in enumerate(note.items):
		amount = plan.get(i)
		if not amount:
			continue
		item.qty = -1
		if item.meta.has_field("received_qty"):
			item.received_qty = -1
		item.stock_qty = -1 * flt(item.conversion_factor or 1)
		item.price_list_rate = 0
		item.discount_percentage = 0
		item.rate = amount
		item.description = _("Credit for carrier invoice dispute {0}").format(doc.name)
		keep.append(item)
	note.set("items", keep)
	# Dated today, not on the original's date that the mapping copies.
	note.set_posting_time = 1
	note.posting_date = today()
	note.due_date = today()
	note.bill_no = doc.carrier_credit_note or doc.name
	note.bill_date = doc.decided_on or today()
	note.remarks = _("Credit for carrier invoice dispute {0} on invoice {1}").format(
		doc.name, doc.invoice_number
	)
	note.insert(ignore_permissions=True)

	frappe.db.set_value(DOCTYPE, doc.name, "debit_note", note.name)
	doc.add_comment("Info", _("Debit note {0} created as a draft").format(note.name))
	return note.name


@frappe.whitelist()
def close(dispute, note=None):
	"""Close without a debit note: withdrawn, or credited outside the system."""
	doc = _load(dispute)
	if doc.status in ("Credited", "Closed"):
		return doc.status
	_set_status(doc.name, "Closed")
	doc.add_comment("Info", _("Closed") + (f": {note}" if note else ""))
	return "Closed"


def on_debit_note_submit(doc, method=None):
	if not doc.get("is_return"):
		return
	name = frappe.db.get_value(DOCTYPE, {"debit_note": doc.name}, "name")
	if name:
		_set_status(name, "Credited", credited_on=getdate(doc.get("posting_date") or today()))


def on_debit_note_cancel(doc, method=None):
	if not doc.get("is_return"):
		return
	row = frappe.db.get_value(DOCTYPE, {"debit_note": doc.name}, ["name", "decision"], as_dict=True)
	if row:
		_set_status(row.name, row.decision or "Accepted", credited_on=None)


def on_debit_note_trash(doc, method=None):
	# Runs before the link check: a deleted draft debit note frees the dispute
	# for a new one instead of being blocked by it.
	if not doc.get("is_return"):
		return
	for name in frappe.get_all(DOCTYPE, filters={"debit_note": doc.name}, pluck="name"):
		frappe.db.set_value(DOCTYPE, name, "debit_note", None)


# ---------------------------------------------------------------------------
# Reminders
# ---------------------------------------------------------------------------


def _assign(name, user, description, date=None):
	if not user or user == "Guest" or not frappe.db.get_value("User", user, "enabled"):
		return
	from frappe.desk.form.assign_to import add

	args = {"assign_to": [user], "doctype": DOCTYPE, "name": name, "description": description, "date": date}
	try:
		add(args, ignore_permissions=True)
	except TypeError:
		add(args)


def remind_disputes():
	"""
	Daily, 06:47: a draft close to its filing deadline, and a sent dispute
	whose answer is late, each become a ToDo for whoever opened the dispute.
	Once per dispute and kind.
	"""
	now = getdate(today())
	soon = add_days(now, REMIND_BEFORE_DAYS)
	jobs = [
		(
			{"status": "Draft", "deadline": ["<=", soon], "deadline_reminded": ["is", "not set"]},
			"deadline_reminded",
			"deadline",
			lambda d: _("File dispute {0} with {1} by {2}.").format(
				d.name, d.billed_by, formatdate(d.deadline)
			),
		),
		(
			{"status": "Sent", "answer_due": ["<", now], "answer_reminded": ["is", "not set"]},
			"answer_reminded",
			"answer_due",
			lambda d: _("{0} has not answered dispute {1}, due {2}. Chase them or record the answer.").format(
				d.billed_by, d.name, formatdate(d.answer_due)
			),
		),
	]
	for filters, flag, date_field, text in jobs:
		rows = frappe.get_all(
			DOCTYPE, filters=filters, fields=["name", "owner", "billed_by", "deadline", "answer_due"]
		)
		for row in rows:
			try:
				_assign(row.name, row.owner, text(row), row.get(date_field))
				frappe.db.set_value(DOCTYPE, row.name, flag, now, update_modified=False)
				frappe.db.commit()
			except Exception:  # noqa: BLE001 - bir itiraz ötekileri durdurmaz
				frappe.db.rollback()
				frappe.log_error(
					title="Carrier dispute reminder", message=f"{row.name}: {frappe.get_traceback()}"
				)
