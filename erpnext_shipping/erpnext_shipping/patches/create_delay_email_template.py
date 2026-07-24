# Copyright (c) 2026, Frappe Technologies and contributors
# For license information, please see license.txt
"""Create / keep up to date the "Shipment Delay Inquiry" Email Template used by the
'Ask Carrier about Delay' button on Shipment. Runs both as a patch and on every
migrate (after_migrate) so body changes propagate."""
import frappe

TEMPLATE_NAME = "Shipment Delay Inquiry"

# tracking_numbers (undelivered parcels for this carrier) is passed by
# get_carrier_delay_email; falls back to the shipment's combined awb_number when the
# template is opened without that context (e.g. picked manually in the composer).
# NOTE: the Email Template `subject` is a Data field capped at 140 chars, so keep the
# stored template source short (a Jinja ternary, not a {% if %} block).
SUBJECT = (
	"Delivery delay – "
	"{{ (tracking_numbers|join(', ')) if tracking_numbers else doc.awb_number }}"
	" ({{ doc.carrier }})"
)

BODY = """<p>Dear {{ doc.carrier or "Carrier" }} team,</p>
<p>{% if tracking_numbers and tracking_numbers | length > 1 %}The following parcels have{% else %}One of our shipments has{% endif %}
not been delivered yet. Could you please confirm the current status and the expected
delivery date?</p>
<table style="border-collapse:collapse;font-size:13px" border="0" cellpadding="4">
    <tr><td><b>Tracking / AWB</b></td><td>{% if tracking_numbers %}{{ tracking_numbers | join(", ") }}{% else %}{{ doc.awb_number }}{% endif %}</td></tr>
    <tr><td><b>Carrier</b></td><td>{{ doc.carrier }}</td></tr>
    <tr><td><b>Pickup date</b></td><td>{{ frappe.utils.formatdate(doc.pickup_date) }}</td></tr>
    <tr><td><b>Ship to</b></td><td>{{ contact_name or doc.delivery_customer or doc.delivery_company or "" }}</td></tr>
    {% if order_refs %}<tr><td><b>Order No</b></td><td>{{ order_refs | join(", ") }}</td></tr>{% endif %}
    <tr><td><b>Our reference</b></td><td>{{ doc.name }}</td></tr>
</table>
<p>Thank you,<br>{{ doc.pickup_company or "" }}</p>
"""


def execute():
	values = {
		"subject": SUBJECT,
		"use_html": 1,
		"response_html": BODY,
		# response (plain) kept in sync as a fallback for older renderers.
		"response": BODY,
	}
	if frappe.db.exists("Email Template", TEMPLATE_NAME):
		doc = frappe.get_doc("Email Template", TEMPLATE_NAME)
		doc.update(values)
		doc.flags.ignore_permissions = True
		doc.save()
	else:
		doc = frappe.get_doc({"doctype": "Email Template", "name": TEMPLATE_NAME, **values})
		doc.flags.ignore_permissions = True
		doc.insert(ignore_if_duplicate=True)
