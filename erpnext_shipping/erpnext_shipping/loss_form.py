# Copyright (c) 2026, Frappe Technologies and contributors
# For license information, please see license.txt
"""Render the DPD 'Declaration of Non-Receipt' by stamping the claim values onto
the ORIGINAL blank DPD PDF, so the layout stays pixel-identical to the carrier's
form (no HTML re-drawing).

Two strategies, chosen automatically per base PDF:
  * AcroForm fill  — if the base PDF has fillable form fields, values are written
    into those fields by name (mapping below). No coordinates, no reportlab.
  * Coordinate overlay — otherwise, a text layer is drawn at fixed (x, y) points
    (bottom-left origin, PDF points) and merged onto the base page.

The blank base PDFs live next to this module and ARE shipped with the app:
    erpnext_shipping/erpnext_shipping/loss_forms/dpd_form_de.pdf     (Germany, DE/EN, Oirschot)
    erpnext_shipping/erpnext_shipping/loss_forms/dpd_form_belux.pdf  (BE/NL/LU, NL/EN, reply@dpd.be)
"""
import io
import os

import frappe
from frappe.utils import formatdate, nowdate

# --- region -> base PDF file -------------------------------------------------
FORM_FILES = {"de": "lbd_DE.pdf", "belux": "lbd_dutch.pdf"}


def _region(doc):
	"""Which DPD form to render. An explicit Form Language wins; otherwise
	decide by delivery country (Germany -> DE, everything else -> BELUX)."""
	choice = (doc.get("claim_form_language") or "").lower()
	if "germany" in choice or choice.strip() == "de":
		return "de"
	if "belux" in choice or choice.strip() == "nl":
		return "belux"
	country = (doc.delivery_country or "").lower()
	return "de" if country in ("germany", "deutschland", "de") else "belux"


def _base_pdf_path(region):
	path = os.path.join(os.path.dirname(__file__), "loss_forms", FORM_FILES[region])
	if not os.path.exists(path):
		frappe.throw(
			frappe._("Blank DPD form not found: {0}. Add the original PDF to erpnext_shipping/loss_forms/.").format(
				FORM_FILES[region]
			)
		)
	return path


# --- logical field values ----------------------------------------------------
def _field_values(doc):
	"""Logical field name -> printed string. Shared by both strategies; the
	AcroForm mapping and the overlay coordinate map both key off these names."""
	return {
		"dispatch_date": formatdate(doc.pickup_date) if doc.pickup_date else "",
		"tracking_numbers": doc.tracking_numbers or "",
		"sender_name": doc.sender_name or "",
		"receiver_name": doc.receiver_name or "",
		"receiver_address": doc.receiver_address or "",
		"receiver_email": doc.receiver_email or "",
		"receiver_phone": doc.receiver_phone or "",
		# Non-Receipt is always the selected option; Receipt stays empty.
		"non_receipt_mark": "X",
		"sign_date": formatdate(nowdate()),
		# Signatory = the person; company name only when the customer is a Company.
		"sign_name": doc.receiver_name or "",
		"sign_company": _company_name(doc),
	}


def _company_name(doc):
	"""Customer's company name for the 'Name der Firma' line — only when the
	customer is a Company (individuals leave it blank)."""
	cust = doc.customer
	if cust and frappe.db.exists("Customer", cust):
		if (frappe.db.get_value("Customer", cust, "customer_type") or "") == "Company":
			return cust
	return ""


# --- strategy 1: fillable AcroForm ------------------------------------------
# Map our logical names -> the actual field names inside the base PDF.
# Filled in per PDF once we inspect the file (leave a name out to skip it).
ACROFORM_FIELDS = {
	"de": {
		# "logical": "PdfFieldName",
	},
	"belux": {
		# "logical": "PdfFieldName",
	},
}


def _has_acroform(reader):
	try:
		return bool(reader.get_fields())
	except Exception:
		return False


def _fill_acroform(reader, region, values):
	from pypdf import PdfWriter

	mapping = ACROFORM_FIELDS.get(region) or {}
	writer = PdfWriter()
	writer.append(reader)
	field_values = {mapping[k]: values[k] for k in mapping if k in values}
	for page in writer.pages:
		writer.update_page_form_field_values(page, field_values, auto_regenerate=False)
	# make viewers render the filled values
	try:
		writer.set_need_appearances_writer(True)
	except Exception:
		pass
	buf = io.BytesIO()
	writer.write(buf)
	return buf.getvalue()


