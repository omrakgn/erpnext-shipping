import frappe

PRINT_FORMAT_NAME = "Pickup Manifest"

HTML = r"""
{% set logo = frappe.db.get_value("Company", doc.company, "company_logo") if doc.company else None %}
<div style="margin-bottom:12px;">
	<table style="width:100%; border:none;">
		<tr>
			<td style="border:none; vertical-align:top; width:50%;">
				{% if logo %}<img src="{{ logo }}" style="max-height:60px;">{% endif %}
			</td>
			<td style="border:none; vertical-align:top; text-align:right; width:50%; font-size:13px;">
				<div><strong>{{ _("Carrier") }}:</strong> {{ doc.carrier }}</div>
				<div><strong>{{ _("Pickup Date") }}:</strong>
					{{ doc.get_formatted("from_date") }}{% if doc.to_date and doc.to_date != doc.from_date %} &ndash; {{ doc.get_formatted("to_date") }}{% endif %}
				</div>
				<div><strong>{{ _("Document") }}:</strong> {{ doc.name }}</div>
			</td>
		</tr>
	</table>
	<h2 style="text-align:center; margin:8px 0;">{{ _("Pickup Manifest") }}</h2>
</div>

<table style="width:100%; border-collapse:collapse;" border="1">
	<thead>
		<tr style="background:#f5f5f5;">
			<th style="padding:4px;">{{ _("No") }}</th>
			<th style="padding:4px;">{{ _("Company Name") }}</th>
			<th style="padding:4px;">{{ _("Contact Person") }}</th>
			<th style="padding:4px;">{{ _("Carrier") }}</th>
			<th style="padding:4px;">{{ _("Item Name") }}</th>
			<th style="padding:4px; text-align:right;">{{ _("Qty") }}</th>
		</tr>
	</thead>
	<tbody>
		{% set ns = namespace(prev=None, seq=0, new=False) %}
		{% for row in doc.items %}
			{% if row.package_no != ns.prev %}
				{% set ns.seq = ns.seq + 1 %}
				{% set ns.prev = row.package_no %}
				{% set ns.new = True %}
			{% else %}
				{% set ns.new = False %}
			{% endif %}
			<tr>
				<td style="padding:4px; text-align:center;">{% if ns.new %}{{ ns.seq }}{% endif %}</td>
				<td style="padding:4px;">{% if ns.new %}{{ row.company_name or "" }}{% endif %}</td>
				<td style="padding:4px;">{% if ns.new %}{{ row.contact_person or "" }}{% endif %}</td>
				<td style="padding:4px;">{% if ns.new %}{{ row.carrier or "" }}{% endif %}</td>
				<td style="padding:4px;">{{ row.item_name or "" }}</td>
				<td style="padding:4px; text-align:right;">{{ row.qty }}</td>
			</tr>
		{% endfor %}
	</tbody>
</table>

<div style="margin-top:24px;">
	<table style="width:100%; border:none;">
		<tr>
			<td style="border:none; width:50%; vertical-align:top; font-size:13px;">
				<div><strong>{{ _("Total Packages") }}:</strong> {{ doc.total_packages }}</div>
				<div><strong>{{ _("Total Quantity") }}:</strong> {{ doc.total_qty }}</div>
			</td>
			<td style="border:none; width:50%; vertical-align:top;">
				<div style="border:1px solid #333; padding:10px;">
					<div style="font-weight:bold; margin-bottom:6px;">{{ _("Received by (Courier)") }}</div>
					<div style="margin-bottom:8px;">{{ _("Name") }}: {{ doc.courier_name or "____________________" }}</div>
					<div style="margin-bottom:8px;">{{ _("Vehicle Plate") }}: {{ doc.vehicle_plate or "____________________" }}</div>
					<div style="margin-top:24px;">{{ _("Signature") }}: ____________________</div>
				</div>
			</td>
		</tr>
	</table>
</div>
"""


def execute():
	if frappe.db.exists("Print Format", PRINT_FORMAT_NAME):
		pf = frappe.get_doc("Print Format", PRINT_FORMAT_NAME)
	else:
		pf = frappe.new_doc("Print Format")
		pf.name = PRINT_FORMAT_NAME

	pf.doc_type = "Pickup Manifest"
	pf.module = "ERPNext Shipping"
	pf.print_format_type = "Jinja"
	pf.standard = "No"
	pf.disabled = 0
	pf.html = HTML
	pf.save(ignore_permissions=True)

	# Bu DocType için varsayılan baskı formatı yap (Property Setter ile; DocType'ta
	# native default_print_format kolonu yok). Kritik değil, hata olursa yut.
	try:
		frappe.make_property_setter(
			{
				"doctype": "Pickup Manifest",
				"doctype_or_field": "DocType",
				"property": "default_print_format",
				"value": PRINT_FORMAT_NAME,
				"property_type": "Data",
			}
		)
	except Exception:
		pass
