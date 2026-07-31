# Copyright (c) 2026, Frappe Technologies and contributors
# For license information, please see license.txt
import frappe
from frappe.model.document import Document
from frappe.utils import add_days, cint, flt, nowdate

from erpnext_shipping.erpnext_shipping.doctype.pickup_manifest.pickup_manifest import _clean_contact

# Carrier claim windows (days from dispatch) — how long you have to file a loss claim.
DEFAULT_CLAIM_WINDOW = {"dpd": 21, "fedex": 30}


def _contact_details(contact_name):
	"""Phone (mobile preferred) and email from the linked Contact, so the claim
	fills even when the Shipment doesn't carry the contact phone directly."""
	if not contact_name or not frappe.db.exists("Contact", contact_name):
		return {}
	c = frappe.get_doc("Contact", contact_name)
	phone = c.get("mobile_no") or c.get("phone")
	if not phone:
		for row in c.get("phone_nos") or []:
			if row.get("is_primary_mobile_no") or row.get("is_primary_phone"):
				phone = row.get("phone")
				break
		else:
			nums = c.get("phone_nos") or []
			phone = nums[0].get("phone") if nums else None
	return {"phone": phone, "email": c.get("email_id")}


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
		contact = _contact_details(sh.get("delivery_contact_name"))
		fill("receiver_name", _clean_contact(sh.get("delivery_contact_name")) or sh.get("delivery_customer"))
		fill("receiver_email", sh.get("delivery_contact_email") or contact.get("email"))
		fill("receiver_phone", sh.get("delivery_contact_phone") or contact.get("phone"))
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

	comm = _make(
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
	# Also surface the email on the linked Shipment's timeline.
	comm_name = (comm or {}).get("name")
	if comm_name and doc.shipment:
		try:
			c = frappe.get_doc("Communication", comm_name)
			c.add_link("Shipment", doc.shipment)
			c.save(ignore_permissions=True)
		except Exception:
			frappe.log_error(frappe.get_traceback(), "loss claim email timeline link")
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


def _form_email_defaults(doc):
	"""Default recipient / subject / body for the customer form email."""
	subject = frappe._("Delivery confirmation form — please sign and return ({0})").format(
		doc.tracking_numbers or doc.name
	)
	content = frappe._(
		"<p>Dear customer,</p><p>Regarding your order, the carrier needs a signed "
		"confirmation / declaration form to investigate the delivery. Please review the "
		"attached form, tick the correct option, sign it and return it to us.</p>"
		"<p>Thank you.</p>"
	)
	return {"recipient": doc.receiver_email or "", "subject": subject, "content": content}


def _carrier_email_defaults(doc):
	"""Default recipient / subject / body for the carrier claim submission."""
	subject = frappe._("Loss claim — parcel {0}").format(doc.tracking_numbers or doc.name)
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
	return {"recipient": _claim_email(doc) or "", "subject": subject, "content": content}


@frappe.whitelist()
def get_form_email_draft(claim):
	"""Editable draft for the customer form email (shown in a confirm dialog)."""
	return _form_email_defaults(frappe.get_doc("Shipment Loss Claim", claim))


@frappe.whitelist()
def get_carrier_email_draft(claim):
	"""Editable draft for the carrier claim email (shown in a confirm dialog)."""
	return _carrier_email_defaults(frappe.get_doc("Shipment Loss Claim", claim))


@frappe.whitelist()
def email_form_to_customer(claim, recipient=None, subject=None, content=None):
	"""Email the pre-filled DPD declaration form to the customer for signature.
	recipient/subject/content are the (edited) values confirmed in the dialog;
	they fall back to the defaults when not supplied."""
	doc = frappe.get_doc("Shipment Loss Claim", claim)
	d = _form_email_defaults(doc)
	recipient = recipient or d["recipient"]
	if not recipient:
		frappe.throw(
			frappe._("No receiver e-mail — use Print/Download and send it via the marketplace instead.")
		)
	pdf = _form_pdf(doc)
	_send_claim_email(doc, recipient, subject or d["subject"], content or d["content"], attachments=[pdf])
	doc.db_set("form_sent_date", frappe.utils.nowdate())
	if doc.status == "Draft":
		doc.db_set("status", "Form Sent to Customer")
	return True


@frappe.whitelist()
def submit_claim_to_carrier(claim, recipient=None, subject=None, content=None):
	"""Email the signed form + purchase invoice to the carrier's claim address."""
	doc = frappe.get_doc("Shipment Loss Claim", claim)
	d = _carrier_email_defaults(doc)
	recipient = recipient or d["recipient"]
	if not recipient:
		frappe.throw(frappe._("No carrier claim e-mail configured in Shipment Settings."))
	attachments = [{"file_url": f} for f in (doc.signed_form, doc.purchase_invoice) if f]
	if not attachments:
		frappe.throw(frappe._("Attach the signed form (and purchase invoice) before submitting."))
	cc = frappe.db.get_single_value("Shipment Settings", "claim_email_cc")
	_send_claim_email(
		doc, recipient, subject or d["subject"], content or d["content"], attachments=attachments, cc=cc
	)
	doc.db_set("submitted_date", frappe.utils.nowdate())
	doc.db_set("status", "Submitted to Carrier")
	return True


OPEN_CLAIM_STATUSES = ["Rejected", "Recovered", "Written Off"]


def _create_one_claim(shipment, claim_type, tracking_number=None):
	"""Create one draft claim (deduped per shipment + tracking number); the
	tracking number scopes the claim to a single parcel when given."""
	filters = {"shipment": shipment, "status": ["not in", OPEN_CLAIM_STATUSES]}
	if tracking_number:
		filters["tracking_numbers"] = tracking_number
	existing = frappe.db.get_value("Shipment Loss Claim", filters, "name")
	if existing:
		return existing
	doc = frappe.new_doc("Shipment Loss Claim")
	doc.shipment = shipment
	doc.claim_type = claim_type
	doc.incident_date = frappe.utils.nowdate()
	if tracking_number:
		# set before insert so autofill keeps this single parcel (not all AWBs)
		doc.tracking_numbers = tracking_number
	doc.insert(ignore_permissions=True)
	return doc.name


@frappe.whitelist()
def create_loss_claim(shipment, claim_type="Not Delivered", tracking_number=None):
	"""Create a draft claim for one parcel (or the whole shipment); return its name."""
	return _create_one_claim(shipment, claim_type, tracking_number or None)


@frappe.whitelist()
def create_loss_claims(shipment, claim_type="Not Delivered", tracking_numbers=None):
	"""Create one claim per selected parcel tracking number; return their names."""
	tns = frappe.parse_json(tracking_numbers) if tracking_numbers else [None]
	return [_create_one_claim(shipment, claim_type, tn or None) for tn in (tns or [None])]


@frappe.whitelist()
def get_claim_parcels(shipment):
	"""Parcels of a shipment for the 'which parcel is lost' picker, flagging any
	that already have an open claim."""
	from erpnext_shipping.erpnext_shipping.shipping import get_shipment_parcel_breakdown

	rows = get_shipment_parcel_breakdown(shipment)
	for r in rows:
		tn = r.get("tracking_number")
		r["existing_claim"] = (
			frappe.db.get_value(
				"Shipment Loss Claim",
				{"shipment": shipment, "tracking_numbers": tn, "status": ["not in", OPEN_CLAIM_STATUSES]},
				"name",
			)
			if tn
			else None
		)
	return rows


# --- deadline reminder (scheduler) ------------------------------------------
# Claims already handed to the carrier (or closed) no longer need a reminder.
REMINDER_DONE_STATUSES = [
	"Submitted to Carrier",
	"Under Review",
	"Approved",
	"Rejected",
	"Paid",
	"Written Off",
	"Recovered",
]


def remind_claim_deadlines():
	"""Daily job: email the internal recipient about claims whose carrier deadline
	is within the reminder window and that haven't been submitted yet. Once per
	claim (guarded by deadline_reminded)."""
	days = cint(frappe.db.get_single_value("Shipment Settings", "claim_deadline_reminder_days"))
	if days <= 0:
		return
	recipient = frappe.db.get_single_value("Shipment Settings", "delay_digest_recipient")
	if not recipient:
		return
	claims = frappe.get_all(
		"Shipment Loss Claim",
		filters={
			"claim_deadline": ["<=", add_days(nowdate(), days)],
			"submitted_date": ["is", "not set"],
			"deadline_reminded": ["is", "not set"],
			"status": ["not in", REMINDER_DONE_STATUSES],
		},
		pluck="name",
	)
	for name in claims:
		doc = frappe.get_doc("Shipment Loss Claim", name)
		if not doc.claim_deadline:
			continue
		_send_deadline_reminder(doc, recipient)
		doc.db_set("deadline_reminded", nowdate())
	frappe.db.commit()


def _send_deadline_reminder(doc, recipient):
	overdue = doc.claim_deadline < nowdate()
	subject = frappe._("{0}Claim deadline {1} — {2}").format(
		"OVERDUE: " if overdue else "",
		frappe.utils.formatdate(doc.claim_deadline),
		doc.tracking_numbers or doc.name,
	)
	link = frappe.utils.get_url_to_form("Shipment Loss Claim", doc.name)
	content = frappe._(
		"<p>The loss claim <a href='{0}'>{1}</a> has not been submitted to the carrier "
		"and its filing deadline is <b>{2}</b>{3}.</p>"
		"<ul><li>Carrier: {4}</li><li>Parcel(s): {5}</li><li>Goods value: {6}</li></ul>"
		"<p>Collect the signed form + invoice and use <b>Submit to Carrier</b> before the deadline.</p>"
	).format(
		link,
		doc.name,
		frappe.utils.formatdate(doc.claim_deadline),
		frappe._(" (already passed)") if overdue else "",
		doc.carrier or "",
		doc.tracking_numbers or "",
		doc.goods_value or "",
	)
	_send_claim_email(doc, recipient, subject, content)


# --- replacement shipment (reship) ------------------------------------------
# Cleared on the copied Shipment so the reship starts as a fresh, untracked draft.
_RESET_SHIPMENT_FIELDS = [
	"shipment_id",
	"awb_number",
	"tracking_url",
	"tracking_status",
	"carrier",
	"service_provider",
	"custom_delivered_at",
	"custom_tracking_details",
	"custom_is_delayed",
	"custom_delay_notified",
	"custom_delay_notified_at",
	"custom_presumed_lost",
	"custom_label_removed",
	"custom_shipping_cost",
	"custom_surcharge_amount",
	"custom_has_weight_surcharge",
	"custom_cost_variance",
	"custom_cost_variance_pct",
	"custom_customer_shipping_charge",
	"custom_shipping_margin",
	"shipment_amount",
]


@frappe.whitelist()
def create_replacement_shipment(claim):
	"""Reship a lost parcel: duplicate the original Delivery Note and Shipment as
	fresh drafts (untracked), link the new Shipment back on the claim. Manual only."""
	doc = frappe.get_doc("Shipment Loss Claim", claim)
	if doc.replacement_shipment:
		return {"shipment": doc.replacement_shipment}
	if not doc.shipment:
		frappe.throw(frappe._("This claim has no linked Shipment to reship."))
	orig = frappe.get_doc("Shipment", doc.shipment)

	dn_name = doc.delivery_note
	if not dn_name and orig.get("shipment_delivery_note"):
		dn_name = orig.shipment_delivery_note[0].delivery_note
	new_dn_name = None
	if dn_name and frappe.db.exists("Delivery Note", dn_name):
		new_dn = frappe.copy_doc(frappe.get_doc("Delivery Note", dn_name))
		new_dn.set("posting_date", nowdate())
		new_dn.set("set_posting_time", 1)
		new_dn.set(
			"remarks",
			frappe._("Replacement for lost shipment {0} (claim {1}).").format(doc.shipment, doc.name),
		)
		new_dn.insert(ignore_permissions=True)
		new_dn_name = new_dn.name

	new_ship = frappe.copy_doc(orig)
	for f in _RESET_SHIPMENT_FIELDS:
		if new_ship.meta.has_field(f):
			new_ship.set(f, None)
	new_ship.set("pickup_date", nowdate())
	new_ship.set("shipment_delivery_note", [])
	if new_dn_name:
		new_ship.append("shipment_delivery_note", {"delivery_note": new_dn_name})
	new_ship.insert(ignore_permissions=True)

	doc.db_set("replacement_shipment", new_ship.name)
	return {"shipment": new_ship.name, "delivery_note": new_dn_name}
