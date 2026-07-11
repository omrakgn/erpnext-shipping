// Copyright (c) 2026, Frappe and contributors
// For license information, please see license.txt

frappe.provide("frappe.dashboards.chart_sources");

frappe.dashboards.chart_sources["Carrier Transit Days"] = {
	method:
		"erpnext_shipping.erpnext_shipping.dashboard_chart_source.carrier_transit_days.carrier_transit_days.get_data",
	filters: [],
};
