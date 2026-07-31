# Copyright (c) 2026, Frappe Technologies and contributors
# For license information, please see license.txt
import frappe
from frappe.model.document import Document
from frappe.utils import add_days, flt

from erpnext_shipping.erpnext_shipping.doctype.pickup_manifest.pickup_manifest import _clean_contact

# Carrier claim windows (days from dispatch) — how long you have to file a loss claim.
DEFAULT_CLAIM_WINDOW = {"dpd": 21, "fedex": 30}


class ShipmentLossClaim(Document):
	def validate(self):
		self.autofill_from_shipment()
		self.set_claim_amount()
		self.set_deadline()
		self.compute_net_loss()

	def autofill_from_shipment(self):
		"""Pull the shipment / delivery details onto the claim when they are empty, so
		the claim (and the DPD form) is self-contained."""
		if not self.shipment:
			return
		sh = frappe.get_doc("Shipment", self.shipment)

		def fill(field, value):
			if not self.get(field) and value:
				self.set(field, value)

		fill("carrier", sh.get("carrier"))
		fill("tracking_numbers", sh.get("awb_number"))
		fill("pickup_date", sh.get("pickup_date"))
		fill("customer", sh.get("delivery_customer") or sh.get("delivery_company"))
		fill(
			"sender_name",
			frappe.db.get_single_value("Shipment Settings", "claim_sender_name")
			or sh.get("pickup_company")
			or frappe.defaults.get_default("company"),
		)
		fill("receiver_name", _clean_contact(sh.get("delivery_contact_name")) or sh.get("delivery_customer"))
		fill("receiver_email", sh.get("delivery_contact_email"))
		fill("receiver_phone", sh.get("delivery_contact_phone"))
		fill("goods_value", sh.get("value_of_goods"))
		fill("shipping_cost", sh.get("custom_shipping_cost"))
		if not self.currency:
			self.currency = sh.get("custom_shipping_cost_currency") or "EUR"

		if not self.delivery_note:
			dns = [r.delivery_note for r in (sh.get("shipment_delivery_note") or []) if r.delivery_note]
			if dns:
				self.delivery_note = dns[0]

		if not self.receiver_address or not self.delivery_country:
			addr = sh.get("delivery_address_name")
			if addr:
				a = frappe.get_doc("Address", addr)
				if not self.receiver_address:
					self.receiver_address = ", ".join(
						p for p in [a.address_line1, a.address_line2, a.pincode, a.city, a.country] if p
					)
				if not self.delivery_country:
					self.delivery_country = a.country

	def set_claim_amount(self):
		if not self.claim_amount:
			self.claim_amount = flt(self.goods_value) + flt(self.shipping_cost)

	def set_deadline(self):
		"""Claim deadline = dispatch date + the carrier's claim window (settings override)."""
		if self.claim_deadline or not self.pickup_date:
			return
		key = "fedex" if "fedex" in (self.carrier or "").lower() else "dpd"
		field = f"{key}_claim_window_days"
		days = frappe.db.get_single_value("Shipment Settings", field) if field else None
		self.claim_deadline = add_days(self.pickup_date, int(days or DEFAULT_CLAIM_WINDOW.get(key, 21)))

	def compute_net_loss(self):
		self.net_loss = flt(self.goods_value) + flt(self.shipping_cost) - flt(self.compensation_amount)


def _claim_email(doc):
	"""Carrier claim-submission recipient by delivery region."""
	country = (doc.delivery_country or "").lower()
	field = (
		"dpd_nl_claim_email"
		if country in ("germany", "deutschland", "de")
		else "dpd_belux_claim_email"
	)
	return frappe.db.get_single_value("Shipment Settings", field)


def _send_claim_email(doc, recipients, subject, content, attachments=None, cc=None):
	"""Send a claim email linked to the claim (shows in its Activity) with no
	unsubscribe footer; attachments = [{fname,fcontent}] or [{file_url}]."""
	recipient_list = [r.strip() for r in str(recipients or "").replace(";", ",").split(",") if r.strip()]
	if not recipient_list:
		frappe.throw(frappe._("No recipient e-mail is set."))
	from frappe.core.doctype.communication.email import make as _make

	_make(
		doctype="Shipment Loss Claim",
		name=doc.name,
		recipients=", ".join(recipient_list),
		cc=cc,
		subject=subject,
		content=content,
		communication_medium="Email",
		sent_or_received="Sent",
		send_email=False,
	)
	frappe.sendmail(
		recipients=recipient_list,
		cc=[c.strip() for c in str(cc or "").replace(";", ",").split(",") if c.strip()] or None,
		subject=subject,
		message=content,
		attachments=attachments or [],
		add_unsubscribe_link=False,
	)


