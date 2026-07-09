// Copyright (c) 2026, Frappe and contributors
// For license information, please see license.txt

frappe.query_reports["Shipping Costs"] = {
	filters: [
		{
			fieldname: "from_date",
			label: __("From Date"),
			fieldtype: "Date",
		},
		{
			fieldname: "to_date",
			label: __("To Date"),
			fieldtype: "Date",
		},
		{
			fieldname: "group_by",
			label: __("Group By"),
			fieldtype: "Select",
			options: ["Parcel", "Shipment", "Delivery Note", "Invoice"],
			default: "Parcel",
			reqd: 1,
		},
		{
			fieldname: "invoice_number",
			label: __("Invoice Number"),
			fieldtype: "Data",
		},
		{
			fieldname: "carrier",
			label: __("Carrier"),
			fieldtype: "Data",
			default: "DPD",
		},
		{
			fieldname: "product_name",
			label: __("Product / Service"),
			fieldtype: "Data",
		},
		{
			fieldname: "shipment",
			label: __("Shipment"),
			fieldtype: "Link",
			options: "Shipment",
		},
		{
			fieldname: "matched",
			label: __("Matched"),
			fieldtype: "Select",
			options: ["", "1", "0"],
		},
		{
			fieldname: "match_method",
			label: __("Match Method"),
			fieldtype: "Select",
			options: ["", "Tracking", "Order Number", "Manual"],
		},
		{
			fieldname: "only_corrections",
			label: __("Only Corrections"),
			fieldtype: "Check",
		},
	],
};
