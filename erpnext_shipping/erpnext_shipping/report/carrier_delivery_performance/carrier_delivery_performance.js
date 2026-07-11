// Copyright (c) 2026, Frappe and contributors
// For license information, please see license.txt

frappe.query_reports["Carrier Delivery Performance"] = {
	filters: [
		{
			fieldname: "from_date",
			label: __("From Pickup Date"),
			fieldtype: "Date",
		},
		{
			fieldname: "to_date",
			label: __("To Pickup Date"),
			fieldtype: "Date",
		},
		{
			fieldname: "carrier",
			label: __("Carrier"),
			fieldtype: "Data",
		},
	],
};
