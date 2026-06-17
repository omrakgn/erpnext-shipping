# Copyright (c) 2020, Frappe Technologies and contributors
# For license information, please see license.txt
import json

import frappe
from frappe import _
from frappe.utils import flt
from erpnext.stock.doctype.shipment.shipment import get_company_contact

from erpnext_shipping.erpnext_shipping.doctype.letmeship.letmeship import (
	LETMESHIP_PROVIDER,
	get_letmeship_utils,
)
from erpnext_shipping.erpnext_shipping.doctype.sendcloud.sendcloud import SENDCLOUD_PROVIDER, SendCloudUtils
from erpnext_shipping.erpnext_shipping.utils import (
	get_address,
	get_contact,
	match_parcel_service_type_carrier,
)


@frappe.whitelist()
def fetch_shipping_rates(
	pickup_from_type,
	delivery_to_type,
	pickup_address_name,
	delivery_address_name,
	parcels,
	description_of_content,
	pickup_date,
	value_of_goods,
	pickup_contact_name=None,
	delivery_contact_name=None,
):
	# Return Shipping Rates for the various Shipping Providers
	shipment_prices = []
	letmeship_enabled = frappe.db.get_single_value("LetMeShip", "enabled")
	sendcloud_enabled = frappe.db.get_single_value("SendCloud", "enabled")
	pickup_address = get_address(pickup_address_name)
	delivery_address = get_address(delivery_address_name)
	parcels = json.loads(parcels)

	if letmeship_enabled:
		pickup_contact = None
		delivery_contact = None
		if pickup_from_type != "Company":
			pickup_contact = get_contact(pickup_contact_name)
		else:
			pickup_contact = get_company_contact(user=pickup_contact_name)
			pickup_contact.email_id = pickup_contact.pop("email", None)

		delivery_contact = get_contact(delivery_contact_name)

		letmeship = get_letmeship_utils()
		letmeship_prices = (
			letmeship.get_available_services(
				delivery_to_type=delivery_to_type,
				pickup_address=pickup_address,
				delivery_address=delivery_address,
				parcels=parcels,
				description_of_content=description_of_content,
				pickup_date=pickup_date,
				value_of_goods=value_of_goods,
				pickup_contact=pickup_contact,
				delivery_contact=delivery_contact,
			)
			or []
		)
		letmeship_prices = match_parcel_service_type_carrier(letmeship_prices, "carrier", "service_name")
		shipment_prices += letmeship_prices

	if sendcloud_enabled:
		sendcloud = SendCloudUtils()
		sendcloud_prices = (
			sendcloud.get_available_services(
				delivery_address=delivery_address, pickup_address=pickup_address, parcels=parcels
			)
			or []
		)
		sendcloud_prices = match_parcel_service_type_carrier(sendcloud_prices, "carrier", "service_name")

		# Favori (yıldızlı) seçenekleri işaretle ve gerekirse sadece onları göster
		preferred_codes = sendcloud.get_preferred_codes()
		if preferred_codes:
			for price in sendcloud_prices:
				if price.get("service_id") in preferred_codes:
					price.is_preferred = 1
			if sendcloud.only_show_preferred():
				sendcloud_prices = [
					price for price in sendcloud_prices if price.get("service_id") in preferred_codes
				]

		shipment_prices += sendcloud_prices

	# "total_price" alanı olan tüm seçenekleri tut (fiyatı None olanlar dahil).
	# SendCloud bazı taşıyıcılar için (örn. kendi sözleşmenizle FedEx) fiyat
	# döndürmez; bu seçeneklerle de gönderi yapılabildiği için elenmemeli.
	shipment_prices = [item for item in shipment_prices if "total_price" in item]
	# Fiyatlı seçenekler önce (ucuzdan pahalıya), fiyatsızlar (None) sona.
	shipment_prices = sorted(
		shipment_prices,
		key=lambda k: (k.get("total_price") is None, k.get("total_price") or 0),
	)
	return shipment_prices


