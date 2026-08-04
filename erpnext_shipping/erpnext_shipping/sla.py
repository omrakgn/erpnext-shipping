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
import math

import frappe
from frappe.utils import add_days, cint, getdate, now_datetime, nowdate

DEFAULT_SLA_DAYS = {"dpd": 3, "fedex": 2}
# Max realistic transit days; above this a delivery is an outlier (stuck/lost) and
# is excluded from the learned averages.
MAX_LEARN_TRANSIT = 21


def _setting(field, default=None):
	val = frappe.db.get_single_value("Shipment Settings", field)
	return default if val in (None, "") else val


def carrier_sla_days(carrier):
	"""Flat per-carrier SLA days from settings (legacy baseline; the primary path
	is now the learned destination lanes, see sla_transit_days)."""
	c = (carrier or "").lower()
	if "fedex" in c:
		return cint(_setting("sla_fedex_days", DEFAULT_SLA_DAYS["fedex"]))
	if "dpd" in c:
		return cint(_setting("sla_dpd_days", DEFAULT_SLA_DAYS["dpd"]))
	return cint(_setting("sla_default_days", 3))


def _norm_carrier(carrier):
	return (carrier or "").split(",")[0].strip().lower()


def _destination(sh):
	"""Delivery country / postal / city (normalised) from the delivery Address."""
	addr = sh.get("delivery_address_name")
	a = (
		frappe.db.get_value("Address", addr, ["country", "pincode", "city"], as_dict=True)
		if addr
		else None
	) or {}
	return {
		"country": (a.get("country") or "").strip().lower(),
		"pincode": (a.get("pincode") or "").replace(" ", "").upper(),
		"city": (a.get("city") or "").strip().lower(),
	}


def sla_transit_days(sh):
	"""Learned transit-day SLA for this shipment's carrier + destination: the most
	specific lane wins (postal code -> city -> country); when the destination has
	no history, fall back to the configurable unknown-destination default."""
	unknown = cint(_setting("sla_unknown_days", 5)) or 5
	carrier = _norm_carrier(sh.get("carrier"))
	dest = _destination(sh)
	if carrier and dest["country"]:
		for level, key in (("Postal", dest["pincode"]), ("City", dest["city"]), ("Country", "")):
			if level != "Country" and not key:
				continue
			days = frappe.db.get_value(
				"Carrier SLA Lane",
				{"carrier": carrier, "country": dest["country"], "region_type": level, "region_key": key},
				"sla_days",
			)
			if days:
				return cint(days)
	return unknown


def _base_date(sh):
	"""Dispatch reference for the SLA: Pickup Date, else the linked Delivery Note
	date, else the Shipment's creation date — so an SLA is always computable
	(and always dispatch + lead, never same-day) without manual entry."""
	if sh.get("pickup_date"):
		return getdate(sh.get("pickup_date"))
	for row in sh.get("shipment_delivery_note") or []:
		if row.get("delivery_note"):
			pd = frappe.db.get_value("Delivery Note", row.get("delivery_note"), "posting_date")
			if pd:
				return getdate(pd)
	return getdate(sh.get("creation")) if sh.get("creation") else None


def compute_sla_date(sh):
	"""Effective SLA date: the Promised Delivery Date if set, otherwise the
	dispatch date (see _base_date) + the carrier's SLA transit days. No manual
	date entry is needed — leave Promised Delivery Date blank to auto-compute."""
	if sh.get("custom_promised_delivery_date"):
		return getdate(sh.get("custom_promised_delivery_date"))
	base = _base_date(sh)
	if not base:
		return None
	return add_days(base, sla_transit_days(sh))


def rebuild_carrier_sla_lanes():
	"""Daily job: learn the transit-day SLA per carrier + destination from the
	actual delivery times of delivered shipments, at three granularities (postal
	code -> city -> country). Rebuilds all non-manual lanes each run."""
	min_samples = cint(_setting("sla_min_samples", 1)) or 1
	rows = frappe.db.sql(
		"""
		select sh.carrier as carrier, addr.country as country,
			addr.pincode as pincode, addr.city as city,
			sh.custom_transit_days as td
		from `tabShipment` sh
		left join `tabAddress` addr on addr.name = sh.delivery_address_name
		where sh.custom_delivered_at is not null
			and sh.custom_transit_days is not null
			and sh.custom_transit_days >= 0 and sh.custom_transit_days <= %s
			and ifnull(sh.carrier, '') != ''
			and ifnull(addr.country, '') != ''
		""",
		MAX_LEARN_TRANSIT,
		as_dict=True,
	)

	agg = {}

	def add(carrier, country, level, key, td):
		s = agg.setdefault((carrier, country, level, key), [0.0, 0])
		s[0] += td
		s[1] += 1

	for r in rows:
		carrier = _norm_carrier(r.carrier)
		country = (r.country or "").strip().lower()
		if not carrier or not country:
			continue
		td = float(r.td or 0)
		pincode = (r.pincode or "").replace(" ", "").upper()
		city = (r.city or "").strip().lower()
		add(carrier, country, "Country", "", td)
		if city:
			add(carrier, country, "City", city, td)
		if pincode:
			add(carrier, country, "Postal", pincode, td)

	# Preserve manually-overridden lanes; rebuild the rest.
	manual = {
		(l.carrier, l.country, l.region_type, l.region_key or "")
		for l in frappe.get_all(
			"Carrier SLA Lane",
			filters={"manual_override": 1},
			fields=["carrier", "country", "region_type", "region_key"],
		)
	}
	for name in frappe.get_all("Carrier SLA Lane", filters={"manual_override": 0}, pluck="name"):
		frappe.delete_doc("Carrier SLA Lane", name, force=True, ignore_permissions=True)

	stamp = now_datetime()
	for (carrier, country, level, key), (total, cnt) in agg.items():
		if cnt < min_samples or (carrier, country, level, key) in manual:
			continue
		avg = total / cnt
		doc = frappe.new_doc("Carrier SLA Lane")
		doc.update(
			{
				"carrier": carrier,
				"country": country,
				"region_type": level,
				"region_key": key,
				"avg_transit_days": round(avg, 1),
				"sla_days": max(1, int(math.ceil(avg))),
				"sample_count": cnt,
				"last_computed": stamp,
			}
		)
		doc.insert(ignore_permissions=True)
	frappe.db.commit()


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
