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
import re
import xml.etree.ElementTree as ET

import frappe
from frappe import _
from frappe.utils import flt, getdate, now_datetime

# match_entry status -> Shipping Cost Entry.match_method label
MATCH_METHOD_LABELS = {
	"tracking": "Tracking",
	"dntrack": "DN Tracking",
	"order": "Order Number",
}

# FedEx invoices arrive as UBL/Peppol XML (one file per invoice).
FEDEX_NS = {
	"i": "urn:oasis:names:specification:ubl:schema:xsd:Invoice-2",
	"cac": "urn:oasis:names:specification:ubl:schema:xsd:CommonAggregateComponents-2",
	"cbc": "urn:oasis:names:specification:ubl:schema:xsd:CommonBasicComponents-2",
}

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

# Base (non-surcharge) component; the rest of the known DPD columns are surcharges.
BASE_COMPONENT = "Product Net Amount"
SURCHARGE_COMPONENTS = [c for c in COMPONENT_COLUMNS if c != BASE_COMPONENT]
# Surcharges that point at a wrong declared weight/size (actionable).
WEIGHT_SURCHARGE_COMPONENTS = ("Oversized/Overweight", "Heavy Weight parcels 20-31,5kg")


def _surcharge_fields(breakdown, inv_weight, corr_weight):
	"""Derived surcharge flags/amount from a parsed charge breakdown + weights. Only
	the known DPD surcharge components are summed, so a FedEx breakdown (different
	keys) safely yields 0 rather than counting its base freight as a surcharge."""
	b = breakdown or {}
	surcharge = sum(flt(b.get(c)) for c in SURCHARGE_COMPONENTS)
	weight_sc = 1 if any(flt(b.get(c)) for c in WEIGHT_SURCHARGE_COMPONENTS) else 0
	reweigh = 1 if (corr_weight and abs(flt(corr_weight) - flt(inv_weight)) > 0.01) else 0
	return {
		"surcharge_amount": surcharge,
		"weight_surcharge": weight_sc,
		"reweigh": reweigh,
	}


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
		where ifnull(so.po_no, '') != '' and dni.docstatus < 2
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


def _dn_tracking_fields():
	"""Configured Delivery Note fieldname(s) holding a tracking number — both the
	normal outbound field and the return field. Names differ between systems, so
	they come from Shipping Cost Settings."""
	defaults = {
		"dn_tracking_number_field": "custom_tracking_number",
		"dn_return_tracking_number_field": "custom_return_tracking_number",
	}
	try:
		s = frappe.get_cached_doc("Shipping Cost Settings")
	except Exception:
		return list(defaults.values())
	if not s.get("enable_dn_tracking_match"):
		return []
	fields = []
	for key, default in defaults.items():
		raw = s.get(key) or default
		fields += [f.strip() for f in raw.replace("\n", ",").split(",") if f.strip()]
	return list(dict.fromkeys(fields))  # de-dupe, keep order


def _tracking_keys(value):
	"""Normalised comparison keys for a tracking number (case- and leading-zero
	tolerant), so DPD/FedEx numbers match values typed manually on a Delivery Note."""
	v = (str(value) or "").strip().lower()
	if not v:
		return set()
	keys = set()
	# Ham + boşluk/tire temizlenmiş varyant (manuel girişte "0544 8801" gibi olabilir).
	for variant in (v, re.sub(r"[\s\-]", "", v)):
		if not variant:
			continue
		keys.add(variant)
		stripped = variant.lstrip("0")
		if stripped:
			keys.add(stripped)
	return keys


def build_dn_tracking_index():
	"""Return {normalised tracking -> set(delivery_note)} built from the configured
	Delivery Note tracking field(s). Lets us match invoice lines to deliveries whose
	tracking number was entered manually on the DN (no Shipment document)."""
	index = {}
	for field in _dn_tracking_fields():
		if not frappe.db.has_column("Delivery Note", field):
			continue
		for r in frappe.get_all(
			"Delivery Note",
			filters={field: ["is", "set"], "docstatus": ["<", 2]},
			fields=["name", field],
		):
			for key in _tracking_keys(r.get(field)):
				index.setdefault(key, set()).add(r.name)
	return index


def build_match_context():
	"""Build the lookup indexes used by match_entry once per import run."""
	return {"po": build_po_no_dn_index(), "track": build_dn_tracking_index()}


