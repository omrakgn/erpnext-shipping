# Copyright (c) 2026, Frappe Technologies and contributors
# For license information, please see license.txt
"""Print format for Pickup Item Summary — total quantity per item for a pickup
date, printable / exportable as PDF. Runs on every migrate so edits propagate."""
import frappe

PRINT_FORMAT_NAME = "Pickup Item Summary"

HTML = r"""
{%- set logo = get_company_logo_src(doc.company) -%}
<div style="margin-bottom:10px;">
	<table style="width:100%; border:none;">
		<tr>
			<td style="border:none; vertical-align:middle; width:55%;">
				{% if logo %}<img src="{{ logo }}" style="max-height:38px;">{% endif %}
			</td>
			<td style="border:none; vertical-align:middle; text-align:right; width:45%; font-size:12px;">
				<div><strong>{{ _("Pickup Date") }}:</strong> {{ doc.get_formatted("pickup_date") }}</div>
				<div><strong>{{ _("Document") }}:</strong> {{ doc.name }}</div>
			</td>
		</tr>
	</table>
	<h3 style="text-align:center; margin:6px 0;">{{ _("Pickup Item Summary") }}</h3>
</div>

<table style="width:100%; border-collapse:collapse; font-size:12px;" border="1">
	<thead>
		<tr style="background:#f5f5f5;">
			<th style="padding:3px 5px; width:25%;">{{ _("Item Code") }}</th>
			<th style="padding:3px 5px;">{{ _("Description") }}</th>
			<th style="padding:3px 5px; width:15%; text-align:right;">{{ _("Total Qty") }}</th>
		</tr>
	</thead>
	<tbody>
		{% for it in doc.items %}
			<tr>
				<td style="padding:3px 5px;">{{ it.item_code }}</td>
				<td style="padding:3px 5px;">{{ it.item_name or "" }}</td>
				<td style="padding:3px 5px; text-align:right;">{{ "%g"|format(it.qty or 0) }}</td>
			</tr>
		{% endfor %}
	</tbody>
</table>

<div style="margin-top:14px; font-size:12px;">
	<strong>{{ _("Distinct Items") }}:</strong> {{ doc.total_items }} &nbsp;·&nbsp;
	<strong>{{ _("Total Quantity") }}:</strong> {{ "%g"|format(doc.total_qty or 0) }}
</div>
"""


def execute():
	pf = (
		frappe.get_doc("Print Format", PRINT_FORMAT_NAME)
		if frappe.db.exists("Print Format", PRINT_FORMAT_NAME)
		else frappe.new_doc("Print Format")
	)
	pf.name = PRINT_FORMAT_NAME
	pf.doc_type = "Pickup Item Summary"
	pf.module = "ERPNext Shipping"
	pf.print_format_type = "Jinja"
	pf.custom_format = 1
	pf.standard = "No"
	pf.disabled = 0
	pf.html = HTML
	pf.save(ignore_permissions=True)
	try:
		frappe.make_property_setter(
			{
				"doctype": "Pickup Item Summary",
				"doctype_or_field": "DocType",
				"property": "default_print_format",
				"value": PRINT_FORMAT_NAME,
				"property_type": "Data",
			}
		)
	except Exception:
		pass
