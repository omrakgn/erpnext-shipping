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
				"in_list_view": 1,
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
				"in_list_view": 1,
			},
			{
				"fieldname": "custom_shipping_price",
				"label": _("Price"),
				"fieldtype": "Float",
				"read_only": 1,
				"insert_after": "custom_shipping_service",
				"allow_on_submit": 1,
			},
			{
				"fieldname": "custom_shipping_contract",
				"label": _("Contract"),
				"fieldtype": "Data",
				"read_only": 1,
				"translatable": 0,
				"insert_after": "custom_shipping_price",
				"allow_on_submit": 1,
			},
			{
				"fieldname": "custom_shipping_contract_id",
				"label": _("Contract ID"),
				"fieldtype": "Data",
				"read_only": 1,
				"hidden": 1,
				"translatable": 0,
				"insert_after": "custom_shipping_contract",
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
			{
				"fieldname": "custom_delivered_at",
				"label": _("Delivered At"),
				"fieldtype": "Datetime",
				"read_only": 1,
				"insert_after": "tracking_status",
			},
			{
				"fieldname": "custom_transit_days",
				"label": _("Transit Days"),
				"fieldtype": "Float",
				"precision": "1",
				"read_only": 1,
				"insert_after": "custom_delivered_at",
				"description": _("Days from Pickup Date to delivery (carrier transit time)."),
			},
			{
				"fieldname": "custom_tracking_details",
				"label": _("Tracking Details (JSON)"),
				"fieldtype": "Long Text",
				"read_only": 1,
				"hidden": 1,
				"translatable": 0,
				"insert_after": "custom_transit_days",
			},
			{
				"fieldname": "custom_label_removed",
				"label": _("Label Removed"),
				"fieldtype": "Check",
				"read_only": 1,
				"in_standard_filter": 1,
				"insert_after": "custom_tracking_details",
				"description": _(
					"Auto-set when the SendCloud parcel is no longer found (e.g. deleted by the carrier)."
				),
			},
			{
				"fieldname": "custom_shipping_cost",
				"label": _("Shipping Cost (Net)"),
				"fieldtype": "Currency",
				"read_only": 1,
				"options": "custom_shipping_cost_currency",
				"insert_after": "custom_label_removed",
				"description": _(
					"Net carrier cost rolled up from imported invoice lines (Shipping Cost Entry)."
				),
			},
			{
				"fieldname": "custom_shipping_cost_currency",
				"label": _("Shipping Cost Currency"),
				"fieldtype": "Data",
				"read_only": 1,
				"hidden": 1,
				"translatable": 0,
				"default": "EUR",
				"insert_after": "custom_shipping_cost",
			},
			{
				"fieldname": "custom_shipping_cost_updated",
				"label": _("Shipping Cost Updated"),
				"fieldtype": "Datetime",
				"read_only": 1,
				"insert_after": "custom_shipping_cost_currency",
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
				"fieldname": "custom_shipment_tracking",
				"label": _("Shipment Tracking"),
				"fieldtype": "HTML",
				"insert_after": "parcel_service_type",
			},
			{
				"fieldname": "custom_shipping_cost",
				"label": _("Shipping Cost (Net)"),
				"fieldtype": "Currency",
				"read_only": 1,
				"insert_after": "custom_shipment_tracking",
				"description": _(
					"Net carrier cost of the linked Shipment (from imported invoice lines)."
				),
			},
		]
	}


def get_fields_for_patch(doctype: str, fieldnames: list[str]) -> dict[str, list[dict]]:
	"""Return specific fields that are needed for a patch."""
	return {doctype: [field for field in get_custom_fields()[doctype] if field["fieldname"] in fieldnames]}
