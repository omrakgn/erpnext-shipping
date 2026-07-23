// Copyright (c) 2026, Frappe and contributors
// For license information, please see license.txt

frappe.query_reports["Delayed Shipments"] = {
	filters: [
		{
			fieldname: "min_days",
			label: __("Minimum Days Since Pickup"),
			fieldtype: "Int",
			default: 5,
		},
		{
			fieldname: "carrier",
			label: __("Carrier"),
			fieldtype: "Data",
		},
	],
	formatter(value, row, column, data, default_formatter) {
		value = default_formatter(value, row, column, data);
		if (column.fieldname === "days_elapsed" && data && data.days_elapsed >= 5) {
			value = `<span style="color:var(--red-600,#c0392b);font-weight:600;">${value}</span>`;
		}
		return value;
	},
};
