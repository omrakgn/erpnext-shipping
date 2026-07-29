# Copyright (c) 2026, Frappe Technologies and contributors
# For license information, please see license.txt
"""Recompute the Shipment cost rollup for every shipment that has cost entries, so
the newly-added cost-variance / customer-charge / margin fields get populated.

Needed because backfill_surcharge_fields already ran (and was logged) BEFORE these
fields existed, so it computed surcharges but not variance/margin."""
import frappe

from erpnext_shipping.erpnext_shipping.shipping_cost import recompute_shipment_cost


def execute():
	shipments = frappe.db.sql_list(
		"select distinct shipment from `tabShipping Cost Entry` where ifnull(shipment, '') != ''"
	)
	for shipment in shipments:
		try:
			recompute_shipment_cost(shipment)
		except Exception:
			frappe.log_error(
				title="Backfill variance/margin failed",
				message=f"Shipment: {shipment}\n{frappe.get_traceback()}",
			)
	frappe.db.commit()
