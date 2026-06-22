import frappe

PRINT_FORMAT_NAME = "Pickup Manifest"

HTML = r"""
{%- set logo = get_company_logo_src(doc.company) -%}
<div style="margin-bottom:10px;">
	<table style="width:100%; border:none;">
		<tr>
			<td style="border:none; vertical-align:middle; width:55%;">
				{% if logo %}<img src="{{ logo }}" style="max-height:38px;">{% endif %}
			</td>
			<td style="border:none; vertical-align:middle; text-align:right; width:45%; font-size:12px;">
				<div><strong>{{ _("Carrier") }}:</strong> {{ doc.carrier }}</div>
				<div><strong>{{ _("Pickup Date") }}:</strong> {{ doc.get_formatted("pickup_date") }}</div>
				<div><strong>{{ _("Document") }}:</strong> {{ doc.name }}</div>
			</td>
		</tr>
	</table>
	<h3 style="text-align:center; margin:6px 0;">{{ _("Pickup Manifest") }}</h3>
</div>

<table style="width:100%; border-collapse:collapse; font-size:12px;" border="1">
	<thead>
		<tr style="background:#f5f5f5;">
			<th style="padding:3px 5px; width:5%;">{{ _("No") }}</th>
			<th style="padding:3px 5px; width:25%;">{{ _("Tracking Number") }}</th>
			<th style="padding:3px 5px; width:30%;">{{ _("Contact Person") }}</th>
			<th style="padding:3px 5px;">{{ _("Item Code") }}</th>
			<th style="padding:3px 5px; width:8%; text-align:right;">{{ _("Qty") }}</th>
		</tr>
	</thead>
	<tbody>
		{% for p in get_manifest_packages(doc.name) %}
			<tr>
				<td style="padding:3px 5px; text-align:center;">{{ p['seq'] }}</td>
				<td style="padding:3px 5px;">{{ p['tracking'] }}</td>
				<td style="padding:3px 5px;">{{ p['contact'] }}</td>
				<td style="padding:3px 5px;">{% for it in p['items'] %}{{ it['item_code'] }}{% if not loop.last %}<br>{% endif %}{% endfor %}</td>
				<td style="padding:3px 5px; text-align:right;">{% for it in p['items'] %}{{ it['qty'] }}{% if not loop.last %}<br>{% endif %}{% endfor %}</td>
			</tr>
		{% endfor %}
	</tbody>
</table>

<table style="width:100%; border:none; margin-top:18px;">
	<tr>
		<td style="border:none; width:55%; vertical-align:bottom; font-size:12px;">
			<div><strong>{{ _("Total Packages") }}:</strong> {{ doc.total_packages }}</div>
			<div><strong>{{ _("Total Quantity") }}:</strong> {{ "%g"|format(doc.total_qty or 0) }}</div>
		</td>
		<td style="border:none; width:45%; vertical-align:top;">
			<div style="border:1px solid #333; padding:8px; font-size:12px;">
				<div style="font-weight:bold; margin-bottom:6px;">{{ _("Received by (Courier)") }}</div>
				<div style="margin-bottom:10px;">{{ _("Name") }}: {{ doc.courier_name or "____________________" }}</div>
				<div style="margin-bottom:10px;">{{ _("Vehicle Plate") }}: {{ doc.vehicle_plate or "____________________" }}</div>
				<div style="margin-top:22px;">{{ _("Signature") }}: ____________________</div>
			</div>
		</td>
	</tr>
</table>
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
	# KRİTİK: custom_format=1 olmadan Frappe html alanını yok sayar ve otomatik
	# (Standard) alan-tablosu çizer. Bunu işaretlemek html'imizi kullandırır.
	pf.custom_format = 1
	pf.standard = "No"
	pf.disabled = 0
	pf.html = HTML
	pf.save(ignore_permissions=True)

	# Bu DocType için varsayılan baskı formatı yap (Property Setter; DocType'ta native
	# default_print_format kolonu yok). Kritik değil, hata olursa yut.
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
