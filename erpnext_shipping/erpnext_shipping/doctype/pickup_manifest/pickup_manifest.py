# Copyright (c) 2026, Frappe and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import flt


class PickupManifest(Document):
	def validate(self):
		packages = {row.package_no for row in self.items if row.package_no}
		if not packages:
			packages = {row.shipment for row in self.items if row.shipment}
		self.total_packages = len(packages)
		self.total_qty = sum(flt(row.qty) for row in self.items)


def _delivery_party_name(sh):
	"""Teslimat tarafının görünen adı (Şirket adı sütunu)."""
	if sh.get("delivery_customer"):
		return frappe.db.get_value("Customer", sh.delivery_customer, "customer_name") or sh.delivery_customer
	if sh.get("delivery_supplier"):
		return frappe.db.get_value("Supplier", sh.delivery_supplier, "supplier_name") or sh.delivery_supplier
	if sh.get("delivery_company"):
		return frappe.db.get_value("Company", sh.delivery_company, "company_name") or sh.delivery_company
	return sh.get("delivery_contact_name") or ""


def _shipment_items(sh):
	"""Gönderiye bağlı Delivery Note'lardan ürünleri (item_code ile birleştirerek) al."""
	dn_names = list({d.delivery_note for d in (sh.get("shipment_delivery_note") or []) if d.delivery_note})
	merged, order = {}, []
	for dn in dn_names:
		for it in frappe.get_all(
			"Delivery Note Item",
			filters={"parent": dn},
			fields=["item_code", "item_name", "qty"],
			order_by="idx",
		):
			key = it.item_code or it.item_name
			if key not in merged:
				merged[key] = {"item_name": it.item_name or it.item_code, "qty": 0}
				order.append(key)
			merged[key]["qty"] += flt(it.qty)
	if not merged:
		return [{"item_name": "", "qty": 0}]
	return [merged[k] for k in order]


@frappe.whitelist()
def generate_pickup_manifests(from_date, to_date, company=None):
	"""Pickup tarihi aralığındaki gönderileri carrier'a göre gruplayıp her carrier
	için ayrı bir Pickup Manifest (kurye teslim tutanağı) oluştur.

	Daha önce bir manifestoya eklenmiş gönderiler atlanır (tekrar önlenir).
	Returns: [{name, carrier, packages}]
	"""
	shipments = frappe.get_all(
		"Shipment",
		filters={
			"pickup_date": ["between", [from_date, to_date]],
			"docstatus": ["<", 2],
			"carrier": ["is", "set"],
		},
		fields=["name", "carrier"],
		order_by="carrier asc, pickup_date asc, name asc",
	)
	if not shipments:
		frappe.throw(_("No shipments with a carrier found for the selected pickup date range."))

	already = {
		r.shipment
		for r in frappe.get_all(
			"Pickup Manifest Item", filters={"shipment": ["is", "set"]}, fields=["shipment"]
		)
	}

	by_carrier = {}
	for sh in shipments:
		if sh.name in already:
			continue
		by_carrier.setdefault(sh.carrier, []).append(sh.name)

	if not by_carrier:
		frappe.throw(_("All shipments in this range are already in a pickup manifest."))

	if not company:
		company = frappe.defaults.get_user_default("Company") or frappe.db.get_single_value(
			"Global Defaults", "default_company"
		)

	created = []
	for carrier, names in by_carrier.items():
		manifest = frappe.new_doc("Pickup Manifest")
		manifest.from_date = from_date
		manifest.to_date = to_date
		manifest.carrier = carrier
		manifest.company = company

		package_no = 0
		for name in names:
			package_no += 1
			sh = frappe.get_doc("Shipment", name)
			company_name = _delivery_party_name(sh)
			contact_person = sh.get("delivery_contact_name") or ""
			for item in _shipment_items(sh):
				manifest.append(
					"items",
					{
						"package_no": package_no,
						"shipment": name,
						"company_name": company_name,
						"contact_person": contact_person,
						"carrier": carrier,
						"item_name": item["item_name"],
						"qty": item["qty"],
					},
				)
		manifest.insert(ignore_permissions=True)
		created.append({"name": manifest.name, "carrier": carrier, "packages": package_no})

	return created
