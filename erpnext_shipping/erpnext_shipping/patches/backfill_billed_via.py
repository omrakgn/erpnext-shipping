# Copyright (c) 2026, Frappe Technologies and contributors
# For license information, please see license.txt
"""Fill the billing party on Shipping Cost Entries imported before it was recorded.

`billed_via` decides who a dispute goes to (docs/plans/kargo-maliyet-ekrani-ve-itiraz.md
§4.6). It was added to the DPD and FedEx parsers after their first imports, so 1,112
DPD entries had it empty (2026-10-16). Those invoices come from the carrier itself:
the billing party is the carrier. SendCloud's parser has always set it.
"""

import frappe


def execute():
	frappe.db.sql(
		"""
		update `tabShipping Cost Entry`
		set billed_via = carrier
		where ifnull(billed_via, '') = '' and ifnull(carrier, '') != ''
		"""
	)
