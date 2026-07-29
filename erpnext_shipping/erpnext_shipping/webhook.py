# Copyright (c) 2026, Frappe Technologies and contributors
# For license information, please see license.txt
"""SendCloud webhook receiver.

Registered in the SendCloud panel as the integration webhook URL:
    https://<site>/api/method/erpnext_shipping.erpnext_shipping.webhook.sendcloud_webhook

SendCloud POSTs a JSON body (with an HMAC-SHA256 signature in the
`Sendcloud-Signature` header) whenever a parcel's status changes. We match the
parcel to a Shipment and enqueue a normal tracking refresh — so the existing
update_tracking logic (delivered_at, transit days, label-removed, Delivery Note
updates) runs event-driven instead of on the hourly poll.
"""
import hashlib
import hmac
import json
import logging

import frappe


@frappe.whitelist(allow_guest=True)
def sendcloud_webhook():
	body = frappe.request.get_data() if frappe.request else b""
	sig = _signature_result(body)

	try:
		payload = json.loads(body or b"{}")
	except Exception:
		payload = {}

	action = payload.get("action")
	parcel = payload.get("parcel") or {}
	parcel_id = str(parcel.get("id") or "")
	tracking = parcel.get("tracking_number")
	status = (parcel.get("status") or {}).get("message")

	# Sessiz denetim logu (logs browser'da değil, site log dosyasında).
	# sig: None = secret yok (doğrulama kapalı), True/False = HMAC eşleşme sonucu.
	_log(f"action={action!r} parcel={parcel_id} status={status!r} tracking={tracking} sig={sig}")

	# İmza secret'ı tanımlı ve eşleşMİYORSA reddet (sahte webhook koruması).
	if sig is False:
		frappe.local.response["http_status_code"] = 401
		return {"ok": False, "error": "invalid signature"}

	# Bağlantı testi / diğer aksiyonlar: 200 dön, işlem yok.
	if action != "parcel_status_changed":
		return {"ok": True, "ignored": action or "no action"}

	shipment = _find_shipment(parcel_id, tracking)
	if not shipment:
		# Eşleşmeyen webhook nadir olmalı — görünür olsun diye Error Log'a yaz.
		frappe.log_error(
			title="SendCloud webhook: shipment not matched",
			message=f"parcel={parcel_id} tracking={tracking} status={status!r}",
		)
		return {"ok": True, "ignored": "shipment not found", "parcel_id": parcel_id}

	# Ağır işi arka plana at; webhook'a hemen 200 dön.
	frappe.enqueue(
		"erpnext_shipping.erpnext_shipping.webhook.refresh_shipment_tracking",
		queue="short",
		shipment=shipment,
		enqueue_after_commit=True,
	)
	return {"ok": True, "shipment": shipment}


def _log(msg):
	try:
		logger = frappe.logger("sendcloud_webhook", allow_site=True)
		logger.setLevel(logging.INFO)
		logger.info(msg)
	except Exception:
		pass


def _signature_result(body):
	"""None = no secret configured (verification off); True/False = HMAC-SHA256 match.
	SendCloud signs the raw body with the integration secret."""
	secret = frappe.db.get_single_value("SendCloud", "webhook_secret")
	if not secret:
		return None
	received = frappe.get_request_header("Sendcloud-Signature") or ""
	expected = hmac.new(secret.encode("utf-8"), body or b"", hashlib.sha256).hexdigest()
	return hmac.compare_digest(received, expected)


def _find_shipment(parcel_id, tracking_number=None):
	"""Locate the Shipment carrying this SendCloud parcel: shipment_id is a
	comma-joined list of parcel ids; fall back to the tracking number."""
	if parcel_id:
		for r in frappe.db.sql(
			"""select name, shipment_id from `tabShipment`
			   where shipment_id like %(p)s order by modified desc limit 25""",
			{"p": f"%{parcel_id}%"},
			as_dict=True,
		):
			ids = [x.strip() for x in (r.shipment_id or "").split(",")]
			if parcel_id in ids:
				return r.name
	if tracking_number:
		name = frappe.db.get_value(
			"Shipment", {"awb_number": ["like", f"%{tracking_number}%"]}, "name"
		)
		if name:
			return name
	return None


def refresh_shipment_tracking(shipment):
	"""Background job: refresh one Shipment's tracking via the standard flow."""
	from erpnext_shipping.erpnext_shipping.shipping import update_tracking

	doc = frappe.get_doc("Shipment", shipment)
	if not doc.get("shipment_id"):
		return
	delivery_notes = [
		row.delivery_note for row in (doc.get("shipment_delivery_note") or []) if row.delivery_note
	]
	update_tracking(shipment, doc.service_provider, doc.shipment_id, delivery_notes)