def _disambiguate_dns(dns):
	"""Pick a single Delivery Note from candidates that share a tracking/order key.
	Shipping cost belongs to the outbound delivery, so prefer the one that is NOT a
	Sales Return (is_return=0) — e.g. the "Return Issued" send-out rather than the
	stock-return receipt. Returns None if still ambiguous."""
	dns = list(dns)
	if len(dns) == 1:
		return dns[0]
	non_return = [d for d in dns if not frappe.db.get_value("Delivery Note", d, "is_return")]
	if len(non_return) == 1:
		return non_return[0]
	return None


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


def match_entry(parcel_number, reference_1, ctx=None):
	"""Match an invoice line to a Delivery Note (and Shipment if one exists).

	1) By tracking: parcel number in a Shipment's awb_number -> that Shipment
	   (and its Delivery Note). Status "tracking".
	2) By DN tracking field: parcel number == the configured Delivery Note tracking
	   field (manually entered, no Shipment needed). Status "dntrack".
	3) By order number: Reference 1 == Sales Order po_no, resolving to exactly one
	   Delivery Note (and its Shipment if any). Status "order".
	Multiple candidates in step 2/3 -> "ambiguous" (left unmatched).
	Returns (shipment_or_None, delivery_note_or_None, status).
	"""
	if ctx is None:
		ctx = build_match_context()

	# 1) Shipment awb_number
	shipment = find_shipment_by_parcel(parcel_number)
	if shipment:
		return shipment, _dn_for_shipment(shipment), "tracking"

	# 2) Delivery Note tracking field (configurable; normal + return)
	track_index = ctx.get("track") or {}
	for key in _tracking_keys(parcel_number):
		dns = track_index.get(key)
		if dns:
			dn = _disambiguate_dns(dns)
			if dn:
				return _shipment_for_dn(dn), dn, "dntrack"
			return None, None, "ambiguous"

	# 3) Order number -> Sales Order po_no
	ref = (reference_1 or "").strip()
	if ref:
		po_index = ctx.get("po") or {}
		# po_no bazen "#1240" bazen "1240" olarak saklanır (Shopify sipariş adı).
		candidates = [ref.lower()]
		stripped = ref.lstrip("#").strip().lower()
		if stripped and stripped != ref.lower():
			candidates.append(stripped)
		for key in candidates:
			dns = po_index.get(key)
			if dns:
				dn = _disambiguate_dns(dns)
				if dn:
					return _shipment_for_dn(dn), dn, "order"
				return None, None, "ambiguous"
	return None, None, "none"


def recompute_shipment_cost(shipment: str):
	"""Sum all Shipping Cost Entries for a Shipment and write the rollup onto the
	Shipment (Shipping Details). The Delivery Note total is handled separately by
	recompute_delivery_note_cost so orders without a Shipment still get their cost."""
	if not shipment:
		return
	agg = frappe.db.sql(
		"""select coalesce(sum(total_net_amount), 0), coalesce(sum(surcharge_amount), 0),
			max(weight_surcharge), max(reweigh)
		from `tabShipping Cost Entry` where shipment=%s""",
		shipment,
	)[0]
	total, surcharge = flt(agg[0]), flt(agg[1])
	currency = (
		frappe.db.get_value(
			"Shipping Cost Entry", {"shipment": shipment}, "currency"
		)
		or "EUR"
	)
	updates = {
		"custom_shipping_cost": total,
		"custom_surcharge_amount": surcharge,
		"custom_has_weight_surcharge": 1 if agg[2] else 0,
		"custom_shipping_cost_currency": currency,
		"custom_shipping_cost_updated": now_datetime(),
	}

	# Quote sapması + müşteri kargo bedeli + marj (alanlar migrate ile geldiyse).
	if frappe.db.has_column("Shipment", "custom_cost_variance"):
		quote = flt(frappe.db.get_value("Shipment", shipment, "shipment_amount"))
		variance = (total - quote) if quote > 0 else 0
		charge = 0
		if frappe.db.has_column("Delivery Note", "custom_customer_shipping_charge"):
			charge = flt(
				frappe.db.sql(
					"""select coalesce(sum(dn.custom_customer_shipping_charge), 0)
					from `tabShipment Delivery Note` sdn
					join `tabDelivery Note` dn on dn.name = sdn.delivery_note
					where sdn.parent = %s""",
					shipment,
				)[0][0]
			)
		updates.update(
			{
				"custom_cost_variance": variance,
				"custom_cost_variance_pct": (variance / quote * 100) if quote > 0 else 0,
				"custom_customer_shipping_charge": charge,
				"custom_shipping_margin": charge - total,
			}
		)

	frappe.db.set_value("Shipment", shipment, updates, update_modified=False)


