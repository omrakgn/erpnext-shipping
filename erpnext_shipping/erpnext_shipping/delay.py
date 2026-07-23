# Copyright (c) 2026, Frappe Technologies and contributors
# For license information, please see license.txt
"""Delayed-shipment detection, flagging, digest email and carrier inquiry email.

Detection is at the Shipment level (a shipment counts as delayed until *every* one
of its parcels is delivered — custom_delivered_at is only set when all are). The
report, digest and carrier email then drill down to the individual undelivered
tracking numbers using the per-parcel custom_tracking_details JSON, so a shipment
with several labels shows exactly which parcel(s) are stuck rather than one blurred
row of all tracking numbers.

Shared by the Delayed Shipments report, the "Delayed" number card, the daily
scheduler (flag + digest) and the "Ask Carrier about Delay" button on Shipment.
"""
import json

import frappe
from frappe import _
from frappe.utils import add_days, cint, now_datetime, nowdate

DELAY_EMAIL_TEMPLATE = "Shipment Delay Inquiry"
DEFAULT_MIN_DAYS = 5


def _delay_min_days(fallback=None):
	"""Configured delay threshold (days) from Shipment Settings, else the default."""
	val = frappe.db.get_single_value("Shipment Settings", "delay_min_days")
	return cint(val) or cint(fallback) or DEFAULT_MIN_DAYS


def _parse_parcels(details_json, awb_fallback):
	"""Per-parcel list from custom_tracking_details JSON; fall back to splitting the
	combined awb_number when no detail is stored yet (older / not-yet-tracked)."""
	try:
		parcels = json.loads(details_json or "[]")
	except Exception:
		parcels = []
	if not parcels:
		awbs = [a.strip() for a in (awb_fallback or "").replace(";", ",").split(",") if a.strip()]
		parcels = [{"tracking_number": a, "status": "", "carrier": ""} for a in awbs]
	return parcels


def _undelivered_parcels(parcels, carrier=None):
	"""Keep only parcels not yet delivered, optionally for one carrier (substring
	match, e.g. 'dpd' matches 'DPD NL')."""
	out = []
	cf = (carrier or "").lower()
	for p in parcels:
		if (p.get("status") or "").strip().lower() == "delivered":
			continue
		if cf and cf not in (p.get("carrier") or "").lower():
			continue
		out.append(p)
	return out


def get_delayed_shipments(min_days=None, carrier=None):
	"""One row per *undelivered parcel* of every shipment that is delayed (not
	delivered `min_days`+ days after Pickup Date). `cnt` is 1 only on the first row
	of each shipment so Sum(cnt) counts distinct shipments (used by the number card).

	Shared source for the report, the number card and the scheduler so all three
	agree on what "delayed" means.
	"""
	min_days = cint(min_days) or _delay_min_days()
	cutoff = add_days(nowdate(), -abs(min_days))

	shipments = frappe.db.sql(
		"""
		select
			name as shipment,
			carrier,
			pickup_date,
			datediff(curdate(), pickup_date) as days_elapsed,
			tracking_status,
			awb_number,
			custom_tracking_details,
			coalesce(delivery_customer, delivery_supplier, delivery_company) as delivery_to
		from `tabShipment`
		where docstatus < 2
			and ifnull(tracking_status, '') != 'Delivered'
			and custom_delivered_at is null
			and pickup_date is not null
			and pickup_date <= %(cutoff)s
			and ifnull(status, '') not in ('Cancelled', 'Completed')
			and ifnull(awb_number, '') != ''
		order by pickup_date asc
		""",
		{"cutoff": cutoff},
		as_dict=True,
	)

	rows = []
	for s in shipments:
		undelivered = _undelivered_parcels(
			_parse_parcels(s.custom_tracking_details, s.awb_number), carrier
		)
		first = True
		for p in undelivered:
			tn = p.get("tracking_number") or ""
			pcarrier = p.get("carrier") or s.carrier
			turl = p.get("tracking_url") or ""
			if not turl and tn and pcarrier:
				turl = _tracking_url(pcarrier, tn)
			rows.append(
				{
					"shipment": s.shipment,
					"carrier": pcarrier,
					"pickup_date": s.pickup_date,
					"days_elapsed": s.days_elapsed,
					"tracking_status": p.get("status") or s.tracking_status,
					"awb_number": tn,
					"tracking_url": turl,
					"delivery_to": s.delivery_to,
					"cnt": 1 if first else 0,
				}
			)
			first = False
	return rows


def _tracking_url(carrier, tracking_number):
	"""Best-effort carrier tracking URL when SendCloud did not store one."""
	try:
		from erpnext_shipping.erpnext_shipping.utils import get_tracking_url

		return get_tracking_url(carrier, tracking_number) or ""
	except Exception:
		return ""


