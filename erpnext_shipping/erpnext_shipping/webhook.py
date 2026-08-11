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
from frappe.utils import now_datetime


@frappe.whitelist(allow_guest=True)
def sendcloud_webhook():
	body = frappe.request.get_data() if frappe.request else b""
	secret = _signing_secret()
	sig_valid = _hmac_match(secret, body) if secret else None

	try:
		payload = json.loads(body or b"{}")
	except Exception:
		payload = {}

	action = payload.get("action")
	parcel = payload.get("parcel") or {}
	parcel_id = str(parcel.get("id") or "")
	tracking = parcel.get("tracking_number")
	status = (parcel.get("status") or {}).get("message")

	# Sessiz denetim logu. sig: imza eşleşmesi (None = secret yok).
	_log(f"action={action!r} parcel={parcel_id} status={status!r} tracking={tracking} sig={sig_valid}")

	# Gerçek durum-değişikliği eventleri imzalı olmalı (sahte webhook koruması).
	# Bağlantı testi vb. (imzasız) aksiyonlar geçer.
	if action == "parcel_status_changed" and secret and not sig_valid:
		frappe.local.response["http_status_code"] = 401
		return {"ok": False, "error": "invalid signature"}

	# Bağlantı testi / diğer aksiyonlar: 200 dön, işlem yok.
	if action != "parcel_status_changed":
		return {"ok": True, "ignored": action or "no action"}

	shipment = _find_shipment(parcel_id, tracking)
	if not shipment:
		# Yarış durumu: SendCloud ilk event'i ("Being announced") paketi yaratır
		# yaratmaz atıyor, bizim booking işlemimiz ise shipment_id'yi henüz
		# commit etmemiş oluyor. Saniyeler sonra eşleşme tutuyor — o yüzden
		# hemen Error Log'a yazmak yerine arka planda tekrar deniyoruz.
		frappe.enqueue(
			"erpnext_shipping.erpnext_shipping.webhook.rematch_parcel",
			queue="short",
			timeout=120,
			parcel_id=parcel_id,
			tracking=tracking,
			status=status,
			job_id=f"sendcloud_rematch::{parcel_id}",
			deduplicate=True,
			enqueue_after_commit=True,
		)
		return {"ok": True, "deferred": "shipment not found yet", "parcel_id": parcel_id}

	# Durumu kaydet, sonra tazele. Bu satır olmadan webhook yalnız "bir şey
	# değişti" diyordu ve kod gidip O ANKİ durumu okuyordu — sıra kayboluyordu.
	# Taşıyıcı bize geri dönen paketi de "Delivered" diye kapattığı için, iadeyi
	# satıştan ayıran tek şey daha önce gelmiş "Refused" olayı.
	record_status(shipment, parcel_id, tracking, status)

	# Ağır işi arka plana at; webhook'a hemen 200 dön.
	frappe.enqueue(
		"erpnext_shipping.erpnext_shipping.webhook.refresh_shipment_tracking",
		queue="short",
		shipment=shipment,
		enqueue_after_commit=True,
	)
	return {"ok": True, "shipment": shipment}


# Kayıt sınırı: bir gönderi normalde 5-15 olay üretir. Üst sınır, döngüye giren
# bir taşıyıcı bildiriminin alanı şişirmesini engeller.
MAX_STATUS_HISTORY = 60


def record_status(shipment, parcel_id, tracking, status):
	"""Append one carrier status to the shipment's history.

	Kept as raw text: the point is to preserve what the carrier actually said,
	including wording we do not map today.
	"""
	if not status:
		return
	try:
		raw = frappe.db.get_value("Shipment", shipment, "custom_status_history")
		history = json.loads(raw) if raw else []
	except Exception:
		history = []

	# Aynı durum art arda tekrar ederse yazma; taşıyıcılar aynı olayı birkaç kez
	# gönderebiliyor ve tekrarlar sırayı okunmaz hale getiriyor.
	if history and history[-1].get("status") == status and str(history[-1].get("parcel_id")) == str(parcel_id):
		return

	history.append({
		"parcel_id": str(parcel_id or ""),
		"tracking": tracking or "",
		"status": status,
		"at": now_datetime().strftime("%Y-%m-%d %H:%M:%S"),
	})
	frappe.db.set_value(
		"Shipment", shipment, "custom_status_history",
		json.dumps(history[-MAX_STATUS_HISTORY:]),
		update_modified=False,
	)


def _log(msg):
	try:
		logger = frappe.logger("sendcloud_webhook", allow_site=True)
		logger.setLevel(logging.INFO)
		logger.info(msg)
	except Exception:
		pass


def _signing_secret():
	"""Secret SendCloud signs webhooks with: the explicit Webhook Secret if set,
	otherwise the API secret (SendCloud signs with it — confirmed via sig_api)."""
	secret = frappe.db.get_single_value("SendCloud", "webhook_secret")
	if secret:
		return secret
	try:
		from frappe.utils.password import get_decrypted_password

		return get_decrypted_password("SendCloud", "SendCloud", "api_secret", raise_exception=False)
	except Exception:
		return None


def _hmac_match(secret, body):
	"""True when the Sendcloud-Signature header matches HMAC-SHA256(secret, body)."""
	if not secret:
		return False
	received = frappe.get_request_header("Sendcloud-Signature") or ""
	if not received:
		return False
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


# Eşleşmeyi kaç kez, kaç saniye arayla tekrar deneyeceğiz. Gözlenen gecikme
# birkaç saniye; 5 x 5sn hem fazlasıyla yeter hem de worker'ı boşuna tutmaz.
_REMATCH_ATTEMPTS = 5
_REMATCH_INTERVAL = 5


def rematch_parcel(parcel_id, tracking=None, status=None):
	"""Arka plan: booking işlemi commit olana kadar eşleşmeyi tekrar dene.

	Eşleşirse normal tracking yenilemesi çalışır. Tüm denemeler biterse artık
	gerçek bir sorun vardır (bize ait olmayan paket, silinmiş Shipment) —
	ancak o zaman Error Log'a yazılır.
	"""
	import time

	for attempt in range(1, _REMATCH_ATTEMPTS + 1):
		time.sleep(_REMATCH_INTERVAL)
		# Commit'i başka bir bağlantı yaptı; okuduğumuz snapshot'ı tazele.
		frappe.db.rollback()
		shipment = _find_shipment(parcel_id, tracking)
		if shipment:
			_log(f"rematch OK parcel={parcel_id} -> {shipment} (deneme {attempt})")
			refresh_shipment_tracking(shipment)
			return shipment

	frappe.log_error(
		title="SendCloud webhook: shipment not matched",
		message=(
			f"parcel={parcel_id} tracking={tracking} status={status!r}\n"
			f"{_REMATCH_ATTEMPTS} deneme x {_REMATCH_INTERVAL}sn sonra da eşleşmedi."
		),
	)
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
