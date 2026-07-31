# Copyright (c) 2026, Frappe Technologies and contributors
# For license information, please see license.txt
"""Retired. The DPD 'Declaration of Non-Receipt' is no longer rendered from a
Jinja print format — it is stamped onto the ORIGINAL DPD PDF (see
erpnext_shipping/loss_form.py) so the layout is pixel-identical to the carrier's
form. This patch now removes the obsolete print format on migrate."""
import frappe

PRINT_FORMAT_NAME = "DPD Declaration of Non-Receipt"


def execute():
	if frappe.db.exists("Print Format", PRINT_FORMAT_NAME):
		frappe.delete_doc("Print Format", PRINT_FORMAT_NAME, ignore_permissions=True, force=True)
