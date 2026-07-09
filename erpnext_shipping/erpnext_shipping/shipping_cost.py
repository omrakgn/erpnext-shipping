# Copyright (c) 2026, Frappe and contributors
# For license information, please see license.txt
"""DPD (and other carrier) shipping-cost invoice import + rollup.

DPD sends an Excel invoice detail file where each row is one charge line for a
parcel (tracking) number. Corrections arrive later as extra rows (credit/reversal
invoices starting with "4", or negative amounts). The effective cost of a
tracking is therefore the SUM of every row for that parcel number.

Each row is stored as a Shipping Cost Entry (idempotent by parcel+invoice), matched
to a Shipment via its awb_number, and rolled up onto the Shipment and its linked
Delivery Notes (Shipping Details).
"""

import io
import json

import frappe
from frappe import _
from frappe.utils import flt, getdate, now_datetime

# --- DPD Excel column headers (exact strings in the file) -------------------
COL_PARCEL = "Parcel Number"
COL_INVOICE = "Invoice Number"
COL_SCAN_DATE = "Scan Date"
COL_PRODUCT = "Product name"
COL_COUNTRY = "Country"
COL_CURRENCY = "Currency"
COL_TOTAL = "Total Net Amount"
COL_VAT = "VAT Rate"
COL_REFERENCE = "Reference 1"
COL_NAME = "Name"
COL_STREET = "Street + Home number"
COL_ZIP = "Receiver Zip code"
COL_CITY = "City"
COL_INV_WEIGHT = "Invoicing Weight"
COL_CORR_WEIGHT = "Corrected Weight"
COL_LENGTH = "Length"
COL_WIDTH = "Width"
COL_HEIGHT = "Height"
COL_GIRTH = "Girth"

# Surcharge / component columns kept in charge_breakdown JSON (for reference).
COMPONENT_COLUMNS = [
	"Product Net Amount",
	"Fuel Surcharge",
	"Security Surcharge",
	"Belgian Road Tax",
	"DPD 10 Surcharge",
	"DPD 12 Surcharge",
	"DPD 18 Surcharge",
	"Oversized/Overweight",
	"Heavy Weight parcels 20-31,5kg",
	"Island Surcharge",
	"Undeliverables",
	"Peak Surcharge",
	"Custom Clearance",
	"Missing docs",
	"LQ Charge",
	"Non EU surcharge for UK",
	"EDI Surcharge",
	"Relabeling Surcharge",
	"Saturday delivery",
	"Other surcharges 4",
	"Other surcharges 5",
]


def _s(v):
	"""Stringify a cell value without a trailing '.0' for whole numbers."""
	if v is None:
		return ""
	if isinstance(v, float) and v.is_integer():
		return str(int(v))
	return str(v).strip()


def _parse_date(v):
	if v is None or v == "":
		return None
	try:
		if hasattr(v, "date"):  # datetime/date from openpyxl
			return v.date() if hasattr(v, "hour") else v
		s = str(v).strip()
		# DPD format: dd.mm.yyyy
		if "." in s and len(s.split(".")) == 3:
			d, m, y = s.split(".")
			return getdate(f"{y}-{m}-{d}")
		return getdate(s)
	except Exception:
		return None


def _norm_parcel(v):
	"""Normalise a parcel number to a 14-digit, leading-zero string when numeric."""
	s = _s(v)
	if s.isdigit() and len(s) < 14:
		s = s.zfill(14)
	return s


def _read_file_content(file_url: str) -> bytes:
	"""Return the binary content of an uploaded File by url or File name."""
	file_doc = None
	if frappe.db.exists("File", {"file_url": file_url}):
		file_doc = frappe.get_doc("File", {"file_url": file_url})
	elif frappe.db.exists("File", file_url):
		file_doc = frappe.get_doc("File", file_url)
	if not file_doc:
		frappe.throw(_("Uploaded file not found: {0}").format(file_url))
	return file_doc.get_content()


def find_shipment_by_parcel(parcel_number: str):
	"""Find the Shipment whose awb_number contains this parcel/tracking number.

	awb_number may be comma-joined (split shipments) and may or may not carry the
	DPD leading zero, so we compare on the leading-zero-stripped value too.
	"""
	pn = _norm_parcel(parcel_number)
	if not pn:
		return None
	stripped = pn.lstrip("0") or pn
	for needle in {pn, stripped}:
		rows = frappe.get_all(
			"Shipment",
			filters={"awb_number": ["like", f"%{needle}%"]},
			fields=["name", "awb_number"],
			limit=10,
		)
		for r in rows:
			awbs = [a.strip() for a in (r.awb_number or "").replace(";", ",").split(",")]
			for a in awbs:
				if a and (a == pn or (a.lstrip("0") or a) == stripped):
					return r.name
	return None


