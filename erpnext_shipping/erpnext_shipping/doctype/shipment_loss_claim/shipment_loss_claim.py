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
		fill("sender_name", sh.get("pickup_company") or frappe.defaults.get_default("company"))
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
