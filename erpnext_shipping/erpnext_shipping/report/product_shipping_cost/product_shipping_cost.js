// Copyright (c) 2026, Frappe and contributors
// For license information, please see license.txt

frappe.query_reports["Product Shipping Cost"] = {
	filters: [
		{
			fieldname: "from_date",
			label: __("From Date"),
			fieldtype: "Date",
			default: frappe.datetime.add_months(frappe.datetime.get_today(), -3),
			reqd: 1,
		},
		{
			fieldname: "to_date",
			label: __("To Date"),
			fieldtype: "Date",
			default: frappe.datetime.get_today(),
			reqd: 1,
		},
		{
			fieldname: "item_code",
			label: __("Item"),
			fieldtype: "Link",
			options: "Item",
		},
		{
			fieldname: "sales_channel",
			label: __("Sales Channel"),
			fieldtype: "Select",
			options: ["", "Amazon", "Bol.com", "Shopify", "Other"],
		},
		{
			fieldname: "billed_via",
			label: __("Billed By"),
			fieldtype: "Select",
			options: ["", "DPD", "SendCloud", "FedEx"],
		},
		{
			fieldname: "carrier",
			label: __("Carrier"),
			fieldtype: "Data",
		},
	],
};
