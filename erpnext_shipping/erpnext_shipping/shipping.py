# Copyright (c) 2020, Frappe Technologies and contributors
# For license information, please see license.txt
import json

import frappe
from frappe import _
from frappe.utils import flt, get_datetime
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
				"shipment_amount": flt(shipment_info.get("shipment_amount")),
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
	seen_dns = set()
	for dn_row in shipment_doc.get("shipment_delivery_note", []):
		# Aynı DN birden fazla listelense bile bir kez işle (adet katlanmasın)
		if not dn_row.delivery_note or dn_row.delivery_note in seen_dns:
			continue
		seen_dns.add(dn_row.delivery_note)
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

		# Her birim ayrı kutu: tam adet kadar ayrı koli satırı (count=1).
		# Böylece her kutu bağımsız düzenlenebilir (örn. bir kutuya hediye eklenebilir).
		whole = int(qty)
		units = [1.0] * whole
		remainder = flt(qty) - whole
		if remainder > 0:
			units.append(remainder)
		if not units:  # qty <= 0 gibi uç durum
			units = [flt(qty) or 1]

		for unit_qty in units:
			parcel_no += 1
			shipment_doc.append(
				"shipment_parcel",
				{
					"length": dims.get("length") or 0,
					"width": dims.get("width") or 0,
					"height": dims.get("height") or 0,
					"weight": flt(dims.get("weight") or item_weight),
					"count": 1,
					"parcel_template": template,
				},
			)
			if has_parcel_items:
				shipment_doc.append(
					"custom_parcel_items",
					{"parcel_no": parcel_no, "item_code": item_code, "qty": unit_qty},
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


@frappe.whitelist()
def get_sendcloud_contracts(carrier=None):
	"""Hesaptaki aktif SendCloud kontratlarını döndür (carrier koduyla filtrelenebilir)."""
	return SendCloudUtils().get_contracts(carrier_code=carrier)


@frappe.whitelist()
def fetch_parcel_rates(shipment, parcel):
	"""Tek bir koli için (kendi ağırlık/ölçüsüyle) SendCloud kargo seçeneklerini getir."""
	if isinstance(parcel, str):
		parcel = json.loads(parcel)

	if not frappe.db.get_single_value("SendCloud", "enabled"):
		return []

	shipment_doc = frappe.get_doc("Shipment", shipment)
	pickup_address = get_address(shipment_doc.pickup_address_name)
	delivery_address = get_address(shipment_doc.delivery_address_name)

	sendcloud = SendCloudUtils()
	prices = (
		sendcloud.get_available_services(
			delivery_address=delivery_address, pickup_address=pickup_address, parcels=[parcel]
		)
		or []
	)
	prices = match_parcel_service_type_carrier(prices, "carrier", "service_name")

	preferred_codes = sendcloud.get_preferred_codes()
	if preferred_codes:
		for price in prices:
			if price.get("service_id") in preferred_codes:
				price.is_preferred = 1
		if sendcloud.only_show_preferred():
			prices = [p for p in prices if p.get("service_id") in preferred_codes]

	prices = [p for p in prices if "total_price" in p]
	prices = sorted(prices, key=lambda k: (k.get("total_price") is None, k.get("total_price") or 0))
	return prices


@frappe.whitelist()
def create_shipment_per_parcel(shipment):
	"""Her koliyi, Shipment Parcel satırında seçilmiş kargo ile ayrı ayrı oluştur."""
	shipment_doc = frappe.get_doc("Shipment", shipment)

	parcels = []
	parcel_services = {}
	for i, row in enumerate(shipment_doc.get("shipment_parcel") or [], start=1):
		parcels.append(
			{
				"length": row.length,
				"width": row.width,
				"height": row.height,
				"weight": row.weight,
				"count": row.count or 1,
			}
		)
		code = row.get("custom_shipping_option_code")
		if code:
			parcel_services[i] = {
				"service_id": code,
				"carrier": row.get("custom_shipping_carrier") or "sendcloud",
				"service_name": row.get("custom_shipping_service") or code,
				"total_price": row.get("custom_shipping_price") or 0,
				"contract_id": row.get("custom_shipping_contract_id"),
			}

	if not parcel_services:
		frappe.throw(_("No per-parcel carrier selected. Use 'Select Carrier' on each parcel first."))

	pickup_address = get_address(shipment_doc.pickup_address_name)
	delivery_address = get_address(shipment_doc.delivery_address_name)

	if shipment_doc.pickup_from_type != "Company":
		pickup_contact = get_contact(shipment_doc.pickup_contact_name)
	else:
		pickup_contact = get_company_contact(user=shipment_doc.pickup_contact_person)
		pickup_contact.email_id = pickup_contact.pop("email", None)
	delivery_contact = get_contact(shipment_doc.delivery_contact_name)

	sendcloud = SendCloudUtils()
	shipment_info = sendcloud.create_shipment_per_parcel(
		shipment=shipment,
		pickup_address=pickup_address,
		pickup_contact=pickup_contact,
		delivery_address=delivery_address,
		delivery_contact=delivery_contact,
		shipment_parcel=json.dumps(parcels),
		parcel_services=parcel_services,
	)

	if shipment_info:
		shipment_doc.db_set(
			{
				"service_provider": shipment_info.get("service_provider"),
				"carrier": shipment_info.get("carrier"),
				"carrier_service": shipment_info.get("carrier_service"),
				"shipment_id": shipment_info.get("shipment_id"),
				"shipment_amount": flt(shipment_info.get("shipment_amount")),
				"awb_number": shipment_info.get("awb_number"),
				"status": "Booked",
			}
		)
		delivery_notes = list(
			{d.delivery_note for d in (shipment_doc.get("shipment_delivery_note") or []) if d.delivery_note}
		)
		if delivery_notes:
			update_delivery_note(delivery_notes=delivery_notes, shipment_info=shipment_info)

	return shipment_info


@frappe.whitelist()
def get_delivery_note_shipment_tracking(delivery_note):
	"""Bu Delivery Note'a bağlı Shipment'ların parça-bazlı takip satırlarını döndür.

	Satırlar Shipment'taki custom_tracking_details JSON'ından gelir; JSON yoksa
	(eski/teslim edilmiş gönderiler) SendCloud'dan bir kez canlı çekilip saklanır.
	"""
	links = frappe.get_all(
		"Shipment Delivery Note",
		filters={"delivery_note": delivery_note, "parenttype": "Shipment"},
		fields=["parent"],
	)
	shipment_names = list({link.parent for link in links})

	rows = []
	for name in shipment_names:
		sh = frappe.get_doc("Shipment", name)
		if sh.docstatus == 2:
			continue

		parcels = []
		details = sh.get("custom_tracking_details")
		if details:
			try:
				parcels = json.loads(details)
			except Exception:
				parcels = []

		# JSON yoksa ve SendCloud ise canlı çek + sakla
		if not parcels and sh.service_provider == SENDCLOUD_PROVIDER and sh.shipment_id:
			data = SendCloudUtils().get_tracking_data(sh.shipment_id) or {}
			parcels = data.get("parcels") or []
			if parcels:
				sh.db_set("custom_tracking_details", json.dumps(parcels))
				if data.get("delivered_at"):
					sh.db_set("custom_delivered_at", get_datetime(data["delivered_at"]))

		for p in parcels:
			rows.append(
				{
					"shipment": name,
					"sku": p.get("sku"),
					"carrier": p.get("carrier") or sh.carrier,
					"tracking_number": p.get("tracking_number"),
					"tracking_url": p.get("tracking_url"),
					"status": p.get("status"),
					"delivered_at": p.get("delivered_at"),
				}
			)

	return rows


def get_shipment_po_no(shipment_doc):
	"""Shipment'a bağlı Delivery Note -> Sales Order -> po_no (Customer's PO No)."""
	for dn_row in shipment_doc.get("shipment_delivery_note") or []:
		if not dn_row.delivery_note:
			continue
		so_rows = frappe.get_all(
			"Delivery Note Item",
			filters={"parent": dn_row.delivery_note, "against_sales_order": ["!=", ""]},
			fields=["against_sales_order"],
			limit=1,
		)
		if so_rows:
			po_no = frappe.db.get_value("Sales Order", so_rows[0].against_sales_order, "po_no")
			if po_no:
				return po_no
	return None


@frappe.whitelist()
def fulfill_sendcloud_order(shipment):
	"""Shipment'ın po_no'su ile eşleşen SendCloud incoming order'ı bul, ERPNext
	bilgileriyle (ağırlık/ölçü/kargo/sözleşme) güncelle ve label oluştur.

	Ship an Order API (create-label-sync) kullanır; pazaryeri (Amazon/Bol/Shopify)
	bağı korunduğu için teslim feedback'i SendCloud üzerinden akmaya devam eder.
	"""
	shipment_doc = frappe.get_doc("Shipment", shipment)

	po_no = get_shipment_po_no(shipment_doc)
	if not po_no:
		frappe.throw(
			_("No Customer's Purchase Order (po_no) found on the linked Sales Order(s).")
		)

	sendcloud = SendCloudUtils()
	order = sendcloud.find_order_by_number(po_no)
	if not order:
		frappe.throw(
			_("No SendCloud order found with order number {0}.").format(frappe.bold(po_no))
		)

	# ERPNext'ten: ilk koliden ağırlık/ölçü; kargo seçimi olan ilk koliden method/sözleşme
	weight, dimensions = None, None
	shipping_option_code, contract_id = None, None
	for row in shipment_doc.get("shipment_parcel") or []:
		if weight is None:
			weight = row.weight
			dimensions = {"length": row.length, "width": row.width, "height": row.height}
		if row.get("custom_shipping_option_code"):
			shipping_option_code = row.get("custom_shipping_option_code")
			contract_id = row.get("custom_shipping_contract_id")
			break

	shipment_info = sendcloud.ship_order(
		order,
		shipping_option_code=shipping_option_code,
		contract_id=contract_id,
		weight=weight,
		dimensions=dimensions,
	)
	if not shipment_info:
		return None

	# Label'ı (base64) Shipment'a ekle
	label_file = shipment_info.pop("label_file", None)
	shipment_info.pop("label_mime_type", None)
	if label_file:
		try:
			import base64

			save_label_as_attachment(shipment, base64.b64decode(label_file))
		except Exception:
			frappe.log_error(title="SendCloud label decode error")

	shipment_doc.db_set(
		{
			"service_provider": shipment_info.get("service_provider"),
			"carrier": shipment_info.get("carrier"),
			"carrier_service": shipment_info.get("carrier_service"),
			"shipment_id": shipment_info.get("shipment_id"),
			"awb_number": shipment_info.get("awb_number"),
			"tracking_url": shipment_info.get("tracking_url"),
			"status": "Booked",
		}
	)

	# Takip bilgilerini çek + sakla (parça-bazlı tablo bundan beslenir)
	if shipment_info.get("shipment_id"):
		try:
			update_tracking(shipment, SENDCLOUD_PROVIDER, shipment_info["shipment_id"])
		except Exception:
			frappe.log_error(title="SendCloud fulfill tracking error")

	frappe.msgprint(
		_("SendCloud order {0} shipped (parcel {1}).").format(
			frappe.bold(po_no), frappe.bold(shipment_info.get("shipment_id") or "")
		),
		title=_("Order Fulfilled"),
		indicator="green",
	)
	return shipment_info


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
	updates = {
		"awb_number": tracking_data.get("awb_number"),
		"tracking_status": tracking_data.get("tracking_status"),
		"tracking_status_info": tracking_data.get("tracking_status_info"),
		"tracking_url": tracking_data.get("tracking_url"),
	}
	# Parça-bazlı detaylar (SKU/carrier/status/teslim zamanı) — Delivery Note tablosu bundan beslenir
	if "parcels" in tracking_data:
		updates["custom_tracking_details"] = json.dumps(tracking_data.get("parcels") or [])
	delivered_at = tracking_data.get("delivered_at")
	if delivered_at:
		updates["custom_delivered_at"] = get_datetime(delivered_at)
	shipment.db_set(updates)


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
		# Tracking artık DN'de düz alanlarda tutulmuyor; canlı tablo (custom_shipment_tracking)
		# bağlı Shipment'ın custom_tracking_details JSON'ından çiziliyor.
