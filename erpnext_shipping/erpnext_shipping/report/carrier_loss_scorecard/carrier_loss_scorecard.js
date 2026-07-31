// Copyright (c) 2026, Frappe Technologies and contributors
// For license information, please see license.txt

frappe.query_reports["Carrier Loss Scorecard"] = {
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
			fieldname: "carrier",
			label: __("Carrier"),
			fieldtype: "Data",
		},
	],
};
