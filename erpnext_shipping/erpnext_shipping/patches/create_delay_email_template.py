# Copyright (c) 2026, Frappe Technologies and contributors
# For license information, please see license.txt
"""Create / keep up to date the "Shipment Delay Inquiry" Email Template used by the
'Ask Carrier about Delay' button on Shipment. Runs both as a patch and on every
migrate (after_migrate) so body changes propagate."""
import frappe

TEMPLATE_NAME = "Shipment Delay Inquiry"

SUBJECT = "Delivery delay inquiry – Tracking {{ doc.awb_number }} ({{ doc.carrier }})"

BODY = """<p>Dear {{ doc.carrier or "Carrier" }} team,</p>
<p>One of our shipments has not been delivered yet. Could you please confirm its
current status and the expected delivery date?</p>
<table style="border-collapse:collapse;font-size:13px" border="0" cellpadding="4">
    <tr><td><b>Tracking / AWB</b></td><td>{{ doc.awb_number }}</td></tr>
    <tr><td><b>Carrier</b></td><td>{{ doc.carrier }}</td></tr>
    <tr><td><b>Pickup date</b></td><td>{{ frappe.utils.formatdate(doc.pickup_date) }}</td></tr>
    <tr><td><b>Ship to</b></td><td>{{ doc.delivery_customer or doc.delivery_company or "" }}</td></tr>
    {% set dns = doc.shipment_delivery_note | map(attribute="delivery_note") | select | list -%}
    {% if dns %}<tr><td><b>Order / Delivery Note</b></td><td>{{ dns | join(", ") }}</td></tr>{% endif %}
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
