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
	(all carriers). Items come from the shipments' linked Delivery Notes.

	Product Bundles are counted by their components. A bundle appears in the
	Delivery Note as a single line whose quantity is the number of bundles, and
	its real contents live in Packed Item — so counting the Delivery Note lines
	alone reported "1" for a box that is picked as several separate products.
	The picker works from this list, so the components have to be in it.
	"""
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

	totals = {}

	def add(item_code, item_name, qty):
		row = totals.get(item_code)
		if not row:
			row = {"item_code": item_code, "item_name": item_name, "qty": 0}
			totals[item_code] = row
		elif not row["item_name"]:
			row["item_name"] = item_name
		row["qty"] += flt(qty)

	# Bundle bileşenleri. Packed Item.qty zaten toplam adettir (bundle adedi ×
	# bundle içindeki adet), ayrıca çarpmak gerekmiyor.
	bundle_parents = {}
	for p in frappe.get_all(
		"Packed Item",
		filters={"parent": ["in", dn_names], "parenttype": "Delivery Note"},
		fields=["parent", "parent_item", "item_code", "item_name", "qty"],
	):
		if not p.item_code:
			continue
		bundle_parents.setdefault(p.parent, set()).add(p.parent_item)
		add(p.item_code, p.item_name, p.qty)

	for it in frappe.get_all(
		"Delivery Note Item",
		filters={"parent": ["in", dn_names]},
		fields=["parent", "item_code", "item_name", "qty"],
	):
		# Bundle'ın kendisi sayılmaz — bileşenleri yukarıda sayıldı. Aynı ürün
		# ayrıca tek başına da satılmışsa o satır normal şekilde eklenir.
		if not it.item_code or it.item_code in bundle_parents.get(it.parent, ()):
			continue
		add(it.item_code, it.item_name, it.qty)

	rows = list(totals.values())
	rows.sort(key=lambda r: (-r["qty"], r["item_code"]))
	return rows


@frappe.whitelist()
def get_pickup_items(pickup_date):
	"""Aggregated item rows for the Generate button (client fills the table)."""
	return _aggregate_for_date(pickup_date)


@frappe.whitelist()
def create_for_date(pickup_date, company=None):
	"""Create the Pickup Item Summary for a date, or refresh the existing one.

	Refreshing rather than returning the existing document as-is: shipments keep
	being booked through the day, and a summary generated in the morning would
	otherwise send the picker out with a list that silently misses them.

	Returns {name, total_items, total_qty, refreshed}.
	"""
	rows = _aggregate_for_date(pickup_date)
	if not rows:
		frappe.throw(_("No shipment items found for {0}.").format(pickup_date))

	existing = frappe.db.get_value("Pickup Item Summary", {"pickup_date": pickup_date}, "name")
	if existing:
		doc = frappe.get_doc("Pickup Item Summary", existing)
		doc.set("items", [])
	else:
		doc = frappe.new_doc("Pickup Item Summary")
		doc.pickup_date = pickup_date
		doc.company = company or frappe.defaults.get_user_default("Company")

	for row in rows:
		doc.append("items", row)
	doc.save(ignore_permissions=True)

	return {
		"name": doc.name,
		"total_items": doc.total_items,
		"total_qty": doc.total_qty,
		"refreshed": bool(existing),
	}
