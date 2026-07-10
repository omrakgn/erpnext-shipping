# Copyright (c) 2026, Frappe Technologies and contributors
# For license information, please see license.txt
import json

import frappe

FIELDS = ("content", "icon", "label", "title", "public", "sequence_id")
CHILD_TABLES = ("links", "shortcuts", "charts", "number_cards")


def execute():
	"""Create or update the standard "Shipping" Workspace from shipping.json so the
	links, shortcuts, number cards and chart stay in sync on migrate."""
	path = frappe.get_app_path(
		"erpnext_shipping", "erpnext_shipping", "workspace", "shipping", "shipping.json"
	)
	try:
		with open(path, encoding="utf-8") as f:
			data = json.load(f)
	except FileNotFoundError:
		return

	if frappe.db.exists("Workspace", "Shipping"):
		doc = frappe.get_doc("Workspace", "Shipping")
		for field in FIELDS:
			doc.set(field, data.get(field))
		for table in CHILD_TABLES:
			doc.set(table, [])
			for row in data.get(table) or []:
				doc.append(table, row)
		doc.flags.ignore_permissions = True
		doc.save()
	else:
		doc = frappe.get_doc(data)
		doc.flags.ignore_permissions = True
		doc.insert(ignore_if_duplicate=True)