@frappe.whitelist()
def create_shipment(
	shipment,
	pickup_from_type,
	delivery_to_type,
	pickup_address_name,
	delivery_address_name,
	shipment_parcel,
	description_of_content,
	pickup_date,
	value_of_goods,
	service_data,
	shipment_notific_email=None,
	tracking_notific_email=None,
	pickup_contact_name=None,
	delivery_contact_name=None,
	delivery_notes=None,
):
	if isinstance(delivery_notes, str):
		delivery_notes = json.loads(delivery_notes)

	if delivery_notes is None:
		delivery_notes = []

	service_info = json.loads(service_data)
	shipment_info, pickup_contact, delivery_contact = None, None, None
	pickup_address = get_address(pickup_address_name)
	delivery_address = get_address(delivery_address_name)
	delivery_company_name = get_delivery_company_name(shipment)

	if pickup_from_type != "Company":
		pickup_contact = get_contact(pickup_contact_name)

	else:
		pickup_contact = get_company_contact(user=pickup_contact_name)
		pickup_contact.email_id = pickup_contact.pop("email", None)

	delivery_contact = get_contact(delivery_contact_name)

	if service_info["service_provider"] == LETMESHIP_PROVIDER:
		letmeship = get_letmeship_utils()
		shipment_info = letmeship.create_shipment(
			pickup_address=pickup_address,
			delivery_company_name=delivery_company_name,
			delivery_address=delivery_address,
			shipment_parcel=shipment_parcel,
			description_of_content=description_of_content,
			pickup_date=pickup_date,
			value_of_goods=value_of_goods,
			pickup_contact=pickup_contact,
			delivery_contact=delivery_contact,
			service_info=service_info,
		)

	if service_info["service_provider"] == SENDCLOUD_PROVIDER:
		sendcloud = SendCloudUtils()
		shipment_info = sendcloud.create_shipment(
			shipment=shipment,
			delivery_address=delivery_address,
			pickup_address=pickup_address,
			pickup_contact=pickup_contact,
			shipment_parcel=shipment_parcel,
			delivery_contact=delivery_contact,
			service_info=service_info,
		)

	if shipment_info:
		shipment = frappe.get_doc("Shipment", shipment)
		shipment.db_set(
			{
				"service_provider": shipment_info.get("service_provider"),
				"carrier": shipment_info.get("carrier"),
				"carrier_service": shipment_info.get("carrier_service"),
				"shipment_id": shipment_info.get("shipment_id"),
				"shipment_amount": shipment_info.get("shipment_amount"),
				"awb_number": shipment_info.get("awb_number"),
				"status": "Booked",
			}
		)

		if delivery_notes:
			update_delivery_note(delivery_notes=delivery_notes, shipment_info=shipment_info)

	return shipment_info


@frappe.whitelist()
def populate_parcels_from_delivery_notes(shipment: str):
	"""Bağlı Delivery Note'lardaki ürünlere göre Shipment Parcel ve Parcel Items
	tablolarını doldur.

	- Her ürün için, Item'daki `custom_shipment_parcel_template` ile bir koli (Shipment
	  Parcel) satırı oluşturulur (şablon ölçüleri, count = adet, kutu başına 1 adet).
	- Şablonu olmayan ürünler atlanır ve kullanıcıya bildirilir.
	- Mevcut satırlar temizlenip yeniden oluşturulur (frontend ezme onayı alır).
	"""
	shipment_doc = frappe.get_doc("Shipment", shipment)

	# Bağlı Delivery Note'lardan ürün -> toplam adet (ilk görülme sırası korunur)
	item_qty = {}
	for dn_row in shipment_doc.get("shipment_delivery_note", []):
		if not dn_row.delivery_note:
			continue
		for item in frappe.get_all(
			"Delivery Note Item",
			filters={"parent": dn_row.delivery_note},
			fields=["item_code", "qty"],
			order_by="idx",
		):
			if not item.item_code:
				continue
			item_qty[item.item_code] = item_qty.get(item.item_code, 0) + (item.qty or 0)

	if not item_qty:
		frappe.throw(_("No items found in the linked Delivery Notes."))

	has_parcel_items = shipment_doc.meta.has_field("custom_parcel_items")

	# Mevcut satırları temizle
	shipment_doc.set("shipment_parcel", [])
	if has_parcel_items:
		shipment_doc.set("custom_parcel_items", [])

	skipped = []
	parcel_no = 0
	for item_code, qty in item_qty.items():
		template = frappe.db.get_value("Item", item_code, "custom_shipment_parcel_template")
		if not template:
			skipped.append(item_code)
			continue

		dims = (
			frappe.db.get_value(
				"Shipment Parcel Template",
				template,
				["length", "width", "height", "weight"],
				as_dict=True,
			)
			or {}
		)
		item_weight = flt(frappe.db.get_value("Item", item_code, "weight_per_unit") or 0)

		# Adet -> count (tam birim ise kutu başına 1 adet)
		if qty == int(qty):
			count = int(qty) or 1
			box_qty = 1
		else:
			count = 1
			box_qty = qty

		parcel_no += 1
		shipment_doc.append(
			"shipment_parcel",
			{
				"length": dims.get("length") or 0,
				"width": dims.get("width") or 0,
				"height": dims.get("height") or 0,
				"weight": flt(dims.get("weight") or item_weight),
				"count": count,
				"parcel_template": template,
			},
		)
		if has_parcel_items:
			shipment_doc.append(
				"custom_parcel_items",
				{"parcel_no": parcel_no, "item_code": item_code, "qty": box_qty},
			)

	shipment_doc.save()

	message = _("Populated {0} parcel(s) from Delivery Notes.").format(parcel_no)
	if skipped:
		message += "<br>" + _("No parcel template set, skipped: {0}").format(", ".join(skipped))
	frappe.msgprint(
		message,
		title=_("Parcels Populated"),
		indicator="green" if parcel_no else "orange",
	)

	return {"created": parcel_no, "skipped": skipped}