def build_po_no_dn_index() -> dict:
	"""Return {normalised po_no -> set(delivery_note names)} for the order-number
	fallback. po_no lives on the Sales Order; we reach the Delivery Note via
	Delivery Note Item.against_sales_order. We anchor on the Delivery Note (not the
	Shipment) because many marketplace orders ship without a Shipment document."""
	rows = frappe.db.sql(
		"""
		select so.po_no as po_no, dni.parent as dn
		from `tabSales Order` so
		join `tabDelivery Note Item` dni on dni.against_sales_order = so.name
		where ifnull(so.po_no, '') != ''
		""",
		as_dict=True,
	)
	index = {}
	for r in rows:
		key = (r.po_no or "").strip().lower()
		if not key:
			continue
		index.setdefault(key, set()).add(r.dn)
	return index


def _shipment_for_dn(dn):
	"""Return the single Shipment a Delivery Note is attached to, or None."""
	shs = frappe.get_all(
		"Shipment Delivery Note",
		filters={"delivery_note": dn, "parenttype": "Shipment"},
		pluck="parent",
	)
	shs = list(set(shs))
	return shs[0] if len(shs) == 1 else None


def _dn_for_shipment(shipment):
	return frappe.db.get_value(
		"Shipment Delivery Note",
		{"parent": shipment, "parenttype": "Shipment"},
		"delivery_note",
	)


def match_entry(parcel_number, reference_1, dn_index=None):
	"""Match an invoice line to a Delivery Note (and Shipment if one exists).

	1) By tracking: parcel number in a Shipment's awb_number -> that Shipment
	   (and its Delivery Note). Status "tracking".
	2) Fallback by order number: Reference 1 == Sales Order po_no, resolving to
	   exactly one Delivery Note. If that DN is on exactly one Shipment, link it
	   too. Status "order". Multiple DNs -> "ambiguous" (left unmatched).
	Returns (shipment_or_None, delivery_note_or_None, status).
	"""
	shipment = find_shipment_by_parcel(parcel_number)
	if shipment:
		return shipment, _dn_for_shipment(shipment), "tracking"

	ref = (reference_1 or "").strip()
	if ref:
		if dn_index is None:
			dn_index = build_po_no_dn_index()
		dns = dn_index.get(ref.lower())
		if dns:
			if len(dns) == 1:
				dn = next(iter(dns))
				return _shipment_for_dn(dn), dn, "order"
			return None, None, "ambiguous"
	return None, None, "none"


def recompute_shipment_cost(shipment: str):
	"""Sum all Shipping Cost Entries for a Shipment and write the rollup onto the
	Shipment (Shipping Details). The Delivery Note total is handled separately by
	recompute_delivery_note_cost so orders without a Shipment still get their cost."""
	if not shipment:
		return
	total = flt(
		frappe.db.sql(
			"""select coalesce(sum(total_net_amount), 0)
			from `tabShipping Cost Entry` where shipment=%s""",
			shipment,
		)[0][0]
	)
	currency = (
		frappe.db.get_value(
			"Shipping Cost Entry", {"shipment": shipment}, "currency"
		)
		or "EUR"
	)
	frappe.db.set_value(
		"Shipment",
		shipment,
		{
			"custom_shipping_cost": total,
			"custom_shipping_cost_currency": currency,
			"custom_shipping_cost_updated": now_datetime(),
		},
		update_modified=False,
	)


def recompute_delivery_note_cost(delivery_note: str):
	"""Sum all Shipping Cost Entries linked to a Delivery Note and write the net
	total onto the DN's Shipping Details. Works whether or not a Shipment exists."""
	if not delivery_note:
		return
	if not frappe.db.has_column("Delivery Note", "custom_shipping_cost"):
		return
	total = flt(
		frappe.db.sql(
			"""select coalesce(sum(total_net_amount), 0)
			from `tabShipping Cost Entry` where delivery_note=%s""",
			delivery_note,
		)[0][0]
	)
	frappe.db.set_value(
		"Delivery Note", delivery_note, "custom_shipping_cost", total, update_modified=False
	)


