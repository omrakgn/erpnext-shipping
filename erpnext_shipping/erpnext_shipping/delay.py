# Copyright (c) 2026, Frappe Technologies and contributors
# For license information, please see license.txt
"""Delayed-shipment detection, flagging, digest email and carrier inquiry email.

Shared by the Delayed Shipments report, the "Delayed" number card, the daily
scheduler (flag + digest) and the "Ask Carrier about Delay" button on Shipment.
"""
import frappe
from frappe import _
from frappe.utils import add_days, cint, nowdate

DELAY_EMAIL_TEMPLATE = "Shipment Delay Inquiry"
DEFAULT_MIN_DAYS = 5


def _delay_min_days(fallback=None):
	"""Configured delay threshold (days) from Shipment Settings, else the default."""
	val = frappe.db.get_single_value("Shipment Settings", "delay_min_days")
	return cint(val) or cint(fallback) or DEFAULT_MIN_DAYS


def get_delayed_shipments(min_days=None, carrier=None):
	"""Shipments not delivered `min_days`+ days after Pickup Date.

	One shared query for the report, the number card and the scheduler so the three
	always agree on what "delayed" means.
	"""
	min_days = cint(min_days) or _delay_min_days()
	cutoff = add_days(nowdate(), -abs(min_days))

	conds = [
		"docstatus < 2",
		"ifnull(tracking_status, '') != 'Delivered'",
		"custom_delivered_at is null",
		"pickup_date is not null",
		"pickup_date <= %(cutoff)s",
		"ifnull(status, '') not in ('Cancelled', 'Completed')",
		"ifnull(awb_number, '') != ''",
	]
	values = {"cutoff": cutoff}
	if carrier:
		conds.append("carrier = %(carrier)s")
		values["carrier"] = carrier
	where = " and ".join(conds)

	return frappe.db.sql(
		f"""
		select
			name as shipment,
			carrier,
			pickup_date,
			datediff(curdate(), pickup_date) as days_elapsed,
			tracking_status,
			awb_number,
			coalesce(delivery_customer, delivery_supplier, delivery_company) as delivery_to,
			1 as cnt
		from `tabShipment`
		where {where}
		order by pickup_date asc
		""",
		values,
		as_dict=True,
	)


def flag_and_notify_delayed():
	"""Daily job: (re)compute the delayed set, keep the `custom_is_delayed` flag in
	sync (set on delayed, clear on the rest) and, if enabled, email the digest."""
	min_days = _delay_min_days()
	rows = get_delayed_shipments(min_days)
	delayed = {r["shipment"] for r in rows}

	# Set flag on currently-delayed shipments.
	for name in delayed:
		if not frappe.db.get_value("Shipment", name, "custom_is_delayed"):
			frappe.db.set_value("Shipment", name, "custom_is_delayed", 1, update_modified=False)

	# Clear flag on shipments that were flagged before but are no longer delayed
	# (delivered / cancelled / within threshold).
	for name in frappe.get_all("Shipment", filters={"custom_is_delayed": 1}, pluck="name"):
		if name not in delayed:
			frappe.db.set_value("Shipment", name, "custom_is_delayed", 0, update_modified=False)

	frappe.db.commit()

	if not rows:
		return

	if not frappe.db.get_single_value("Shipment Settings", "enable_delay_digest"):
		return

	raw = frappe.db.get_single_value("Shipment Settings", "delay_digest_recipient") or ""
	recipients = [e.strip() for e in raw.replace(";", ",").split(",") if e.strip()]
	if not recipients:
		return

	frappe.sendmail(
		recipients=recipients,
		subject=_("Delayed shipments: {0} not delivered ({1}+ days)").format(len(rows), min_days),
		message=_digest_html(rows, min_days),
		reference_doctype="Shipment",
	)


def _digest_html(rows, min_days):
	head = "".join(
		f"<th style='text-align:left;padding:6px 10px;border-bottom:2px solid #d1d8dd'>{h}</th>"
		for h in (_("Shipment"), _("Carrier"), _("Pickup"), _("Days"), _("Status"), _("Tracking"), _("To"))
	)
	body = []
	for r in rows:
		cells = [
			r.get("shipment") or "",
			r.get("carrier") or "",
			frappe.utils.formatdate(r.get("pickup_date")) if r.get("pickup_date") else "",
			str(r.get("days_elapsed") or ""),
			r.get("tracking_status") or "",
			r.get("awb_number") or "",
			r.get("delivery_to") or "",
		]
		tds = "".join(
			f"<td style='padding:6px 10px;border-bottom:1px solid #ebeff2'>{frappe.utils.escape_html(str(c))}</td>"
			for c in cells
		)
		body.append(f"<tr>{tds}</tr>")

	return f"""
	<p>{_("The following shipments have not been delivered {0}+ days after pickup.").format(min_days)}</p>
	<table style='border-collapse:collapse;font-size:13px'>
		<thead><tr>{head}</tr></thead>
		<tbody>{"".join(body)}</tbody>
	</table>
	"""


def _carrier_delay_recipient(carrier):
	"""Pick the configured DPD/FedEx inquiry email for this shipment's carrier."""
	name = (carrier or "").lower()
	if "fedex" in name:
		return frappe.db.get_single_value("Shipment Settings", "fedex_delay_email") or ""
	if "dpd" in name:
		return frappe.db.get_single_value("Shipment Settings", "dpd_delay_email") or ""
	return ""


@frappe.whitelist()
def get_carrier_delay_email(shipment):
	"""Return a ready-to-send email draft (recipient + subject + body) for asking the
	carrier about a delayed shipment. The body is rendered from the Email Template so
	tracking number, pickup date, recipient etc. are filled automatically."""
	doc = frappe.get_doc("Shipment", shipment)
	context = {"doc": doc, "shipment": doc}

	subject = content = ""
	if frappe.db.exists("Email Template", DELAY_EMAIL_TEMPLATE):
		t = frappe.get_doc("Email Template", DELAY_EMAIL_TEMPLATE)
		subject = frappe.render_template(t.subject or "", context)
		content = frappe.render_template(t.response_html or t.response or "", context)

	return {
		"recipients": _carrier_delay_recipient(doc.carrier),
		"subject": subject,
		"content": content,
	}
