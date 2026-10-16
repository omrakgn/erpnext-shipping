# Copyright (c) 2026, Frappe and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document


class ShippingCostEntry(Document):
	def validate(self):
		# Negatif tutar veya "4..." ile başlayan fatura = düzeltme/iade satırı.
		self.is_correction = 1 if (
			(self.invoice_number or "").startswith("4") or (self.total_net_amount or 0) < 0
		) else 0
		self.matched = 1 if (self.shipment or self.delivery_note) else 0
		if (self.shipment or self.delivery_note) and not self.match_method:
			# Elle bağlandıysa (import bir yöntem set etmediyse) Manual say.
			self.match_method = "Manual"
		if not (self.shipment or self.delivery_note):
			self.match_method = None
		self._fill_receiver()
		self._fill_contents()

	def _fill_receiver(self):
		from erpnext_shipping.erpnext_shipping.shipping_cost import fill_receiver_from_order

		old = self.get_doc_before_save()
		# Boş değer None ya da "" gelebilir; ikisi aynı bağ.
		link_changed = bool(old) and (
			(old.get("shipment") or None) != (self.shipment or None)
			or (old.get("delivery_note") or None) != (self.delivery_note or None)
		)
		fill_receiver_from_order(self, link_changed=link_changed)

	def _fill_contents(self):
		from erpnext_shipping.erpnext_shipping.shipping_cost import fill_cost_items

		fill_cost_items(self)

	def on_update(self):
		self._recompute_affected()

	def on_trash(self):
		# Kayıt silinince bağlı Shipment/DN toplamı da güncellensin.
		self._recompute_affected()

	def _recompute_affected(self):
		from erpnext_shipping.erpnext_shipping.shipping_cost import (
			recompute_delivery_note_cost,
			recompute_shipment_cost,
		)

		# Shipment/DN değiştiyse hem eskisini hem yenisini yeniden hesapla.
		shipments, dns = set(), set()
		if self.shipment:
			shipments.add(self.shipment)
		if self.delivery_note:
			dns.add(self.delivery_note)
		old = self.get_doc_before_save()
		if old:
			if old.get("shipment"):
				shipments.add(old.shipment)
			if old.get("delivery_note"):
				dns.add(old.delivery_note)
		for dn in dns:
			recompute_delivery_note_cost(dn)
		for name in shipments:
			recompute_shipment_cost(name)
