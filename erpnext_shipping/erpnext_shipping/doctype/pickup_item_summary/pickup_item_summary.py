# Copyright (c) 2026, Frappe and contributors
# For license information, please see license.txt
import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import flt


class PickupItemSummary(Document):
	def validate(self):
		self.total_items = len(self.items)
		self.total_qty = sum(flt(r.qty) for r in self.items)


def _aggregate_for_date(pickup_date):
	"""Total quantity per item across every shipment on the pickup date
	(all carriers). Items come from the shipments' linked Delivery Notes."""
	if not pickup_date:
		return []
	shipments = frappe.get_all(
		"Shipment", filters={"pickup_date": pickup_date, "docstatus": ["<", 2]}, pluck="name"
	)
	if not shipments:
		return []
	dn_names = list(
		{
			d
			for d in frappe.get_all(
				"Shipment Delivery Note",
				filters={"parent": ["in", shipments], "delivery_note": ["is", "set"]},
				pluck="delivery_note",
			)
			if d
		}
	)
	if not dn_names:
		return []
	placeholders = ", ".join(["%s"] * len(dn_names))
	return frappe.db.sql(
		f"""
		select item_code, min(item_name) as item_name, sum(qty) as qty
		from `tabDelivery Note Item`
		where parent in ({placeholders}) and ifnull(item_code, '') != ''
		group by item_code
		order by qty desc, item_code
		""",
		tuple(dn_names),
		as_dict=True,
	)


@frappe.whitelist()
def get_pickup_items(pickup_date):
	"""Aggregated item rows for the Generate button (client fills the table)."""
	return _aggregate_for_date(pickup_date)


@frappe.whitelist()
def create_for_date(pickup_date, company=None):
	"""Create (or return the existing) Pickup Item Summary for a date, pre-filled."""
	existing = frappe.db.get_value("Pickup Item Summary", {"pickup_date": pickup_date}, "name")
	if existing:
		return existing
	doc = frappe.new_doc("Pickup Item Summary")
	doc.pickup_date = pickup_date
	doc.company = company or frappe.defaults.get_user_default("Company")
	for row in _aggregate_for_date(pickup_date):
		doc.append("items", row)
	if not doc.items:
		frappe.throw(_("No shipment items found for {0}.").format(pickup_date))
	doc.insert(ignore_permissions=True)
	return doc.name
