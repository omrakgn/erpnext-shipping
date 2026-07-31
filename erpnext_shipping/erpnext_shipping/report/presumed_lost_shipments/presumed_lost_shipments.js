// Copyright (c) 2026, Frappe and contributors
// For license information, please see license.txt

frappe.query_reports["Presumed Lost Shipments"] = {
	filters: [
		{
			fieldname: "min_days",
			label: __("Undelivered at least (days)"),
			fieldtype: "Int",
			description: __("Leave 0 to use the Presumed Lost threshold from Shipment Settings."),
		},
		{
			fieldname: "carrier",
			label: __("Carrier"),
			fieldtype: "Data",
		},
	],
	formatter(value, row, column, data, default_formatter) {
		value = default_formatter(value, row, column, data);
		if (column.fieldname === "days_elapsed" && data && data.days_elapsed) {
			value = `<span style="color:var(--red-600,#c0392b);font-weight:600;">${value}</span>`;
		}
		return value;
	},
};