def flag_and_notify_delayed():
	"""Daily job: keep the custom_is_delayed flag in sync, then (once per shipment)
	send the enabled delay emails. Both the internal notification and the carrier
	email are created with communication.email.make so they are linked to the
	Shipment and appear in its Activity/timeline."""
	min_days = _delay_min_days()
	rows = get_delayed_shipments(min_days)

	by_ship = {}
	for r in rows:
		by_ship.setdefault(r["shipment"], []).append(r)
	delayed = set(by_ship)

	# Flag currently-delayed shipments.
	for name in delayed:
		if not frappe.db.get_value("Shipment", name, "custom_is_delayed"):
			frappe.db.set_value("Shipment", name, "custom_is_delayed", 1, update_modified=False)

	# Clear flag (and the notified marker) on shipments no longer delayed, so a later
	# re-delay notifies again.
	for name in frappe.get_all("Shipment", filters={"custom_is_delayed": 1}, pluck="name"):
		if name not in delayed:
			frappe.db.set_value(
				"Shipment",
				name,
				{"custom_is_delayed": 0, "custom_delay_notified": 0},
				update_modified=False,
			)

	frappe.db.commit()

	if not rows:
		return

	auto_internal = frappe.db.get_single_value("Shipment Settings", "auto_email_internal")
	auto_carrier = frappe.db.get_single_value("Shipment Settings", "auto_email_carrier")
	if not (auto_internal or auto_carrier):
		return

	raw = frappe.db.get_single_value("Shipment Settings", "delay_digest_recipient") or ""
	internal_recipients = ", ".join(e.strip() for e in raw.replace(";", ",").split(",") if e.strip())

	# Skip old / abandoned untracked shipments: only auto-email those delayed at most
	# this many days (0 = no upper limit). They still stay flagged and in the report.
	max_days = cint(frappe.db.get_single_value("Shipment Settings", "delay_notify_max_days"))

	for name, ship_rows in by_ship.items():
		# Once per shipment — do not resend every day.
		if frappe.db.get_value("Shipment", name, "custom_delay_notified"):
			continue
		# Too old to bother the carrier / ourselves about.
		if max_days and cint(ship_rows[0].get("days_elapsed")) > max_days:
			continue
		sent = False
		try:
			if auto_internal and internal_recipients:
				sent = _send_internal_delay_email(name, ship_rows, internal_recipients, min_days) or sent
			if auto_carrier:
				sent = _send_carrier_delay_email(name) or sent
		except Exception:
			frappe.log_error(
				title="Delay auto-email failed",
				message=f"Shipment: {name}\n{frappe.get_traceback()}",
			)
			continue
		if sent:
			frappe.db.set_value(
				"Shipment",
				name,
				{"custom_delay_notified": 1, "custom_delay_notified_at": now_datetime()},
				update_modified=False,
			)

	frappe.db.commit()


def _make_linked_email(shipment, recipients, subject, content):
	"""Send an email linked to the Shipment (reference_doctype/name) so it shows in
	the shipment's Activity/timeline. Returns False when there is no recipient."""
	if not recipients:
		return False
	from frappe.core.doctype.communication.email import make as _make

	_make(
		doctype="Shipment",
		name=shipment,
		recipients=recipients,
		subject=subject,
		content=content,
		communication_medium="Email",
		sent_or_received="Sent",
		send_email=True,
	)
	return True


def _send_internal_delay_email(shipment, ship_rows, recipients, min_days):
	subject = _("Delayed: {0} — {1} parcel(s), {2}+ days").format(shipment, len(ship_rows), min_days)
	return _make_linked_email(shipment, recipients, subject, _digest_html(ship_rows, min_days))


def _send_carrier_delay_email(shipment):
	d = get_carrier_delay_email(shipment)
	return _make_linked_email(shipment, d.get("recipients"), d.get("subject"), d.get("content"))


def _digest_html(rows, min_days):
	head = "".join(
		f"<th style='text-align:left;padding:6px 10px;border-bottom:2px solid #d1d8dd'>{h}</th>"
		for h in (
			_("Shipment"),
			_("Carrier"),
			_("Tracking"),
			_("Status"),
			_("Pickup"),
			_("Days"),
			_("To"),
		)
	)
	body = []
	for r in rows:
		cells = [
			r.get("shipment") or "",
			r.get("carrier") or "",
			r.get("awb_number") or "",
			r.get("tracking_status") or "",
			frappe.utils.formatdate(r.get("pickup_date")) if r.get("pickup_date") else "",
			str(r.get("days_elapsed") or ""),
			r.get("delivery_to") or "",
		]
		tds = "".join(
			f"<td style='padding:6px 10px;border-bottom:1px solid #ebeff2'>{frappe.utils.escape_html(str(c))}</td>"
			for c in cells
		)
		body.append(f"<tr>{tds}</tr>")

	return f"""
	<p>{_("The following parcels have not been delivered {0}+ days after pickup.").format(min_days)}</p>
	<table style='border-collapse:collapse;font-size:13px'>
		<thead><tr>{head}</tr></thead>
		<tbody>{"".join(body)}</tbody>
	</table>
	"""


def _carrier_key(carrier):
	name = (carrier or "").lower()
	if "fedex" in name:
		return "fedex"
	if "dpd" in name:
		return "dpd"
	return ""


def _carrier_delay_recipient(carrier):
	"""Pick the configured DPD/FedEx inquiry email for this shipment's carrier."""
	key = _carrier_key(carrier)
	if key == "fedex":
		return frappe.db.get_single_value("Shipment Settings", "fedex_delay_email") or ""
	if key == "dpd":
		return frappe.db.get_single_value("Shipment Settings", "dpd_delay_email") or ""
	return ""


@frappe.whitelist()
def get_carrier_delay_email(shipment):
	"""Return a ready-to-send email draft (recipient + subject + body) for asking the
	carrier about a delayed shipment. Only the *undelivered* tracking numbers for the
	resolved carrier are listed, so a multi-label shipment does not leak already-
	delivered parcels or the other carrier's numbers to the wrong carrier."""
	doc = frappe.get_doc("Shipment", shipment)

	key = _carrier_key(doc.carrier)
	parcels = _parse_parcels(doc.get("custom_tracking_details"), doc.awb_number)
	undelivered = _undelivered_parcels(parcels, key) or _undelivered_parcels(parcels)
	tracking_numbers = [p.get("tracking_number") for p in undelivered if p.get("tracking_number")]

	context = {
		"doc": doc,
		"shipment": doc,
		"tracking_numbers": tracking_numbers,
		"carrier": doc.carrier,
	}

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