def set_dn_customer_shipping_charge(doc, method=None):
	"""Delivery Note validate hook: when a shipping-charge account is configured in
	Shipment Settings, fill custom_customer_shipping_charge from the DN's Sales Taxes
	& Charges rows on that account. No-op (keeps the manual value) when unset — so this
	stays dormant until the user actually starts charging customers for shipping."""
	account = frappe.db.get_single_value("Shipment Settings", "customer_shipping_account_head")
	if not account:
		return
	doc.custom_customer_shipping_charge = sum(
		flt(t.tax_amount) for t in (doc.get("taxes") or []) if t.account_head == account
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


# ---------------------------------------------------------------------------
# Shared import core (carrier-agnostic): match -> upsert -> rollup
# ---------------------------------------------------------------------------
def _new_stats():
	return {
		"created": 0,
		"updated": 0,
		"matched_tracking": 0,
		"matched_order": 0,
		"ambiguous": 0,
		"files": 0,
		"errors": [],
		"affected_shipments": set(),
		"affected_dns": set(),
		"unmatched": set(),
	}


def _upsert_cost_entry(values, ctx, stats, pdf=None):
	"""Match one parsed invoice line to a Shipment/Delivery Note and upsert it as a
	Shipping Cost Entry (idempotent by parcel+invoice). Updates the stats accumulator
	and optionally attaches the source invoice PDF."""
	parcel = values["parcel_number"]
	invoice = values.get("invoice_number") or ""
	name = f"{parcel}-{invoice}" if invoice else parcel

	shipment, delivery_note, status = match_entry(parcel, values.get("reference_1"), ctx)
	values = dict(values)
	values["shipment"] = shipment
	values["delivery_note"] = delivery_note
	values["match_method"] = MATCH_METHOD_LABELS.get(status)

	if shipment:
		stats["affected_shipments"].add(shipment)
	if delivery_note:
		stats["affected_dns"].add(delivery_note)
	if shipment or delivery_note:
		if status in ("tracking", "dntrack"):
			stats["matched_tracking"] += 1
		elif status == "order":
			stats["matched_order"] += 1
	else:
		stats["unmatched"].add(parcel)
		if status == "ambiguous":
			stats["ambiguous"] += 1

	if frappe.db.exists("Shipping Cost Entry", name):
		doc = frappe.get_doc("Shipping Cost Entry", name)
		doc.update(values)
		doc.save(ignore_permissions=True)
		stats["updated"] += 1
	else:
		doc = frappe.new_doc("Shipping Cost Entry")
		doc.update(values)
		doc.name = name
		doc.flags.name_set = True
		doc.insert(ignore_permissions=True)
		stats["created"] += 1

	if pdf:
		_attach_pdf(doc, pdf)
	return doc


def _attach_pdf(doc, pdf):
	"""Attach (filename, bytes) as a private File to the entry, skipping duplicates."""
	filename, data = pdf
	if not data:
		return
	exists = frappe.db.exists(
		"File",
		{
			"attached_to_doctype": "Shipping Cost Entry",
			"attached_to_name": doc.name,
			"file_name": filename,
		},
	)
	if exists:
		return
	frappe.get_doc(
		{
			"doctype": "File",
			"file_name": filename,
			"attached_to_doctype": "Shipping Cost Entry",
			"attached_to_name": doc.name,
			"is_private": 1,
			"content": data,
		}
	).insert(ignore_permissions=True)


def _finalize_import(stats):
	"""Recompute rollups for every affected DN and Shipment, then commit."""
	for dn in stats["affected_dns"]:
		recompute_delivery_note_cost(dn)
	for shipment in stats["affected_shipments"]:
		recompute_shipment_cost(shipment)
	frappe.db.commit()


def _stats_summary(stats):
	return {
		"created": stats["created"],
		"updated": stats["updated"],
		"files": stats["files"],
		"errors": stats["errors"],
		"matched_shipments": len(stats["affected_shipments"]),
		"matched_by_tracking": stats["matched_tracking"],
		"matched_by_order": stats["matched_order"],
		"ambiguous": stats["ambiguous"],
		"unmatched_parcels": len(stats["unmatched"]),
	}


# ---------------------------------------------------------------------------
# Parsers
# ---------------------------------------------------------------------------
def _parse_dpd_into(content, source_file, carrier, ctx, stats):
	"""Parse a DPD invoice detail .xlsx and upsert each row."""
	import openpyxl

	wb = openpyxl.load_workbook(io.BytesIO(content), read_only=True, data_only=True)
	ws = wb.active
	rows = ws.iter_rows(values_only=True)
	header = next(rows, None)
	if not header:
		raise ValueError(_("The uploaded file is empty."))
	idx = {str(h).strip(): i for i, h in enumerate(header) if h is not None}
	if COL_PARCEL not in idx:
		raise ValueError(
			_("Column '{0}' not found. Is this a DPD invoice detail file?").format(COL_PARCEL)
		)

	def cell(row, colname):
		i = idx.get(colname)
		return row[i] if (i is not None and i < len(row)) else None

	for row in rows:
		parcel = _norm_parcel(cell(row, COL_PARCEL))
		if not parcel:
			continue
		breakdown = {}
		for comp in COMPONENT_COLUMNS:
			val = cell(row, comp)
			if val not in (None, "", 0, "0"):
				breakdown[comp] = flt(val)
		values = {
			"parcel_number": parcel,
			"invoice_number": _s(cell(row, COL_INVOICE)),
			"carrier": carrier,
			"scan_date": _parse_date(cell(row, COL_SCAN_DATE)),
			"product_name": _s(cell(row, COL_PRODUCT)),
			"country": _s(cell(row, COL_COUNTRY)),
			"currency": _s(cell(row, COL_CURRENCY)) or "EUR",
			"total_net_amount": flt(cell(row, COL_TOTAL)),
			"vat_rate": flt(cell(row, COL_VAT)),
			"reference_1": _s(cell(row, COL_REFERENCE)),
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
			**_surcharge_fields(
				breakdown, flt(cell(row, COL_INV_WEIGHT)), flt(cell(row, COL_CORR_WEIGHT))
			),
			"source_file": source_file,
		}
		_upsert_cost_entry(values, ctx, stats)


def _fedex_text(el, path):
	x = el.find(path, FEDEX_NS)
	return x.text.strip() if (x is not None and x.text) else None


def _extract_embedded_pdf(root):
	"""Return (filename, bytes) of the embedded human-readable invoice PDF, or None."""
	import base64

	el = root.find(".//cbc:EmbeddedDocumentBinaryObject", FEDEX_NS)
	if el is None or not el.text:
		return None
	filename = el.get("filename") or "invoice.pdf"
	try:
		return (filename, base64.b64decode(el.text))
	except Exception:
		return None


def _parse_fedex_into(content, source_file, ctx, stats):
	"""Parse a FedEx UBL/Peppol XML invoice; one Shipping Cost Entry per InvoiceLine.

	The tracking (AWB) number is DocumentReference/ID[@schemeID='AAM']; the Item
	Description carries Collection date, Payweight and the receiver.
	"""
	root = ET.fromstring(content)
	invoice = _fedex_text(root, "cbc:ID")
	currency = _fedex_text(root, "cbc:DocumentCurrencyCode") or "EUR"
	issue = _fedex_text(root, "cbc:IssueDate")
	supplier = _fedex_text(root, "cac:AccountingSupplierParty/cac:Party/cac:PartyName/cbc:Name") or ""
	carrier = "FedEx" if "fedex" in supplier.lower() else (supplier or "FedEx")
	order_ref = _fedex_text(root, "cac:OrderReference/cbc:ID")
	if order_ref and order_ref.strip().lower() in ("no reference given", "not applicable", ""):
		order_ref = None
	pdf = _extract_embedded_pdf(root)

	for line in root.findall("cac:InvoiceLine", FEDEX_NS):
		awb = None
		for dr in line.findall("cac:DocumentReference/cbc:ID", FEDEX_NS):
			if dr.get("schemeID") == "AAM" and dr.text:
				awb = dr.text.strip()
		if not awb:
			awb = _fedex_text(line, "cac:Item/cbc:Name")
		if not awb:
			continue

		# Order number: FedEx satır-bazlı AccountingCost'ta taşır (ör. "#1240" =
		# Shopify sipariş adı). Yoksa fatura seviyesindeki OrderReference'a düş.
		line_ref = _fedex_text(line, "cbc:AccountingCost") or order_ref

		desc = _fedex_text(line, "cac:Item/cbc:Description") or ""
		m_coll = re.search(r"Collection:([0-9-]+)", desc)
		m_pw = re.search(r"Payweight:([0-9.]+)", desc)
		m_rcv = re.search(r"Receiver:([^;]*);[^;]*;([^;]*);([^;]*);", desc)

		charges = {}
		for ac in line.findall("cac:AllowanceCharge", FEDEX_NS):
			ind = _fedex_text(ac, "cbc:ChargeIndicator")
			reason = _fedex_text(ac, "cbc:AllowanceChargeReason") or "Charge"
			amt = flt(_fedex_text(ac, "cbc:Amount"))
			charges[reason] = amt if ind == "true" else -amt

		values = {
			"parcel_number": awb,
			"invoice_number": invoice or "",
			"carrier": carrier,
			"scan_date": _parse_date(m_coll.group(1) if m_coll else issue),
			"product_name": _fedex_text(line, "cac:Item/cac:SellersItemIdentification/cbc:ID") or "",
			"country": (m_rcv.group(3).strip() if m_rcv else ""),
			"currency": currency,
			"total_net_amount": flt(_fedex_text(line, "cbc:LineExtensionAmount")),
			"vat_rate": flt(_fedex_text(line, "cac:Item/cac:ClassifiedTaxCategory/cbc:Percent")),
			"reference_1": line_ref or "",
			"receiver_name": (m_rcv.group(1).strip() if m_rcv else ""),
			"receiver_city": (m_rcv.group(2).strip() if m_rcv else ""),
			"invoicing_weight": flt(m_pw.group(1)) if m_pw else 0,
			"charge_breakdown": json.dumps(charges, ensure_ascii=False),
			"source_file": source_file,
		}
		_upsert_cost_entry(values, ctx, stats, pdf=pdf)


def _process_content(content, filename, ctx, stats):
	"""Dispatch one file's content to the right parser by extension."""
	low = (filename or "").lower()
	if low.endswith((".xlsx", ".xls")):
		_parse_dpd_into(content, filename, "DPD", ctx, stats)
	elif low.endswith(".xml"):
		_parse_fedex_into(content, filename, ctx, stats)
	else:
		# İçeriğe göre kaba tahmin: XML mi?
		head = content[:200].lstrip()
		if head.startswith(b"<?xml") or b"Invoice-2" in head:
			_parse_fedex_into(content, filename, ctx, stats)
		else:
			raise ValueError(_("Unsupported file type: {0}").format(filename))


# ---------------------------------------------------------------------------
# Public import entrypoints
# ---------------------------------------------------------------------------
@frappe.whitelist()
def import_invoice(file_url: str):
	"""Import a carrier invoice: DPD .xlsx, FedEx .xml, or a .zip of many such files.
	Auto-detects the format. Idempotent by parcel+invoice; rolls up costs onto the
	matched Shipment and Delivery Note."""
	content = _read_file_content(file_url)
	filename = file_url.rsplit("/", 1)[-1]
	stats = _new_stats()
	ctx = build_match_context()

	if filename.lower().endswith(".zip"):
		import zipfile

		with zipfile.ZipFile(io.BytesIO(content)) as zf:
			for member in zf.namelist():
				if member.endswith("/"):
					continue
				base = member.rsplit("/", 1)[-1]
				if base.startswith(".") or not base.lower().endswith((".xml", ".xlsx", ".xls")):
					continue
				try:
					_process_content(zf.read(member), base, ctx, stats)
					stats["files"] += 1
				except Exception as e:
					stats["errors"].append(f"{base}: {e}")
	else:
		_process_content(content, filename, ctx, stats)
		stats["files"] += 1

	_finalize_import(stats)
	return _stats_summary(stats)


@frappe.whitelist()
def import_dpd_invoice(file_url: str, carrier: str = "DPD"):
	"""Backward-compatible entrypoint for DPD .xlsx imports."""
	content = _read_file_content(file_url)
	stats = _new_stats()
	ctx = build_match_context()
	_parse_dpd_into(content, file_url.rsplit("/", 1)[-1], carrier, ctx, stats)
	stats["files"] = 1
	_finalize_import(stats)
	return _stats_summary(stats)


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
	ctx = build_match_context()
	for name in entries:
		doc = frappe.get_doc("Shipping Cost Entry", name)
		shipment, delivery_note, status = match_entry(doc.parcel_number, doc.reference_1, ctx)
		if shipment or delivery_note:
			doc.shipment = shipment
			doc.delivery_note = delivery_note
			doc.match_method = MATCH_METHOD_LABELS.get(status)
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
