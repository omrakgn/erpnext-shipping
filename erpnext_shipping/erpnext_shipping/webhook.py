# Copyright (c) 2026, Frappe Technologies and contributors
# For license information, please see license.txt
"""SendCloud webhook receiver.

Registered in the SendCloud panel as the integration webhook URL:
    https://<site>/api/method/erpnext_shipping.erpnext_shipping.webhook.sendcloud_webhook

SendCloud POSTs a JSON body (with an HMAC-SHA256 signature in the
`Sendcloud-Signature` header) whenever a parcel's status changes. We verify the
signature, match the parcel to a Shipment and enqueue a normal tracking refresh —
so the existing update_tracking logic (delivered_at, transit days, label-removed,
Delivery Note updates) runs event-driven instead of on the hourly poll.
"""
import hashlib
import hmac
import json

import frappe


@frappe.whitelist(allow_guest=True)
def sendcloud_webhook():
	# Ham gövde (imza doğrulaması ham byte üzerinden yapılır).
	body = frappe.request.get_data() if frappe.request else b""

	if not _verify_signature(body):
		frappe.local.response["http_status_code"] = 401
		return {"ok": False, "error": "invalid signature"}

	try:
		payload = json.loads(body or b"{}")
	except Exception:
		payload = {}

	action = payload.get("action")
	parcel = payload.get("parcel") or {}
	parcel_id = str(parcel.get("id") or "")
	status = (parcel.get("status") or {}).get("message")
	tracking = parcel.get("tracking_number")
	logger = frappe.logger("sendcloud_webhook", allow_site=True)

	# SendCloud bağlantı testi / diğer aksiyonlar (integration_connected, test vb.):
	# 200 dön, işlem yapma. Böylece panelde "Unable to connect" hatası çıkmaz.
	if action != "parcel_status_changed":
		logger.info(f"received action={action!r} (ignored)")
		return {"ok": True, "ignored": action or "no action"}

	shipment = _find_shipment(parcel_id, tracking)
	logger.info(f"parcel={parcel_id} status={status!r} tracking={tracking} matched={shipment}")

	if not shipment:
		return {"ok": True, "ignored": "shipment not found", "parcel_id": parcel_id}

	# Ağır işi (SendCloud'a tekrar sorup güncelleme) arka plana at; webhook'a hemen 200 dön.
	frappe.enqueue(
		"erpnext_shipping.erpnext_shipping.webhook.refresh_shipment_tracking",
		queue="short",
		shipment=shipment,
		enqueue_after_commit=True,
	)
	return {"ok": True, "shipment": shipment}


def _verify_signature(body):
	"""True when the signature matches, or when no secret is configured (verification
	is then skipped). SendCloud signs the raw body with HMAC-SHA256 (hex)."""
	secret = frappe.db.get_single_value("SendCloud", "webhook_secret")
	if not secret:
		return True
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
