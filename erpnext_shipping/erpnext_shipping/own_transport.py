# Copyright (c) 2026, Frappe and contributors
# For license information, please see license.txt
"""Shipments that go without a carrier label: our own vehicle, or a hired
transport company. docs/plans/ozel-tasima.md

There is no tracking feed, so nobody reports the delivery. Without one, every
reader of the delivery date says "unknown": the customer's return period
(`multichannel_core` `delivery_facts`), Shopify's "Delivered" status, the
follow-up of the order. `mark_delivered` writes the delivery by hand, from the
signed copy of the Delivery Pickup Note, along the same path the carrier feed
uses (`custom_delivered_at`, `announce_delivered`), so those readers need no
change.

Such a shipment keeps `awb_number` and `shipment_id` empty. The delay, loss,
SLA and tracking jobs all skip shipments without them; a transport company's
reference goes to the delivery note's "Transport Receipt No" (`lr_no`), never to
`awb_number`, or the shipment would be flagged delayed and then lost.
"""

import frappe
from frappe import _
from frappe.utils import cstr, date_diff, get_datetime, getdate, now_datetime

OWN_VEHICLE = "Own Vehicle"
TRANSPORT_COMPANY = "Transport Company"
OWN_TRANSPORT_MODES = (OWN_VEHICLE, TRANSPORT_COMPANY)


def is_own_transport(doc):
	return (doc.get("shipping_transport_mode") or "") in OWN_TRANSPORT_MODES


def set_own_transport_carrier(doc, method=None):
	"""Shipment validate hook: name the carrier when nobody else will.

	A carrier label writes `carrier`; here no label is made. The transport
	company's name comes from the delivery note's Transporter Info, else the
	mode itself. Reports group by carrier, and Bol maps an unknown carrier to
	OTHER, which is what an own delivery is.
	"""
	if not is_own_transport(doc) or doc.get("carrier"):
		return
	ad = None
	if doc.shipping_transport_mode == TRANSPORT_COMPANY:
		for satir in doc.get("shipment_delivery_note") or []:
			ad = frappe.db.get_value("Delivery Note", satir.delivery_note, "transporter_name")
			if ad:
				break
	doc.carrier = ad or doc.shipping_transport_mode


@frappe.whitelist()
def mark_delivered(shipment, delivered_at, received_by, proof=None, remarks=None):
	"""Record the delivery of an own-transport shipment from its signed copy."""
	doc = frappe.get_doc("Shipment", shipment)
	doc.check_permission("write")

	if doc.docstatus != 1:
		frappe.throw(_("Submit the shipment first."))
	if not is_own_transport(doc):
		frappe.throw(_("This shipment goes with a carrier. Its delivery comes from the carrier's tracking."))
	if doc.get("shipment_id") or doc.get("awb_number"):
		frappe.throw(
			_(
				"This shipment has a carrier label ({0}). Its delivery comes from the carrier's tracking."
			).format(doc.get("awb_number") or doc.get("shipment_id"))
		)
	if doc.get("custom_delivered_at"):
		frappe.throw(_("Already marked as delivered on {0}.").format(doc.custom_delivered_at))
	if doc.get("custom_returned_to_sender"):
		frappe.throw(_("This shipment is marked as returned to us."))

	received_by = cstr(received_by).strip()
	if not received_by:
		frappe.throw(_("Enter the name of the person who received the goods."))
	teslim = get_datetime(delivered_at)
	if teslim > now_datetime():
		frappe.throw(_("The delivery time cannot be in the future."))
	if doc.get("pickup_date") and teslim.date() < getdate(doc.pickup_date):
		frappe.throw(_("The delivery cannot be before the pickup date {0}.").format(doc.pickup_date))

	degerler = {
		"tracking_status": "Delivered",
		"custom_delivered_at": teslim,
		"shipping_received_by": received_by,
	}
	# Taşıyıcı akışındaki kuralla aynı: 0-90 gün dışı bozuk veri, yazılmaz.
	if doc.get("pickup_date"):
		gun = date_diff(teslim.date(), doc.pickup_date)
		if gun is not None and 0 <= gun <= 90:
			degerler["custom_transit_days"] = gun
	if proof:
		degerler["shipping_delivery_proof"] = proof

	onceki = doc.get("tracking_status")
	doc.db_set(degerler)

	# ERPNext'in "Create > Shipment"i irsaliyenin her ürün satırı için bir satır yazıyor:
	# iki ürünlü irsaliye tabloda iki kez. Sırayı koruyarak tekilleştir.
	irsaliyeler = list(
		dict.fromkeys(r.delivery_note for r in doc.get("shipment_delivery_note") or [] if r.delivery_note)
	)
	if proof:
		_attach_proof(proof, doc.name, irsaliyeler)

	not_metni = _("Delivered on {0}, received by {1}. Marked by hand from the signed delivery note.").format(
		teslim, received_by
	)
	if not proof:
		not_metni += " " + _("No signed copy attached.")
	if cstr(remarks).strip():
		not_metni += " " + _("Remarks: {0}").format(cstr(remarks).strip())
	doc.add_comment("Info", not_metni)
	for dn in irsaliyeler:
		frappe.get_doc("Delivery Note", dn).add_comment("Info", not_metni)

	# Taşıyıcı akışıyla aynı duyuru: Shopify'a "Delivered", sipariş takibi.
	if onceki != "Delivered":
		from erpnext_shipping.erpnext_shipping.shipping import announce_delivered

		announce_delivered(doc.name)
	return {"delivered_at": teslim, "received_by": received_by}


def _attach_proof(file_url, shipment, delivery_notes):
	"""The signed copy, on the shipment and on each of its delivery notes.

	The dialog uploads it unattached. The upload itself is moved to the
	shipment; each delivery note gets a File record pointing at the same file,
	so it shows under the note's attachments without a second copy on disk.
	"""
	yukleme = frappe.db.get_value(
		"File",
		{"file_url": file_url, "attached_to_name": ["is", "not set"]},
		["name", "is_private", "file_name"],
		as_dict=True,
	)
	gizli = 1
	ad = file_url.rsplit("/", 1)[-1]
	if yukleme:
		gizli, ad = yukleme.is_private, yukleme.file_name or ad
		frappe.db.set_value(
			"File", yukleme.name, {"attached_to_doctype": "Shipment", "attached_to_name": shipment}
		)
	elif not frappe.db.exists(
		"File", {"file_url": file_url, "attached_to_doctype": "Shipment", "attached_to_name": shipment}
	):
		_file(file_url, ad, gizli, "Shipment", shipment)
	for dn in delivery_notes:
		if not frappe.db.exists(
			"File", {"file_url": file_url, "attached_to_doctype": "Delivery Note", "attached_to_name": dn}
		):
			_file(file_url, ad, gizli, "Delivery Note", dn)


def _file(file_url, file_name, is_private, doctype, name):
	frappe.get_doc(
		{
			"doctype": "File",
			"file_url": file_url,
			"file_name": file_name,
			"is_private": is_private,
			"attached_to_doctype": doctype,
			"attached_to_name": name,
		}
	).insert(ignore_permissions=True)
