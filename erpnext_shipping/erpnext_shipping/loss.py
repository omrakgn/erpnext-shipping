# Copyright (c) 2026, Frappe Technologies and contributors
# For license information, please see license.txt
"""Presumed-lost shipment detection.

Carriers do not reliably mark parcels as "Lost", so we flag shipments that stay
undelivered beyond a (configurable) threshold as PRESUMED LOST — surfaced in a
report/card for a human to decide whether to open a Shipment Loss Claim.
"""
import frappe
from frappe.utils import add_days, cint, nowdate

DEFAULT_PRESUMED_LOST_DAYS = 20


def presumed_lost_days():
	return cint(frappe.db.get_single_value("Shipment Settings", "presumed_lost_days")) or DEFAULT_PRESUMED_LOST_DAYS


def _presumed_lost_names(min_days):
	cutoff = add_days(nowdate(), -abs(min_days))
	return set(
		frappe.db.sql_list(
			"""
			select name from `tabShipment`
			where docstatus < 2
				and ifnull(tracking_status, '') != 'Delivered'
				and custom_delivered_at is null
				and pickup_date is not null
				and pickup_date <= %s
				and ifnull(status, '') not in ('Cancelled', 'Completed')
				and ifnull(awb_number, '') != ''
			""",
			cutoff,
		)
	)


def flag_presumed_lost():
	"""Daily job: keep the custom_presumed_lost flag in sync with the threshold."""
	names = _presumed_lost_names(presumed_lost_days())
	for name in names:
		if not frappe.db.get_value("Shipment", name, "custom_presumed_lost"):
			frappe.db.set_value("Shipment", name, "custom_presumed_lost", 1, update_modified=False)
	for name in frappe.get_all("Shipment", filters={"custom_presumed_lost": 1}, pluck="name"):
		if name not in names:
			frappe.db.set_value("Shipment", name, "custom_presumed_lost", 0, update_modified=False)
	frappe.db.commit()
