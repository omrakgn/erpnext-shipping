// Copyright (c) 2026, Frappe Technologies and contributors
// For license information, please see license.txt

frappe.query_reports["SLA Performance"] = {
	filters: [
		{ fieldname: "from_date", label: __("From Date"), fieldtype: "Date" },
		{ fieldname: "to_date", label: __("To Date"), fieldtype: "Date" },
		{
			fieldname: "channel",
			label: __("Sales Channel"),
			fieldtype: "Select",
			options: ["", "Amazon", "Bol.com", "Shopify", "Other"],
		},
		{ fieldname: "carrier", label: __("Carrier"), fieldtype: "Data" },
	],
};
