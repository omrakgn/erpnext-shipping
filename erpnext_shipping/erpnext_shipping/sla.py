# Copyright (c) 2026, Frappe Technologies and contributors
# For license information, please see license.txt
"""Delivery SLA (marketplace commitment) tracking.

The SLA date is the delivery date promised to the customer/marketplace: an
explicit Promised Delivery Date if set, otherwise Pickup Date + the carrier's
SLA transit days (Shipment Settings). Each shipment gets an SLA Date and Status
(On Track / At Risk / Breached while open; Met / Missed once delivered). A daily
job emails the internal recipient when an undelivered shipment goes At Risk or
Breaches its SLA — once each — so the team can act on the marketplace.
"""
import frappe
from frappe.utils import add_days, cint, getdate, nowdate

DEFAULT_SLA_DAYS = {"dpd": 3, "fedex": 2}


def _setting(field, default=None):
	val = frappe.db.get_single_value("Shipment Settings", field)
	return default if val in (None, "") else val


def carrier_sla_days(carrier):
	"""SLA transit days for a carrier from settings, with sensible fallbacks."""
	c = (carrier or "").lower()
	if "fedex" in c:
		return cint(_setting("sla_fedex_days", DEFAULT_SLA_DAYS["fedex"]))
	if "dpd" in c:
		return cint(_setting("sla_dpd_days", DEFAULT_SLA_DAYS["dpd"]))
	return cint(_setting("sla_default_days", 3))


def compute_sla_date(sh):
	"""Effective SLA date: promised date if set, else pickup + carrier SLA days."""
	if sh.get("custom_promised_delivery_date"):
		return getdate(sh.get("custom_promised_delivery_date"))
	if not sh.get("pickup_date"):
		return None
	return add_days(getdate(sh.get("pickup_date")), carrier_sla_days(sh.get("carrier")))


def _status(sla_date, delivered_on, today, risk_days):
	if not sla_date:
		return ""
	if delivered_on:
		return "Met" if getdate(delivered_on) <= sla_date else "Missed"
	if today > sla_date:
		return "Breached"
	if today >= add_days(sla_date, -abs(risk_days)):
		return "At Risk"
	return "On Track"


def set_sla_fields(doc, method=None):
	"""Shipment validate hook: keep SLA Date and Status current on the document."""
	sla_date = compute_sla_date(doc)
	doc.custom_sla_date = sla_date
	risk_days = cint(_setting("sla_risk_days", 1))
	delivered_on = doc.get("custom_delivered_at")
	doc.custom_sla_status = _status(sla_date, delivered_on, getdate(nowdate()), risk_days)


def _active_shipments():
	"""Submitted, dispatched shipments within a bounded window (undelivered, or
	delivered within 90 days so their Met/Missed status stays fresh)."""
	return frappe.get_all(
		"Shipment",
		filters=[
			["docstatus", "=", 1],
			["awb_number", "is", "set"],
			["pickup_date", "is", "set"],
			["status", "not in", ["Cancelled", "Completed"]],
			["pickup_date", ">=", add_days(nowdate(), -120)],
		],
		fields=["name"],
		pluck="name",
	)


def flag_and_notify_sla():
	"""Daily job: refresh SLA status for active shipments and email the internal
	recipient when one goes At Risk or Breaches (once each)."""
	today = getdate(nowdate())
	risk_days = cint(_setting("sla_risk_days", 1))
	max_overdue = cint(_setting("sla_notify_max_overdue_days", 14))
	notify = bool(_setting("sla_enabled", 0))
	recipient = _setting("delay_digest_recipient")
	for name in _active_shipments():
		sh = frappe.get_doc("Shipment", name)
		sla_date = compute_sla_date(sh)
		delivered_on = sh.get("custom_delivered_at")
		status = _status(sla_date, delivered_on, today, risk_days)
		if sh.get("custom_sla_date") != sla_date or sh.get("custom_sla_status") != status:
			frappe.db.set_value(
				"Shipment",
				name,
				{"custom_sla_date": sla_date, "custom_sla_status": status},
				update_modified=False,
			)
		if not notify or not recipient or delivered_on or not sla_date:
			continue
		# Presumed-lost shipments belong to the loss/claim workflow, not SLA nudges.
		if sh.get("custom_presumed_lost"):
			continue
		if status == "Breached" and not sh.get("custom_sla_breach_notified"):
			# Skip stale breaches — the marketplace action window is long gone (loss case).
			if max_overdue and (today - sla_date).days > max_overdue:
				continue
			_notify(sh, sla_date, recipient, breached=True)
			frappe.db.set_value("Shipment", name, "custom_sla_breach_notified", nowdate(), update_modified=False)
		elif status == "At Risk" and not sh.get("custom_sla_risk_notified"):
			_notify(sh, sla_date, recipient, breached=False)
			frappe.db.set_value("Shipment", name, "custom_sla_risk_notified", nowdate(), update_modified=False)
	frappe.db.commit()


def _notify(sh, sla_date, recipient, breached):
	"""Internal SLA email, linked to the shipment timeline (no unsubscribe footer)."""
	kind = frappe._("SLA BREACHED") if breached else frappe._("SLA at risk")
	subject = frappe._("{0} — {1} (due {2})").format(
		kind, sh.name, frappe.utils.formatdate(sla_date)
	)
	link = frappe.utils.get_url_to_form("Shipment", sh.name)
	channel = sh.get("custom_sales_channel") or ""
	content = frappe._(
		"<p>Shipment <a href='{0}'>{1}</a> is still undelivered and its delivery SLA "
		"{2} <b>{3}</b>.</p><ul>"
		"<li>Channel: {4}</li><li>Carrier: {5}</li><li>Tracking: {6}</li>"
		"<li>Destination: {7}</li></ul>"
		"<p>Act on the marketplace if needed (expedite / inform the customer).</p>"
	).format(
		link,
		sh.name,
		frappe._("was") if breached else frappe._("is"),
		frappe.utils.formatdate(sla_date),
		channel or frappe._("(unset)"),
		sh.get("carrier") or "",
		sh.get("awb_number") or "",
		sh.get("delivery_customer") or sh.get("delivery_company") or "",
	)
	recipient_list = [r.strip() for r in str(recipient).replace(";", ",").split(",") if r.strip()]
	if not recipient_list:
		return
	from frappe.core.doctype.communication.email import make as _make

	comm = _make(
		doctype="Shipment",
		name=sh.name,
		recipients=", ".join(recipient_list),
		subject=subject,
		content=content,
		communication_medium="Email",
		sent_or_received="Sent",
		send_email=False,
	)
	frappe.sendmail(recipients=recipient_list, subject=subject, message=content, add_unsubscribe_link=False)
	return comm
