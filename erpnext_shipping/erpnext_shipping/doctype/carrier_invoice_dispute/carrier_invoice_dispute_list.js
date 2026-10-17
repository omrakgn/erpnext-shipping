// Copyright (c) 2026, Frappe and contributors
// For license information, please see license.txt

frappe.listview_settings["Carrier Invoice Dispute"] = {
	add_fields: ["status", "deadline", "answer_due"],

	get_indicator(doc) {
		const colors = {
			Draft: "gray",
			Sent: "blue",
			Accepted: "green",
			"Partly Accepted": "orange",
			Rejected: "red",
			Credited: "green",
			Closed: "gray",
		};
		const today = frappe.datetime.get_today();
		if (doc.status === "Draft" && doc.deadline && doc.deadline < today) {
			return [__("Deadline Passed"), "red", "status,=,Draft"];
		}
		if (doc.status === "Sent" && doc.answer_due && doc.answer_due < today) {
			return [__("Answer Late"), "orange", "status,=,Sent"];
		}
		return [__(doc.status), colors[doc.status] || "gray", `status,=,${doc.status}`];
	},
};
