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
		# Adet — para birimi gösterme.
		"currency": "",
	},
	{
		"name": "Labels Removed",
		"label": "Labels Removed",
		"document_type": "Shipment",
		"function": "Count",
		"aggregate_function_based_on": "",
		"filters_json": json.dumps([["Shipment", "custom_label_removed", "=", 1]]),
		"color": "#FFC733",
		"currency": "",
	},
	{
		"name": "Avg Delivery Time (Days)",
		"label": "Avg Delivery Time (Days)",
		"document_type": "Shipment",
		"function": "Average",
		"aggregate_function_based_on": "custom_transit_days",
		# Yalnızca gerçek teslimler (delivered_at dolu) ve makul aralık (0..90 gün);
		# custom_transit_days=0 varsayılanlarını ve bozuk outlier'ları dışla.
		"filters_json": json.dumps(
			[
				["Shipment", "custom_delivered_at", "is", "set"],
				["Shipment", "custom_transit_days", ">=", 0],
				["Shipment", "custom_transit_days", "<=", 90],
			]
		),
		"color": "#7575FF",
		# Gün — para birimi gösterme (Frappe aksi halde EUR ile biçimlendiriyor).
		"currency": "",
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
	{
		"name": "Avg Transit Days by Carrier",
		"chart_name": "Avg Transit Days by Carrier",
		"chart_type": "Group By",
		"document_type": "Shipment",
		"group_by_based_on": "carrier",
		"group_by_type": "Average",
		"aggregate_function_based_on": "custom_transit_days",
		"number_of_groups": 0,
		"timeseries": 0,
		"type": "Bar",
		"filters_json": json.dumps(
			[
				["Shipment", "custom_delivered_at", "is", "set"],
				["Shipment", "custom_transit_days", ">=", 0],
				["Shipment", "custom_transit_days", "<=", 90],
			]
		),
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

		# Frappe, sayısal kartlara varsayılan şirket para birimini (EUR) atayıp değeri
		# € ile gösteriyor. Adet/gün kartlarında bunu doğrudan DB'de boşalt (validate'i
		# atlar) ki günler/adetler düz sayı görünsün.
		if "currency" in card:
			frappe.db.set_value(
				"Number Card", card["name"], "currency", card["currency"], update_modified=False
			)

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

	# Transit (gün) grafiğinde para birimi biçimlendirmesini kapat.
	if frappe.db.has_column("Dashboard Chart", "currency") and frappe.db.exists(
		"Dashboard Chart", "Avg Transit Days by Carrier"
	):
		frappe.db.set_value(
			"Dashboard Chart", "Avg Transit Days by Carrier", "currency", "", update_modified=False
		)
