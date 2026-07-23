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
		// Tracking no'yu carrier takip linkine tıklanır yap.
		if (column.fieldname === "awb_number" && data && data.tracking_url && value) {
			return `<a href="${encodeURI(data.tracking_url)}" target="_blank" rel="noopener">${frappe.utils.escape_html(
				value
			)}</a>`;
		}
		value = default_formatter(value, row, column, data);
		if (column.fieldname === "days_elapsed" && data && data.days_elapsed >= 5) {
			value = `<span style="color:var(--red-600,#c0392b);font-weight:600;">${value}</span>`;
		}
		return value;
	},
};
