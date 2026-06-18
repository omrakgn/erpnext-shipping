from .utils import identity as _


def get_custom_fields():
	return {
		"Item": [
			{
				"fieldname": "custom_shipment_parcel_template",
				"label": _("Shipment Parcel Template"),
				"fieldtype": "Link",
				"options": "Shipment Parcel Template",
				"insert_after": "weight_per_unit",
				"description": _(
					"Default parcel box template used when auto-populating Shipment parcels for this item."
				),
			},
		],
		"Shipment Parcel": [
			{
				"fieldname": "custom_carrier_section",
				"label": _("Per-Parcel Carrier"),
				"fieldtype": "Section Break",
				"collapsible": 1,
				"insert_after": "count",
			},
			{
				"fieldname": "custom_select_carrier",
				"label": _("Select Carrier"),
				"fieldtype": "Button",
				"insert_after": "custom_carrier_section",
				"allow_on_submit": 1,
			},
			{
				"fieldname": "custom_shipping_option_code",
				"label": _("Shipping Option Code"),
				"fieldtype": "Data",
				"read_only": 1,
				"translatable": 0,
				"insert_after": "custom_select_carrier",
				"allow_on_submit": 1,
			},
			{
				"fieldname": "custom_shipping_carrier",
				"label": _("Carrier"),
				"fieldtype": "Data",
				"read_only": 1,
				"translatable": 0,
				"insert_after": "custom_shipping_option_code",
				"allow_on_submit": 1,
			},
			{
				"fieldname": "custom_shipping_service",
				"label": _("Service"),
				"fieldtype": "Data",
				"read_only": 1,
				"translatable": 0,
				"insert_after": "custom_shipping_carrier",
				"allow_on_submit": 1,
			},
			{
				"fieldname": "custom_shipping_price",
				"label": _("Price"),
				"fieldtype": "Float",
				"read_only": 1,
				"insert_after": "custom_shipping_service",
				"allow_on_submit": 1,
			},
		],
		"Shipment": [
			{
				"fieldname": "custom_parcel_items",
				"label": _("Parcel Items"),
				"fieldtype": "Table",
				"options": "Shipment Parcel Item",
				"insert_after": "shipment_parcel",
				"description": _(
					"Optional: map which items and quantities go into each parcel. "
					"'Parcel No' matches the row number of the Shipment Parcel table above. "
					"Leave empty to send all Delivery Note items in every parcel."
				),
			},
		],
		"Delivery Note": [
			{
				"fieldname": "shipping_sec_break",
				"label": _("Shipping Details"),
				"fieldtype": "Section Break",
				"collapsible": 1,
				"insert_after": "sales_team",
			},
			{
				"fieldname": "delivery_type",
				"label": _("Delivery Type"),
				"fieldtype": "Data",
				"read_only": 1,
				"translatable": 0,
				"insert_after": "shipping_sec_break",
			},
			{
				"fieldname": "parcel_service",
				"label": _("Parcel Service"),
				"fieldtype": "Data",  # needs to be "Data" for backward compat
				"options": "Parcel Service",
				"read_only": 1,
				"insert_after": "delivery_type",
			},
			{
				"fieldname": "parcel_service_type",
				"label": _("Parcel Service Type"),
				"fieldtype": "Data",  # needs to be "Data" for backward compat
				"options": "Parcel Service Type",
				"read_only": 1,
				"insert_after": "parcel_service",
			},
			{
				"fieldname": "shipping_col_break",
				"fieldtype": "Column Break",
				"insert_after": "parcel_service_type",
			},
			{
				"fieldname": "tracking_number",
				"label": _("Tracking Number"),
				"fieldtype": "Data",
				"read_only": 1,
				"translatable": 0,
				"insert_after": "shipping_col_break",
			},
			{
				"fieldname": "tracking_url",
				"label": _("Tracking URL"),
				"fieldtype": "Small Text",
				"read_only": 1,
				"translatable": 0,
				"insert_after": "tracking_number",
			},
			{
				"fieldname": "tracking_status",
				"label": _("Tracking Status"),
				"fieldtype": "Data",
				"read_only": 1,
				"translatable": 0,
				"insert_after": "tracking_url",
			},
			{
				"fieldname": "tracking_status_info",
				"label": _("Tracking Status Information"),
				"fieldtype": "Data",
				"read_only": 1,
				"translatable": 0,
				"insert_after": "tracking_status",
			},
		]
	}


def get_fields_for_patch(doctype: str, fieldnames: list[str]) -> dict[str, list[dict]]:
	"""Return specific fields that are needed for a patch."""
	return {doctype: [field for field in get_custom_fields()[doctype] if field["fieldname"] in fieldnames]}
