from .utils import identity as _


def get_custom_fields():
	return {
		"Item": [
			{
				"fieldname": "custom_shipment_parcel_template",
				"label": _("Shipment Parcel Template"),
				"fieldtype": "Link",
				"options": "Shipment Parcel Template",
				# Details sekmesinde (stock_uom sonrası) — her ürün tipinde görünür.
				# weight_per_unit Inventory sekmesinde olduğu için stok-takipsiz
				# (Product Bundle) ürünlerde erişilemiyordu.
				"insert_after": "stock_uom",
				"description": _(
					"Default parcel box template used when auto-populating Shipment parcels for this item. "
					"For a Product Bundle set the bundle's box size here; the box weight is summed from the components."
				),
			},
			{
				"fieldname": "custom_ship_separate_parcels",
				"label": _("Ship Components Separately"),
				"fieldtype": "Check",
				"insert_after": "custom_shipment_parcel_template",
				"description": _(
					"Product Bundle only: ship each component in its own parcel (using the "
					"component's own template) instead of one box for the whole bundle."
				),
			},
		],
		"Shipment Parcel": [
			{
				"fieldname": "custom_value_of_goods",
				"label": _("Value of Goods"),
				"fieldtype": "Currency",
				"in_list_view": 1,
				"insert_after": "count",
				"description": _(
					"Goods value for this parcel/label. Auto-derived from the source Delivery "
					"Note (split by weight when the order spans several parcels); editable."
				),
			},
			{
				"fieldname": "custom_delivery_note",
				"label": _("Delivery Note"),
				"fieldtype": "Link",
				"options": "Delivery Note",
				"read_only": 1,
				"insert_after": "custom_value_of_goods",
				"description": _("Source Delivery Note this parcel was built from."),
			},
			{
				"fieldname": "custom_source_item",
				"label": _("Source Item"),
				"fieldtype": "Data",
				"read_only": 1,
				"translatable": 0,
				"insert_after": "custom_delivery_note",
				"description": _(
					"Source Delivery Note line item (bundle parent for bundles). Value of Goods "
					"is derived from this line's amount, split across its parcels by weight."
				),
			},
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
				"fieldname": "custom_delivery_status_section",
				"label": _("Delivery Status"),
				"fieldtype": "Section Break",
				"collapsible": 1,
				"insert_after": "tracking_status",
			},
			{
				"fieldname": "custom_delivered_at",
				"label": _("Delivered At"),
				"fieldtype": "Datetime",
				"read_only": 1,
				"insert_after": "custom_delivery_status_section",
			},
			{
				"fieldname": "custom_transit_days",
				"label": _("Transit Days"),
				"fieldtype": "Float",
				"precision": "1",
				"read_only": 1,
				# Teslim edilmeyen gönderilerde Float varsayılanı 0.0 -> "0 günde teslim"
				# gibi yanıltıcı görünüyor. Yalnızca teslim tarihi varken göster.
				"depends_on": "custom_delivered_at",
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
				"fieldname": "custom_is_delayed",
				"label": _("Delayed"),
				"fieldtype": "Check",
				"read_only": 1,
				"in_standard_filter": 1,
				"insert_after": "custom_label_removed",
				"description": _(
					"Auto-set by the daily check when the shipment is still not delivered "
					"after the configured number of days from pickup. Cleared on delivery."
				),
			},
			{
				"fieldname": "custom_delay_notified",
				"label": _("Delay Notified"),
				"fieldtype": "Check",
				"read_only": 1,
				"hidden": 1,
				"insert_after": "custom_is_delayed",
				"description": _(
					"Set once the automatic delay email(s) have been sent for this shipment, so "
					"the daily job does not resend. Cleared when the shipment is no longer delayed."
				),
			},
			{
				"fieldname": "custom_delay_notified_at",
				"label": _("Delay Notified At"),
				"fieldtype": "Datetime",
				"read_only": 1,
				"hidden": 1,
				"insert_after": "custom_delay_notified",
			},
			{
				"fieldname": "custom_presumed_lost",
				"label": _("Presumed Lost"),
				"fieldtype": "Check",
				"read_only": 1,
				"in_standard_filter": 1,
				"insert_after": "custom_delay_notified_at",
				"description": _(
					"Auto-set when a shipment stays undelivered beyond the presumed-lost "
					"threshold (carriers rarely mark parcels Lost). Review and open a Shipment "
					"Loss Claim if needed."
				),
			},
			{
				"fieldname": "custom_sla_section",
				"label": _("Delivery SLA"),
				"fieldtype": "Section Break",
				"collapsible": 1,
				"insert_after": "custom_presumed_lost",
			},
			{
				"fieldname": "custom_sales_channel",
				"label": _("Sales Channel"),
				"fieldtype": "Select",
				"options": "\nAmazon\nBol.com\nShopify\nOther",
				"translatable": 0,
				"in_standard_filter": 1,
				"insert_after": "custom_sla_section",
				"description": _("Marketplace / channel this order was sold on (for SLA performance)."),
			},
			{
				"fieldname": "custom_promised_delivery_date",
				"label": _("Promised Delivery Date"),
				"fieldtype": "Date",
				"insert_after": "custom_sales_channel",
				"description": _(
					"Optional override. Leave blank — the SLA Date is computed automatically "
					"as the dispatch date (Pickup Date, else Delivery Note date) + the carrier "
					"SLA days. Only set this to force a specific promised date."
				),
			},
			{
				"fieldname": "custom_sla_date",
				"label": _("SLA Date"),
				"fieldtype": "Date",
				"read_only": 1,
				"insert_after": "custom_promised_delivery_date",
				"description": _("Effective SLA delivery date (promised date, else Pickup + carrier SLA days)."),
			},
			{
				"fieldname": "custom_sla_status",
				"label": _("SLA Status"),
				"fieldtype": "Select",
				"options": "\nOn Track\nAt Risk\nBreached\nMet\nMissed",
				"translatable": 0,
				"read_only": 1,
				"in_standard_filter": 1,
				"insert_after": "custom_sla_date",
				"description": _(
					"On Track / At Risk / Breached for undelivered; Met / Missed once delivered "
					"(vs the SLA Date)."
				),
			},
			{
				"fieldname": "custom_sla_risk_notified",
				"label": _("SLA Risk Notified"),
				"fieldtype": "Date",
				"read_only": 1,
				"hidden": 1,
				"insert_after": "custom_sla_status",
			},
			{
				"fieldname": "custom_sla_breach_notified",
				"label": _("SLA Breach Notified"),
				"fieldtype": "Date",
				"read_only": 1,
				"hidden": 1,
				"insert_after": "custom_sla_risk_notified",
			},
			{
				"fieldname": "custom_cost_section",
				"label": _("Shipping Cost & Margin"),
				"fieldtype": "Section Break",
				"collapsible": 1,
				"insert_after": "custom_sla_breach_notified",
			},
			{
				"fieldname": "custom_shipping_cost",
				"label": _("Shipping Cost (Net)"),
				"fieldtype": "Currency",
				"read_only": 1,
				"options": "custom_shipping_cost_currency",
				"insert_after": "custom_cost_section",
				"description": _(
					"Net carrier cost rolled up from imported invoice lines (Shipping Cost Entry)."
				),
			},
			{
				"fieldname": "custom_surcharge_amount",
				"label": _("Surcharge Amount"),
				"fieldtype": "Currency",
				"read_only": 1,
				"options": "custom_shipping_cost_currency",
				"insert_after": "custom_shipping_cost",
				"description": _(
					"Total carrier surcharges (fuel, oversize/overweight, peak, etc.) within the "
					"shipping cost, from the imported invoice."
				),
			},
			{
				"fieldname": "custom_has_weight_surcharge",
				"label": _("Weight/Size Surcharge"),
				"fieldtype": "Check",
				"read_only": 1,
				"in_standard_filter": 1,
				"insert_after": "custom_surcharge_amount",
				"description": _(
					"The carrier billed an oversize/overweight surcharge — the declared "
					"weight/dimensions for this shipment may be wrong."
				),
			},
			{
				"fieldname": "custom_cost_variance",
				"label": _("Cost Variance"),
				"fieldtype": "Currency",
				"read_only": 1,
				"options": "custom_shipping_cost_currency",
				"insert_after": "custom_has_weight_surcharge",
				"description": _(
					"Invoiced cost minus the quote (shipment_amount). Positive = paid more than "
					"quoted. Only meaningful for shipments booked through ERPNext (quote > 0)."
				),
			},
			{
				"fieldname": "custom_cost_variance_pct",
				"label": _("Cost Variance %"),
				"fieldtype": "Percent",
				"read_only": 1,
				"insert_after": "custom_cost_variance",
			},
			{
				"fieldname": "custom_customer_shipping_charge",
				"label": _("Customer Shipping Charge"),
				"fieldtype": "Currency",
				"read_only": 1,
				"options": "custom_shipping_cost_currency",
				"insert_after": "custom_cost_variance_pct",
				"description": _(
					"Shipping revenue charged to the customer (summed from the linked Delivery "
					"Notes). 0 when shipping is free. Used for the shipping margin."
				),
			},
			{
				"fieldname": "custom_shipping_margin",
				"label": _("Shipping Margin"),
				"fieldtype": "Currency",
				"read_only": 1,
				"options": "custom_shipping_cost_currency",
				"insert_after": "custom_customer_shipping_charge",
				"description": _(
					"Customer Shipping Charge minus the invoiced cost. Negative = shipping is a "
					"net cost (free shipping)."
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
				"insert_after": "custom_shipping_margin",
			},
			{
				"fieldname": "custom_shipping_cost_updated",
				"label": _("Shipping Cost Updated"),
				"fieldtype": "Datetime",
				"read_only": 1,
				"insert_after": "custom_shipping_cost_currency",
			},
			{
				"fieldname": "custom_parcel_breakdown_section",
				"label": _("Parcel Breakdown"),
				"fieldtype": "Section Break",
				"collapsible": 1,
				"insert_after": "custom_shipping_cost_updated",
			},
			{
				"fieldname": "custom_parcel_breakdown",
				"label": _("Parcel Breakdown"),
				"fieldtype": "HTML",
				"insert_after": "custom_parcel_breakdown_section",
				"description": _(
					"Per-parcel tracking number, carrier, cost, status, delivery and label state."
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
			{
				"fieldname": "custom_customer_shipping_charge",
				"label": _("Customer Shipping Charge"),
				"fieldtype": "Currency",
				"insert_after": "custom_shipping_cost",
				"description": _(
					"Shipping revenue charged to the customer for this order. Auto-filled from a "
					"configured taxes account head when set in Shipment Settings; otherwise "
					"editable / 0 (free shipping)."
				),
			},
		],
	}


def get_fields_for_patch(doctype: str, fieldnames: list[str]) -> dict[str, list[dict]]:
	"""Return specific fields that are needed for a patch."""
	return {doctype: [field for field in get_custom_fields()[doctype] if field["fieldname"] in fieldnames]}