def _form_pdf(doc):
	"""Rendered DPD form as an attachment dict, stamped onto the original PDF."""
	from erpnext_shipping.erpnext_shipping.loss_form import render_claim_form

	return {
		"fname": f"DPD-Non-Receipt-{doc.name}.pdf",
		"fcontent": render_claim_form(doc),
	}


@frappe.whitelist()
def download_claim_form(claim):
	"""Stream the filled DPD form PDF to the browser (Print / Download button)."""
	doc = frappe.get_doc("Shipment Loss Claim", claim)
	att = _form_pdf(doc)
	frappe.local.response.filename = att["fname"]
	frappe.local.response.filecontent = att["fcontent"]
	frappe.local.response.type = "pdf"


@frappe.whitelist()
def email_form_to_customer(claim):
	"""Email the pre-filled DPD declaration form to the customer for signature."""
	doc = frappe.get_doc("Shipment Loss Claim", claim)
	if not doc.receiver_email:
		frappe.throw(
			frappe._("No receiver e-mail — use Print/Download and send it via the marketplace instead.")
		)
	pdf = _form_pdf(doc)
	subject = frappe._("Delivery confirmation form — please sign and return ({0})").format(
		doc.tracking_numbers or claim
	)
	content = frappe._(
		"<p>Dear customer,</p><p>Regarding your order, the carrier needs a signed "
		"confirmation / declaration form to investigate the delivery. Please review the "
		"attached form, tick the correct option, sign it and return it to us.</p>"
		"<p>Thank you.</p>"
	)
	_send_claim_email(doc, doc.receiver_email, subject, content, attachments=[pdf])
	doc.db_set("form_sent_date", frappe.utils.nowdate())
	if doc.status == "Draft":
		doc.db_set("status", "Form Sent to Customer")
	return True


@frappe.whitelist()
def submit_claim_to_carrier(claim):
	"""Email the signed form + purchase invoice to the carrier's claim address."""
	doc = frappe.get_doc("Shipment Loss Claim", claim)
	recipient = _claim_email(doc)
	if not recipient:
		frappe.throw(frappe._("No carrier claim e-mail configured in Shipment Settings."))
	attachments = [{"file_url": f} for f in (doc.signed_form, doc.purchase_invoice) if f]
	if not attachments:
		frappe.throw(frappe._("Attach the signed form (and purchase invoice) before submitting."))
	subject = frappe._("Loss claim — parcel {0}").format(doc.tracking_numbers or claim)
	content = frappe._(
		"<p>Dear DPD,</p><p>Please find attached the signed declaration of non-receipt "
		"and the purchase invoice for the parcel(s) below. We would like to file a loss "
		"claim.</p><ul>"
		"<li>Parcel number(s): {0}</li><li>Dispatch date: {1}</li>"
		"<li>Receiver: {2}</li><li>Goods value: {3}</li></ul><p>Kind regards.</p>"
	).format(
		doc.tracking_numbers or "",
		frappe.utils.formatdate(doc.pickup_date) if doc.pickup_date else "",
		doc.receiver_name or "",
		doc.goods_value or "",
	)
	cc = frappe.db.get_single_value("Shipment Settings", "claim_email_cc")
	_send_claim_email(doc, recipient, subject, content, attachments=attachments, cc=cc)
	doc.db_set("submitted_date", frappe.utils.nowdate())
	doc.db_set("status", "Submitted to Carrier")
	return True


@frappe.whitelist()
def create_loss_claim(shipment, claim_type="Not Delivered"):
	"""Create a draft Shipment Loss Claim pre-filled from the shipment; return its name."""
	existing = frappe.db.get_value(
		"Shipment Loss Claim",
		{"shipment": shipment, "status": ["not in", ["Rejected", "Recovered", "Written Off"]]},
		"name",
	)
	if existing:
		return existing
	doc = frappe.new_doc("Shipment Loss Claim")
	doc.shipment = shipment
	doc.claim_type = claim_type
	doc.incident_date = frappe.utils.nowdate()
	doc.insert(ignore_permissions=True)
	return doc.name
