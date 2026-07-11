# Copyright (c) 2026, Frappe Technologies and contributors
# For license information, please see license.txt
import json

import frappe

# Frappe list/report filters are [doctype, fieldname, operator, value] (4 elements).
NUMBER_CARDS = [
	{
		"name": "Shipping Cost Last Month",
		"label": "Shipping Cost Last Month",
		"document_type": "Shipping Cost Entry",
		"function": "Sum",
		"aggregate_function_based_on": "total_net_amount",
		"filters_json": json.dumps(
			[["Shipping Cost Entry", "scan_date", "Timespan", "last month"]]
		),
		"color": "#29CD42",
	},
	{
		"name": "Unmatched Cost Entries",
		"label": "Unmatched Cost Entries",
		"document_type": "Shipping Cost Entry",
		"function": "Count",
		"aggregate_function_based_on": "",
		"filters_json": json.dumps([["Shipping Cost Entry", "matched", "=", 0]]),
		"color": "#CB2929",
	},
	{
		"name": "Labels Removed",
		"label": "Labels Removed",
		"document_type": "Shipment",
		"function": "Count",
		"aggregate_function_based_on": "",
		"filters_json": json.dumps([["Shipment", "custom_label_removed", "=", 1]]),
		"color": "#FFC733",
	},
]

# Cards from earlier versions to remove (renamed / replaced).
LEGACY_CARDS = ["Shipping Cost This Month"]

DASHBOARD_CHARTS = [
	{
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
	},
	{
		"name": "Shipping Cost by Carrier",
		"chart_name": "Shipping Cost by Carrier",
		"chart_type": "Group By",
		"document_type": "Shipping Cost Entry",
		"group_by_based_on": "carrier",
		"group_by_type": "Sum",
		"aggregate_function_based_on": "total_net_amount",
		"number_of_groups": 0,
		"timeseries": 0,
		"type": "Bar",
		"filters_json": "[]",
	},
	{
		"name": "Shipping Cost by Country",
		"chart_name": "Shipping Cost by Country",
		"chart_type": "Group By",
		"document_type": "Shipping Cost Entry",
		"group_by_based_on": "country",
		"group_by_type": "Sum",
		"aggregate_function_based_on": "total_net_amount",
		"number_of_groups": 10,
		"timeseries": 0,
		"type": "Bar",
		"filters_json": "[]",
	},
]


def execute():
	# force=True so a card still referenced by the workspace can be removed here
	# (the workspace patch runs afterwards and re-points to the new cards).
	for name in LEGACY_CARDS:
		if frappe.db.exists("Number Card", name):
			frappe.delete_doc("Number Card", name, force=True, ignore_permissions=True)

	for card in NUMBER_CARDS:
		if not frappe.db.exists("DocType", card["document_type"]):
			continue
		if frappe.db.exists("Number Card", card["name"]):
			doc = frappe.get_doc("Number Card", card["name"])
			doc.update({k: v for k, v in card.items() if k != "name"})
			doc.flags.ignore_permissions = True
			doc.save()
		else:
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

	for chart in DASHBOARD_CHARTS:
		if not frappe.db.exists("DocType", chart["document_type"]):
			continue
		if frappe.db.exists("Dashboard Chart", chart["name"]):
			doc = frappe.get_doc("Dashboard Chart", chart["name"])
			doc.update({k: v for k, v in chart.items() if k != "name"})
			doc.flags.ignore_permissions = True
			doc.save()
		else:
			doc = frappe.get_doc({"doctype": "Dashboard Chart", "is_public": 1, **chart})
			doc.flags.ignore_permissions = True
			doc.insert(ignore_if_duplicate=True)