# --- strategy 2: coordinate overlay -----------------------------------------
# Each field -> (x, y[, size[, max_width]]) in PDF points, bottom-left origin.
# Calibrated against the original DPD PDFs (A4, ~595x842). max_width shrinks the
# font (down to OVERLAY_MIN_SIZE) so long values stay inside their box.
OVERLAY_FONT = "Helvetica"
OVERLAY_FONT_SIZE = 9
OVERLAY_MIN_SIZE = 6.5
OVERLAY_COORDS = {
	# Germany form (lbd_DE.pdf, 595 wide): value cell x=298..524; signing
	# lines at y=145.6 (date/signatory) and y=103.3 (company/signature).
	"de": {
		"dispatch_date":    (315, 562, 9, 205),
		"tracking_numbers": (315, 541, 9, 205),
		"sender_name":      (315, 520, 9, 205),
		"receiver_name":    (315, 503, 9, 205),
		"receiver_address": (315, 494, 7.5, 205),
		"receiver_email":   (315, 480, 9, 205),
		"receiver_phone":   (315, 459, 9, 205),
		"non_receipt_mark": (71.5, 326.5, 10),
		"sign_date":        (76, 150, 9, 195),
		"sign_name":        (303, 150, 9, 215),
		"sign_company":     (76, 108, 9, 195),
	},
	# BELUX form (lbd_dutch.pdf): value cell x=178..560; signing lines at
	# y=212.5 (date/signatory) and y=155.3 (company/signature).
	"belux": {
		"dispatch_date":    (285, 633, 9, 270),
		"tracking_numbers": (285, 609, 9, 270),
		"sender_name":      (285, 586, 9, 270),
		"receiver_name":    (285, 567, 9, 270),
		"receiver_address": (285, 558, 7.5, 270),
		"receiver_email":   (285, 540, 9, 270),
		"receiver_phone":   (285, 517, 9, 270),
		"non_receipt_mark": (36.5, 366.5, 10),
		"sign_date":        (41, 220, 9, 230),
		"sign_name":        (300, 220, 9, 250),
		"sign_company":     (41, 165, 9, 230),
	},
}


def _fit_size(c, text, size, max_width):
	"""Shrink the font until the text fits max_width (or hits the minimum)."""
	if not max_width:
		return size
	from reportlab.pdfbase.pdfmetrics import stringWidth

	while size > OVERLAY_MIN_SIZE and stringWidth(text, OVERLAY_FONT, size) > max_width:
		size -= 0.5
	return size


def _overlay(reader, region, values):
	from pypdf import PdfReader, PdfWriter
	from reportlab.pdfgen import canvas

	coords = OVERLAY_COORDS.get(region) or {}
	base_page = reader.pages[0]
	width = float(base_page.mediabox.width)
	height = float(base_page.mediabox.height)

	buf = io.BytesIO()
	c = canvas.Canvas(buf, pagesize=(width, height))
	c.setFillColorRGB(0, 0, 0)
	for field, pos in coords.items():
		text = str(values.get(field, "") or "")
		if not text:
			continue
		x, y = pos[0], pos[1]
		base_size = pos[2] if len(pos) > 2 else OVERLAY_FONT_SIZE
		max_width = pos[3] if len(pos) > 3 else None
		bold = field == "non_receipt_mark"
		font = "Helvetica-Bold" if bold else OVERLAY_FONT
		for i, line in enumerate(text.split("\n")):
			size = base_size if bold else _fit_size(c, line, base_size, max_width)
			c.setFont(font, size)
			c.drawString(x, y - i * (size + 2), line)
	c.save()
	buf.seek(0)

	overlay_page = PdfReader(buf).pages[0]
	base_page.merge_page(overlay_page)
	writer = PdfWriter()
	writer.add_page(base_page)
	for extra in reader.pages[1:]:
		writer.add_page(extra)
	out = io.BytesIO()
	writer.write(out)
	return out.getvalue()


# --- public ------------------------------------------------------------------
def render_claim_form(doc):
	"""Return the filled DPD form as PDF bytes for the given claim doc."""
	from pypdf import PdfReader

	region = _region(doc)
	path = _base_pdf_path(region)
	values = _field_values(doc)
	reader = PdfReader(path)

	if _has_acroform(reader) and (ACROFORM_FIELDS.get(region)):
		return _fill_acroform(reader, region, values)
	return _overlay(reader, region, values)