@frappe.whitelist()
def import_dpd_invoice(file_url: str, carrier: str = "DPD"):
	"""Import a DPD invoice detail .xlsx: upsert each row as a Shipping Cost Entry,
	match to a Shipment and roll up costs. Idempotent by parcel+invoice."""
	import openpyxl

	content = _read_file_content(file_url)
	wb = openpyxl.load_workbook(io.BytesIO(content), read_only=True, data_only=True)
	ws = wb.active

	rows = ws.iter_rows(values_only=True)
	header = next(rows, None)
	if not header:
		frappe.throw(_("The uploaded file is empty."))
	idx = {str(h).strip(): i for i, h in enumerate(header) if h is not None}

	if COL_PARCEL not in idx:
		frappe.throw(
			_("Column '{0}' not found. Is this a DPD invoice detail file?").format(COL_PARCEL)
		)

	def cell(row, colname):
		i = idx.get(colname)
		return row[i] if (i is not None and i < len(row)) else None

	source_file = file_url.rsplit("/", 1)[-1]
	created = updated = skipped = 0
	matched_tracking = matched_order = ambiguous = 0
	affected_shipments = set()
	affected_dns = set()
	unmatched_parcels = set()
	dn_index = build_po_no_dn_index()

	for row in rows:
		parcel = _norm_parcel(cell(row, COL_PARCEL))
		if not parcel:
			continue
		invoice = _s(cell(row, COL_INVOICE))
		name = f"{parcel}-{invoice}" if invoice else parcel

		breakdown = {}
		for comp in COMPONENT_COLUMNS:
			val = cell(row, comp)
			if val not in (None, "", 0, "0"):
				breakdown[comp] = flt(val)

		reference_1 = _s(cell(row, COL_REFERENCE))
		shipment, delivery_note, status = match_entry(parcel, reference_1, dn_index)
		match_method = {"tracking": "Tracking", "order": "Order Number"}.get(status)
		if shipment:
			affected_shipments.add(shipment)
		if delivery_note:
			affected_dns.add(delivery_note)
		if shipment or delivery_note:
			if status == "tracking":
				matched_tracking += 1
			elif status == "order":
				matched_order += 1
		else:
			unmatched_parcels.add(parcel)
			if status == "ambiguous":
				ambiguous += 1

		values = {
			"parcel_number": parcel,
			"invoice_number": invoice,
			"carrier": carrier,
			"scan_date": _parse_date(cell(row, COL_SCAN_DATE)),
			"product_name": _s(cell(row, COL_PRODUCT)),
			"country": _s(cell(row, COL_COUNTRY)),
			"currency": _s(cell(row, COL_CURRENCY)) or "EUR",
			"total_net_amount": flt(cell(row, COL_TOTAL)),
			"vat_rate": flt(cell(row, COL_VAT)),
			"reference_1": reference_1,
			"receiver_name": _s(cell(row, COL_NAME)),
			"receiver_street": _s(cell(row, COL_STREET)),
			"receiver_zip": _s(cell(row, COL_ZIP)),
			"receiver_city": _s(cell(row, COL_CITY)),
			"invoicing_weight": flt(cell(row, COL_INV_WEIGHT)),
			"corrected_weight": flt(cell(row, COL_CORR_WEIGHT)),
			"length": flt(cell(row, COL_LENGTH)),
			"width": flt(cell(row, COL_WIDTH)),
			"height": flt(cell(row, COL_HEIGHT)),
			"girth": flt(cell(row, COL_GIRTH)),
			"charge_breakdown": json.dumps(breakdown, ensure_ascii=False),
			"source_file": source_file,
			"shipment": shipment,
			"delivery_note": delivery_note,
			"match_method": match_method,
		}

		if frappe.db.exists("Shipping Cost Entry", name):
			doc = frappe.get_doc("Shipping Cost Entry", name)
			doc.update(values)
			doc.save(ignore_permissions=True)
			updated += 1
		else:
			doc = frappe.new_doc("Shipping Cost Entry")
			doc.update(values)
			doc.name = name
			doc.flags.name_set = True
			doc.insert(ignore_permissions=True)
			created += 1

	# Rollup: her etkilenen Delivery Note ve Shipment için toplamı yeniden hesapla.
	for dn in affected_dns:
		recompute_delivery_note_cost(dn)
	for shipment in affected_shipments:
		recompute_shipment_cost(shipment)

	frappe.db.commit()

	return {
		"created": created,
		"updated": updated,
		"skipped": skipped,
		"matched_shipments": len(affected_shipments),
		"matched_by_tracking": matched_tracking,
		"matched_by_order": matched_order,
		"ambiguous": ambiguous,
		"unmatched_parcels": len(unmatched_parcels),
	}


@frappe.whitelist()
def rematch_unmatched():
	"""Re-run Shipment matching for all currently unmatched entries (e.g. after
	the Shipments/labels are synced). Returns how many got matched."""
	entries = frappe.get_all(
		"Shipping Cost Entry", filters={"matched": 0}, pluck="name"
	)
	matched = 0
	affected_shipments = set()
	affected_dns = set()
	dn_index = build_po_no_dn_index()
	for name in entries:
		doc = frappe.get_doc("Shipping Cost Entry", name)
		shipment, delivery_note, status = match_entry(doc.parcel_number, doc.reference_1, dn_index)
		if shipment or delivery_note:
			doc.shipment = shipment
			doc.delivery_note = delivery_note
			doc.match_method = {"tracking": "Tracking", "order": "Order Number"}.get(status)
			doc.save(ignore_permissions=True)
			if shipment:
				affected_shipments.add(shipment)
			if delivery_note:
				affected_dns.add(delivery_note)
			matched += 1
	for dn in affected_dns:
		recompute_delivery_note_cost(dn)
	for shipment in affected_shipments:
		recompute_shipment_cost(shipment)
	frappe.db.commit()
	return {"matched": matched, "remaining": len(entries) - matched}
