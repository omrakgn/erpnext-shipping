// Copyright (c) 2026, Frappe and contributors
// For license information, please see license.txt

frappe.query_reports["Carrier Surcharges"] = {
	filters: [
		{
			fieldname: "from_date",
			label: __("From Scan Date"),
			fieldtype: "Date",
			default: frappe.datetime.add_months(frappe.datetime.get_today(), -3),
		},
		{
			fieldname: "to_date",
			label: __("To Scan Date"),
			fieldtype: "Date",
			default: frappe.datetime.get_today(),
		},
		{
			fieldname: "carrier",
			label: __("Carrier"),
			fieldtype: "Data",
		},
		{
			fieldname: "only_surcharged",
			label: __("Only with surcharge"),
			fieldtype: "Check",
			default: 1,
		},
		{
			fieldname: "only_weight_surcharge",
			label: __("Only weight/size or re-weighed"),
			fieldtype: "Check",
		},
	],
	formatter(value, row, column, data, default_formatter) {
		value = default_formatter(value, row, column, data);
		if (column.fieldname === "surcharge_pct" && data && flt(data.surcharge_pct) >= 25) {
			value = `<span style="color:var(--red-600,#c0392b);font-weight:600;">${value}</span>`;
		}
		return value;
	},
};
