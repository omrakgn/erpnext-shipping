# Copyright (c) 2026, Frappe Technologies and contributors
# For license information, please see license.txt
import json

import frappe


def execute():
	"""Ensure the standard "Shipping" Workspace exists (in case the app-folder
	workspace sync did not create it). Reads the same shipping.json so the content
	stays single-sourced."""
	if frappe.db.exists("Workspace", "Shipping"):
		return

	path = frappe.get_app_path(
		"erpnext_shipping", "erpnext_shipping", "workspace", "shipping", "shipping.json"
	)
	try:
		with open(path, encoding="utf-8") as f:
			data = json.load(f)
	except FileNotFoundError:
		return

	doc = frappe.get_doc(data)
	doc.flags.ignore_permissions = True
	doc.insert(ignore_if_duplicate=True)