def get_delivery_company_name(shipment: str) -> str | None:
	shipment_doc = frappe.get_doc("Shipment", shipment)
	if shipment_doc.delivery_customer:
		return frappe.db.get_value("Customer", shipment_doc.delivery_customer, "customer_name")
	if shipment_doc.delivery_supplier:
		return frappe.db.get_value("Supplier", shipment_doc.delivery_supplier, "supplier_name")
	if shipment_doc.delivery_company:
		return frappe.db.get_value("Company", shipment_doc.delivery_company, "company_name")

	return None


@frappe.whitelist()
def print_shipping_label(shipment: str):
	shipment_doc = frappe.get_doc("Shipment", shipment)
	service_provider = shipment_doc.service_provider
	shipment_id = shipment_doc.shipment_id

	if service_provider == LETMESHIP_PROVIDER:
		letmeship = get_letmeship_utils()
		shipping_label = letmeship.get_label(shipment_id)
	elif service_provider == SENDCLOUD_PROVIDER:
		sendcloud = SendCloudUtils()
		shipping_label = []
		_labels = sendcloud.get_label(shipment_id)
		for i, label_url in enumerate(_labels, start=1):
			content = sendcloud.download_label(label_url)
			file_url = save_label_as_attachment(shipment, content, i)
			shipping_label.append(file_url)

	return shipping_label


def save_label_as_attachment(shipment: str, content: bytes, index: int = None) -> str:
	"""Store label as attachment to Shipment and return the URL."""
	attachment = frappe.new_doc("File")
	if index is not None:
		attachment.file_name = f"label_{shipment}_{index}.pdf"
	else:
		attachment.file_name = f"label_{shipment}.pdf"
	attachment.content = content
	attachment.folder = "Home/Attachments"
	attachment.attached_to_doctype = "Shipment"
	attachment.attached_to_name = shipment
	attachment.is_private = 1
	attachment.save()
	return attachment.file_url


@frappe.whitelist()
def update_tracking(shipment, service_provider, shipment_id, delivery_notes=None):
	if isinstance(delivery_notes, str):
		delivery_notes = json.loads(delivery_notes)

	if delivery_notes is None:
		delivery_notes = []

	# Update Tracking info in Shipment
	tracking_data = None
	if service_provider == LETMESHIP_PROVIDER:
		letmeship = get_letmeship_utils()
		tracking_data = letmeship.get_tracking_data(shipment_id)
	elif service_provider == SENDCLOUD_PROVIDER:
		sendcloud = SendCloudUtils()
		tracking_data = sendcloud.get_tracking_data(shipment_id)

	if not tracking_data:
		return

	shipment = frappe.get_doc("Shipment", shipment)
	shipment.db_set(
		{
			"awb_number": tracking_data.get("awb_number"),
			"tracking_status": tracking_data.get("tracking_status"),
			"tracking_status_info": tracking_data.get("tracking_status_info"),
			"tracking_url": tracking_data.get("tracking_url"),
		}
	)

	if delivery_notes:
		update_delivery_note(delivery_notes=delivery_notes, tracking_info=tracking_data)


def update_delivery_note(delivery_notes, shipment_info=None, tracking_info=None):
	# Update Shipment Info in Delivery Note
	# Using db_set since some services might not exist
	delivery_notes = list(set(delivery_notes))

	for delivery_note in delivery_notes:
		dl_doc = frappe.get_doc("Delivery Note", delivery_note)
		if shipment_info:
			dl_doc.db_set("delivery_type", "Parcel Service")
			dl_doc.db_set("parcel_service", shipment_info.get("carrier"))
			dl_doc.db_set("parcel_service_type", shipment_info.get("carrier_service"))
		if tracking_info:
			dl_doc.db_set("tracking_number", tracking_info.get("awb_number"))
			dl_doc.db_set("tracking_url", tracking_info.get("tracking_url"))
			dl_doc.db_set("tracking_status", tracking_info.get("tracking_status"))
			dl_doc.db_set("tracking_status_info", tracking_info.get("tracking_status_info"))
