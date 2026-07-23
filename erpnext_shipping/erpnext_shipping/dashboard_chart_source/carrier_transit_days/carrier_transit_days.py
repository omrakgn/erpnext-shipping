# Copyright (c) 2026, Frappe and contributors
# For license information, please see license.txt
import re

import frappe
from frappe import _

from erpnext_shipping.erpnext_shipping.report.carrier_delivery_performance.carrier_delivery_performance import (
	MAX_TRANSIT_DAYS,
	_canon,
)


@frappe.whitelist()
def get_data(chart_name=None, chart=None, filters=None, **kwargs):
	"""Average transit days per carrier, using the same normalised/capped logic as
	the Carrier Delivery Performance report (canonical carrier names, mixed-carrier
	shipments split, real deliveries only, 0..MAX days)."""
	rows = frappe.db.sql(
		"""
		select carrier, custom_transit_days as days
		from `tabShipment`
		where docstatus < 2 and custom_delivered_at is not null and ifnull(carrier, '') != ''
		""",
		as_dict=True,
	)

	agg = {}
	for r in rows:
		if r.days is None or r.days < 0 or r.days > MAX_TRANSIT_DAYS:
			continue
		for raw in re.split(r"[,;]", r.carrier or ""):
			carrier = _canon(raw)
			if carrier:
				agg.setdefault(carrier, []).append(r.days)

	items = sorted(agg.items(), key=lambda kv: len(kv[1]), reverse=True)
	labels = [c for c, _days in items]
	values = [round(sum(d) / len(d), 1) for _c, d in items]

	# Boş veri seti frappe-charts'ta NaN koordinat -> removeChild çökmesi -> tüm
	# workspace render'ı kırılır. En az bir nokta döndürerek bunu engelle.
	if not labels:
		labels = [_("No data")]
		values = [0]

	return {
		"labels": labels,
		"datasets": [{"name": "Avg Transit (days)", "values": values}],
	}
