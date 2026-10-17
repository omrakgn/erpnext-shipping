# Copyright (c) 2026, Frappe and contributors
# For license information, please see license.txt

from frappe.model.document import Document

from erpnext_shipping.erpnext_shipping import dispute


class CarrierInvoiceDispute(Document):
	def onload(self):
		self.set_onload("states", dispute.document_states(self))

	def validate(self):
		dispute.validate_dispute(self)

	def on_update(self):
		dispute.sync_cost_entries(self)

	def on_trash(self):
		dispute.release_cost_entries(self)
