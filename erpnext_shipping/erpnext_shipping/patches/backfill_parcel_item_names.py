# Copyright (c) 2026, Frappe Technologies and contributors
# For license information, please see license.txt
"""Fill the item name on parcel item rows written without it.

`Shipment Parcel Item.item_name` is fetched from the item code, but Frappe fetches
link values before the validate hooks run, and `auto_populate_parcels` adds the
rows in a validate hook: 304 rows on 248 shipments had it empty (2026-10-17).
New rows get the name from `shipping._build_parcels`; this fills the old ones from
the item master. Only empty names are written, so running it again changes nothing.
"""

import frappe


def execute():
	frappe.db.sql(
		"""
		update `tabShipment Parcel Item` spi
		join `tabItem` i on i.name = spi.item_code
		set spi.item_name = i.item_name
		where ifnull(spi.item_name, '') = ''
		"""
	)
