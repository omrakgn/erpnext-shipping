# Copyright (c) 2026, Frappe Technologies and contributors
# For license information, please see license.txt
import json

import frappe

NUMBER_CARDS = [
	{
		"name": "Shipping Cost This Month",
		"label": "Shipping Cost This Month",
		"document_type": "Shipping Cost Entry",
		"function": "Sum",
		"aggregate_function_based_on": "total_net_amount",
		"filters_json": json.dumps(
			[["Shipping Cost Entry", "scan_date", "Timespan", "this month", False]]
		),
		"color": "#29CD42",
	},
	{
		"name": "Unmatched Cost Entries",
		"label": "Unmatched Cost Entries",
		"document_type": "Shipping Cost Entry",
		"function": "Count",
		"filters_json": json.dumps([["Shipping Cost Entry", "matched", "=", 0, False]]),
		"color": "#CB2929",
	},
	{
		"name": "Labels Removed",
		"label": "Labels Removed",
		"document_type": "Shipment",
		"function": "Count",
		"filters_json": json.dumps([["Shipment", "custom_label_removed", "=", 1, False]]),
		"color": "#FFC733",
	},
]

DASHBOARD_CHART = {
	"name": "Monthly Shipping Cost",
	"chart_name": "Monthly Shipping Cost",
	"chart_type": "Sum",
	"document_type": "Shipping Cost Entry",
	"based_on": "scan_date",
	"value_based_on": "total_net_amount",
	"timeseries": 1,
	"timespan": "Last Year",
	"time_interval": "Monthly",
	"type": "Line",
	"filters_json": "[]",
}


def execute():
	for card in NUMBER_CARDS:
		if frappe.db.exists("Number Card", card["name"]):
			continue
		if not frappe.db.exists("DocType", card["document_type"]):
			continue
		doc = frappe.get_doc(
			{
				"doctype": "Number Card",
				"type": "Document Type",
				"is_public": 1,
				"show_percentage_stats": 1,
				"stats_time_interval": "Monthly",
				**card,
			}
		)
		doc.flags.ignore_permissions = True
		doc.insert(ignore_if_duplicate=True)

	if not frappe.db.exists("Dashboard Chart", DASHBOARD_CHART["name"]) and frappe.db.exists(
		"DocType", DASHBOARD_CHART["document_type"]
	):
		doc = frappe.get_doc({"doctype": "Dashboard Chart", "is_public": 1, **DASHBOARD_CHART})
		doc.flags.ignore_permissions = True
		doc.insert(ignore_if_duplicate=True)
