# Copyright (c) 2020, Frappe Technologies Pvt. Ltd. and contributors
# For license information, please see license.txt

import json
import re

import frappe
import requests
from frappe import _
from frappe.model.document import Document
from frappe.utils import flt
from frappe.utils.data import get_link_to_form
from requests.exceptions import HTTPError

from erpnext_shipping.erpnext_shipping.utils import show_error_alert

SENDCLOUD_PROVIDER = "SendCloud"
WEIGHT_DECIMALS = 3
CURRENCY_DECIMALS = 2

BASE_URL = "https://panel.sendcloud.sc/api"
FETCH_SHIPPING_OPTIONS_URL = f"{BASE_URL}/v3/fetch-shipping-options"
SHIPMENTS_URL = f"{BASE_URL}/v3/shipments"
SHIPMENTS_ANNOUNCE_URL = f"{BASE_URL}/v3/shipments/announce"
LABELS_URL = f"{BASE_URL}/v2/labels"
PARCELS_URL = f"{BASE_URL}/v2/parcels"


class SendCloud(Document):
	pass


class SendCloudUtils:
	def __init__(self):
		settings = frappe.get_single("SendCloud")
		self.api_key = settings.api_key
		self.api_secret = settings.get_password("api_secret")
		self.enabled = settings.enabled

		if not self.enabled:
			link = get_link_to_form("SendCloud", "SendCloud", _("SendCloud Settings"))
			frappe.throw(_("Please enable SendCloud Integration in {0}").format(link))

	def get_available_services(self, delivery_address, pickup_address, parcels: list[dict]):
		# Retrieve rates at SendCloud from specification stated.
		if not self.enabled or not self.api_key or not self.api_secret:
			return []

		max_weight = max(parcel.get("weight", 0) for parcel in parcels)
		max_length = max(parcel.get("length", 0) for parcel in parcels)
		max_width = max(parcel.get("width", 0) for parcel in parcels)
		max_height = max(parcel.get("height", 0) for parcel in parcels)

		to_country = delivery_address.country_code.upper()
		from_country = pickup_address.country_code.upper()

		payload = {
			"to_country_code": to_country,
			"from_country_code": from_country,
			"weight": {"value": max_weight, "unit": "kg"},
			"dimensions": {"length": max_length, "width": max_width, "height": max_height, "unit": "cm"},
		}

		try:
			response = requests.post(
				FETCH_SHIPPING_OPTIONS_URL,
				json=payload,
				auth=(self.api_key, self.api_secret),
				headers={"Accept": "application/json", "Content-Type": "application/json"},
			)

			response_data = response.json()

			if "error" in response_data:
				error_message = response_data["error"]["message"]
				frappe.throw(error_message, title=_("SendCloud"))

			if "data" not in response_data or not response_data["data"]:
				frappe.throw(_("No shipping options found for this destination."), title=_("Sendcloud"))

			available_services = []
			for service in response_data["data"]:
				available_service = self.get_service_dict(service, parcels)
				available_services.append(available_service)

			return available_services
		except Exception:
			show_error_alert("fetching SendCloud prices")

	def create_shipment(
		self,
		shipment,
		pickup_address,
		pickup_contact,
		delivery_address,
		delivery_contact,
		service_info,
		shipment_parcel,
	):
		if not self.enabled or not self.api_key or not self.api_secret:
			return []

		# Shipment item bilgilerini bir kez al
		shipment_items_data = self.get_shipment_items(shipment)
		
		parcels = []
		for i, parcel in enumerate(json.loads(shipment_parcel), start=1):
			parcel_count = parcel.get("count", 1)
			for j in range(parcel_count):
				parcel_data = self.get_parcel(
					parcel,
					shipment,
					i,
					shipment_items_data,
				)
				parcels.append(parcel_data)

		house_number, address = self.extract_house_number(pickup_address.address_line1)

		# Müşteri tipini belirle (Şirket mi, Bireysel mi?)
		customer_name = f"{delivery_contact.first_name} {delivery_contact.last_name}"
		company_name = self.get_company_name(delivery_address, customer_name)

		# Teslimat adresi için house number extraction
		delivery_house_number, delivery_street = self.extract_house_number(delivery_address.address_line1)

		# to_address oluştur
		to_address = {
			"name": customer_name,
			"address_line_1": delivery_street or delivery_address.address_line1,
			"postal_code": delivery_address.pincode,
			"city": delivery_address.city,
			"country_code": delivery_address.country_code.upper(),
		}
		
		# House number varsa ekle
		if delivery_house_number:
			to_address["house_number"] = delivery_house_number
		
		# Opsiyonel alanları sadece doluysa ekle
		if company_name:
			to_address["company_name"] = company_name
		if delivery_contact.phone:
			to_address["phone_number"] = delivery_contact.phone
		if delivery_contact.email_id:
			to_address["email"] = delivery_contact.email_id

		# from_address oluştur
		from_address = {
			"name": f"{pickup_contact.first_name} {pickup_contact.last_name}",
			"address_line_1": address or pickup_address.address_line1,
			"house_number": house_number or " ",
			"postal_code": pickup_address.pincode,
			"city": pickup_address.city,
			"country_code": pickup_address.country_code.upper(),
		}
		
		# Company name için: address_title adres gibi görünüyorsa, şirket adını farklı yerden al
		from_company = pickup_address.address_title
		if from_company and not any(word in from_company.lower() for word in ['straat', 'laan', 'weg', 'street', 'road', 'avenue']):
			from_address["company_name"] = from_company
		else:
			# Address title adres içeriyorsa, Company'den şirket adını al
			from_address["company_name"] = frappe.defaults.get_global_default("company") or "Scarnatti"
		
		if pickup_contact.phone:
			from_address["phone_number"] = pickup_contact.phone
		if pickup_contact.email_id:
			from_address["email"] = pickup_contact.email_id

		# Order number: Önce PO, yoksa SO, yoksa Shipment adı
		api_order_number = shipment
		if shipment_items_data and shipment_items_data.get("order_number"):
			api_order_number = shipment_items_data["order_number"]

		payload = {
			"order_number": api_order_number,
			"parcels": parcels,
			"to_address": to_address,
			"from_address": from_address,
			"ship_with": {
				"type": "shipping_option_code",
				"properties": {
					"shipping_option_code": service_info["service_id"],
				},
			},
		}
		
		# Brand ID ekle (SendCloud Settings'ten al)
		if self.get_brand_id():
			payload["brand_id"] = self.get_brand_id()

		# DEBUG: Payload'u logla
		frappe.log_error(message=json.dumps(payload, indent=2, default=str), title="SendCloud Debug Payload")

		if service_info.get("multicollo"):
			# Multicollo Logic: All packages are processed in a single API call
			try:
				response = requests.post(
					SHIPMENTS_URL,
					json=payload,
					auth=(self.api_key, self.api_secret),
				)
				response_data = response.json()
				
				# DEBUG: API Response logla
				frappe.log_error(message=json.dumps(response_data, indent=2, default=str), title="SendCloud API Response (multicollo)")
				
				if "errors" in response_data and response_data["errors"]:
					error_details = [
						f"Code: {err.get('code', 'N/A')}, Detail: {err.get('detail', 'N/A')}"
						for err in response_data["errors"]
					]
					error_message = "\n".join(error_details)
					frappe.msgprint(
						_("Error occurred while creating shipment for parcel {0}:").format(
							parcel.get("order_number")
						)
						+ f"\n{error_message}",
						indicator="red",
						alert=True,
					)
					return None

				parcels_data = response_data.get("data", {}).get("parcels", [])
				if parcels_data:
					shipment_ids = [str(parcel["id"]) for parcel in parcels_data]
					tracking_numbers = [parcel.get("tracking_number") or "" for parcel in parcels_data]
					tracking_urls = [parcel.get("tracking_url") or "" for parcel in parcels_data]
					return {
						"service_provider": "SendCloud",
						"shipment_id": ", ".join(shipment_ids),
						"carrier": self.get_carrier(service_info["carrier"], post_or_get="post"),
						"carrier_service": service_info["service_name"],
						"shipment_amount": service_info["total_price"],
						"awb_number": ", ".join(tracking_numbers),
						"tracking_url": ", ".join(tracking_urls),
					}
			except Exception:
				show_error_alert("creating SendCloud Shipment (multicollo)")
		else:
			# Non-Multicollo Logic: A separate API call is made for each package
			shipments_results = []
			for parcel in parcels:
				payload_single = payload.copy()
				payload_single["parcels"] = [parcel]
				try:
					response = requests.post(
						SHIPMENTS_ANNOUNCE_URL,
						json=payload_single,
						auth=(self.api_key, self.api_secret),
					)
					response_data = response.json()
					
					# DEBUG: API Response logla
					frappe.log_error(message=json.dumps(response_data, indent=2, default=str), title="SendCloud API Response (non-multicollo)")
					
					if "errors" in response_data and response_data["errors"]:
						error_details = [
							f"Code: {err.get('code', 'N/A')}, Detail: {err.get('detail', 'N/A')}"
							for err in response_data["errors"]
						]
						error_message = "\n".join(error_details)
						frappe.msgprint(
							_("Error occurred while creating shipment for parcel {0}:").format(
								parcel.get("order_number")
							)
							+ f"\n{error_message}",
							indicator="red",
							alert=True,
						)
						continue

					parcels_data = response_data.get("data", {}).get("parcels", [])
					if parcels_data:
						parcel_data = parcels_data[0]
						shipments_results.append(
							{
								"shipment_id": str(parcel_data["id"]),
								"awb_number": parcel_data.get("tracking_number", ""),
								"tracking_url": parcel_data.get("tracking_url", ""),
								"carrier": self.get_carrier(service_info["carrier"], post_or_get="post"),
								"carrier_service": service_info["service_name"],
								"shipment_amount": service_info["total_price"],
							}
						)
				except Exception:
					show_error_alert(f"creating SendCloud Shipment for parcel {parcel.get('order_number')}")
			if shipments_results:
				combined_result = {
					"service_provider": "SendCloud",
					"shipment_id": ", ".join(
						item["shipment_id"] for item in shipments_results if item.get("shipment_id")
					),
					"carrier": shipments_results[0]["carrier"],
					"carrier_service": shipments_results[0]["carrier_service"],
					"shipment_amount": service_info["total_price"],
					"awb_number": ", ".join(
						item["awb_number"] for item in shipments_results if item.get("awb_number")
					),
					"tracking_url": ", ".join(
						item["tracking_url"] for item in shipments_results if item.get("tracking_url")
					),
				}
				return combined_result

		return None

	def get_label(self, shipment_id):
		# Retrieve shipment label from SendCloud
		shipment_id_list = shipment_id.split(", ")
		label_urls = []

		for ship_id in shipment_id_list:
			try:
				response = requests.get(
					f"{LABELS_URL}/{ship_id}",
					auth=(self.api_key, self.api_secret),
					headers={"Accept": "application/json"},
				)
				response.raise_for_status()
				data = response.json()
				label_url = data.get("label", {}).get("label_printer")
				if label_url:
					label_urls.append(label_url)
				else:
					frappe.msgprint(
						msg=_(
							"Please make sure Shipment (ID: {0}), exists and is a complete Shipment on SendCloud."
						).format(ship_id),
						title=_("Label Not Found"),
					)
			except Exception:
				show_error_alert("printing SendCloud Label")

		return label_urls

	def download_label(self, label_url: str):
		"""Download label from SendCloud."""
		try:
			resp = requests.get(label_url, auth=(self.api_key, self.api_secret))
			resp.raise_for_status()
			return resp.content
		except HTTPError:
			frappe.msgprint(
				_("An error occurred while downloading label from SendCloud"), indicator="orange", alert=True
			)

	def get_tracking_data(self, shipment_id):
		# return SendCloud tracking data
		shipment_id_list = shipment_id.split(", ")
		awb_number, tracking_status, tracking_urls = [], [], []

		for ship_id in shipment_id_list:
			try:
				response = requests.get(
					f"{PARCELS_URL}/{ship_id}",
					auth=(self.api_key, self.api_secret),
					headers={"Accept": "application/json"},
				)
				response.raise_for_status()
				tracking_data = response.json()
			except Exception:
				show_error_alert("updating SendCloud Shipment")
				continue

			parcel_data = tracking_data.get("parcel", {})

			tracking_url = parcel_data.get("tracking_url")
			if tracking_url:
				tracking_urls.append(tracking_url)

			tracking_number = parcel_data.get("tracking_number")
			if tracking_number:
				awb_number.append(tracking_number)

			status_message = parcel_data.get("status", {}).get("message")
			if status_message:
				tracking_status.append(status_message)

		return {
			"awb_number": ", ".join(awb_number),
			"tracking_status": ", ".join(tracking_status),
			"tracking_status_info": ", ".join(tracking_status),
			"tracking_url": ", ".join(tracking_urls),
		}

	def total_parcel_price(self, parcel_price, parcels: list[dict]):
		count = 0
		for parcel in parcels:
			count += parcel.get("count")
		return flt(parcel_price) * count

	def get_service_dict(self, service, parcels: list[dict]):
		"""Returns a dictionary with service info."""
		available_service = frappe._dict()
		available_service.service_provider = "SendCloud"
		available_service.carrier = service["carrier"]["name"]
		available_service.service_name = service["product"]["name"]
		available_service.service_id = service["code"]
		available_service.multicollo = service["functionalities"].get("multicollo", False)

		quotes = service.get("quotes", [])
		if quotes:
			price_data = quotes[0].get("price", {}).get("total", {})
			available_service.total_price = self.total_parcel_price(
				float(price_data.get("value", 0)), parcels
			)
			available_service.currency = price_data.get("currency")

		return available_service

	def get_carrier(self, carrier_name, post_or_get=None):
		# make 'sendcloud' => 'SendCloud' while displaying rates
		# reverse the same while creating shipment
		if carrier_name in ("sendcloud", "SendCloud"):
			return "SendCloud" if post_or_get == "get" else "sendcloud"
		else:
			return carrier_name.upper() if post_or_get == "get" else carrier_name.lower()

	def get_brand_id(self):
		"""SendCloud Settings'ten brand_id al"""
		try:
			settings = frappe.get_single("SendCloud")
			brand_id = settings.get("brand_id")
			if brand_id:
				return int(brand_id)
		except Exception:
			pass
		return None

	def get_parcel(self, parcel, shipment, index, shipment_items_data=None):
		"""Parcel verilerini SendCloud API formatında hazırla"""
		parcel_data = {
			"dimensions": {
				"length": parcel.get("length", 0),
				"width": parcel.get("width", 0),
				"height": parcel.get("height", 0),
				"unit": "cm",
			},
			"weight": {"value": flt(parcel.get("weight", 0), WEIGHT_DECIMALS), "unit": "kg"},
			"order_number": f"{shipment}-{index}",
		}

		# Item bilgilerini ekle
		if shipment_items_data and shipment_items_data.get("parcel_items"):
			parcel_data["parcel_items"] = shipment_items_data["parcel_items"]
			
			# Label notes (SKU bilgileri)
			if shipment_items_data.get("label_notes"):
				parcel_data["label_notes"] = shipment_items_data["label_notes"]

		return parcel_data

	def get_shipment_items(self, shipment_name):
		"""
		Shipment'a bağlı Delivery Note'lardan item bilgilerini al.
		SendCloud API formatında parcel_items listesi döndürür.
		
		Returns:
			dict: {
				"parcel_items": [...],
				"order_number": "PO veya SO numarası",
				"label_notes": ["SKU[qty]", ...],
				"total_value": toplam değer
			}
		"""
		items_dict = {}  # SKU bazlı birleştirme için
		sku_qty_dict = {}  # Label notes için SKU -> toplam adet
		order_number = None
		total_value = 0
		default_currency = "EUR"

		try:
			shipment_doc = frappe.get_doc("Shipment", shipment_name)

			# Shipment'a bağlı Delivery Note'ları kontrol et
			delivery_notes = shipment_doc.get("shipment_delivery_note", [])

			if not delivery_notes:
				# Delivery Note yoksa None döndür
				return None

			for dn_row in delivery_notes:
				if not dn_row.delivery_note:
					continue

				delivery_note = frappe.get_doc("Delivery Note", dn_row.delivery_note)
				default_currency = delivery_note.currency or "EUR"
				
				# Sales Order'dan PO numarasını al
				if not order_number:
					for item in delivery_note.items:
						if item.against_sales_order:
							so = frappe.get_doc("Sales Order", item.against_sales_order)
							# Öncelik: Customer's Purchase Order (po_no), yoksa Sales Order adı
							if so.po_no:
								order_number = so.po_no
							else:
								order_number = so.name
							break

				for item in delivery_note.items:
					if not item.item_code:
						continue
						
					item_doc = frappe.get_cached_doc("Item", item.item_code)
					sku = item_doc.get("custom_sku") or item.item_code
					
					# Aynı SKU'ları birleştir
					if sku in items_dict:
						# Mevcut item'a ekle
						items_dict[sku]["quantity"] += int(item.qty)
						items_dict[sku]["price"]["value"] += flt(item.amount, CURRENCY_DECIMALS)
						items_dict[sku]["weight"]["value"] += flt(item.total_weight or 0, WEIGHT_DECIMALS)
						sku_qty_dict[sku] += int(item.qty)
					else:
						# Yeni item oluştur
						item_weight = item.total_weight or 0
						if not item_weight and item.qty:
							item_weight = item.qty * (item_doc.weight_per_unit or 0)
						
						items_dict[sku] = {
							"description": (item.item_name or item.item_code or "Product")[:200],
							"quantity": int(item.qty),
							"price": {
								"value": flt(item.amount, CURRENCY_DECIMALS),
								"currency": default_currency
							},
							"weight": {
								"value": flt(item_weight, WEIGHT_DECIMALS) if item_weight > 0 else 0.1,
								"unit": "kg"
							},
							"sku": sku[:50]
						}
						
						# HS Code ve Origin Country
						if item_doc.customs_tariff_number:
							items_dict[sku]["hs_code"] = item_doc.customs_tariff_number[:20]
						if item_doc.country_of_origin:
							country_code = frappe.db.get_value("Country", item_doc.country_of_origin, "code")
							if country_code:
								items_dict[sku]["origin_country"] = country_code.upper()
						
						sku_qty_dict[sku] = int(item.qty)
					
					total_value += item.amount

			if not items_dict:
				return None
			
			# Label notes oluştur - SKU[toplam adet] formatında
			label_notes = []
			for sku, qty in sku_qty_dict.items():
				note = f"{sku}[{qty}]"
				# Max 50 karakter limiti
				if len(note) > 50:
					note = f"{sku[:45]}[{qty}]"
				label_notes.append(note)
				
			return {
				"parcel_items": list(items_dict.values()),
				"order_number": order_number,
				"label_notes": label_notes if label_notes else None,
				"total_value": total_value
			}

		except Exception as e:
			frappe.log_error(
				message=f"Error getting shipment items for {shipment_name}: {str(e)}",
				title="SendCloud - Get Shipment Items Error"
			)
			return None

	def format_parcel_item(self, item, currency="EUR"):
		"""
		Delivery Note Item'ı SendCloud API v3 parcel_item formatına dönüştür.

		SendCloud API v3 formatı:
		{
			"description": "T-Shirt",
			"hs_code": "6109",
			"origin_country": "SE",
			"product_id": "898678671",
			"quantity": 2,
			"sku": "TST-OD2019-B620",
			"value": {"value": 19.95, "currency": "EUR"},
			"weight": {"value": 0.9, "unit": "kg"}
		}
		"""
		try:
			# Temel item bilgileri
			parcel_item = {
				"description": (item.item_name or item.item_code or "Product")[:200],
				"quantity": int(item.qty),
				"price": {
					"value": flt(item.amount, CURRENCY_DECIMALS),
					"currency": currency or "EUR"
				},
			}

			# Ağırlık hesapla
			item_weight = 0
			if item.total_weight:
				item_weight = item.total_weight
			elif item.qty and hasattr(item, 'weight_per_unit') and item.weight_per_unit:
				item_weight = item.qty * item.weight_per_unit

			# Ağırlık varsa ekle, yoksa varsayılan 0.1 kg
			parcel_item["weight"] = {
				"value": flt(item_weight, WEIGHT_DECIMALS) if item_weight > 0 else 0.1,
				"unit": "kg"
			}

			# SKU ekle
			if item.item_code:
				parcel_item["sku"] = item.item_code[:50]

			# Item master'dan ek bilgileri al
			if item.item_code:
				item_doc = frappe.get_cached_doc("Item", item.item_code)

				# HS Code (Gümrük Tarife Numarası)
				if item_doc.customs_tariff_number:
					parcel_item["hs_code"] = item_doc.customs_tariff_number[:20]

				# Menşei Ülke
				if item_doc.country_of_origin:
					country_code = frappe.db.get_value(
						"Country", item_doc.country_of_origin, "code"
					)
					if country_code:
						parcel_item["origin_country"] = country_code.upper()

			return parcel_item

		except Exception as e:
			frappe.log_error(
				message=f"Error formatting parcel item {item.item_code}: {str(e)}",
				title="SendCloud - Format Parcel Item Error"
			)
			return None

	def extract_house_number(self, address):
		pattern = r"\b\d+[/-]?\w*(?:-\d+\w*)?\b"
		match = re.search(pattern, address)
		if match:
			house_number = match.group(0)
			cleaned_address = re.sub(pattern, "", address).strip()
			return house_number, cleaned_address
		else:
			return None, None

	def get_company_name(self, delivery_address, customer_name):
		"""
		Müşteri tipine göre company_name belirle.
		- Şirket müşterisiyse: Şirket adını döndür
		- Bireysel müşteriyse: Boş string döndür (SendCloud'da company_name görünmez)
		"""
		try:
			# Address'e bağlı Customer veya Link'i bul
			customer = None
			customer_type = None

			# Önce address_title'dan Customer bulmayı dene
			if delivery_address.address_title:
				# Customer tablosunda ara
				customer_exists = frappe.db.exists("Customer", delivery_address.address_title)
				if customer_exists:
					customer = delivery_address.address_title
					customer_type = frappe.db.get_value("Customer", customer, "customer_type")

			# Eğer bulunamadıysa, Dynamic Link üzerinden bul
			if not customer and delivery_address.name:
				links = frappe.get_all(
					"Dynamic Link",
					filters={
						"link_doctype": "Customer",
						"parenttype": "Address",
						"parent": delivery_address.name
					},
					fields=["link_name"]
				)
				if links:
					customer = links[0].link_name
					customer_type = frappe.db.get_value("Customer", customer, "customer_type")

			# Customer tipi "Company" ise şirket adını döndür
			if customer_type == "Company":
				# Şirket adı olarak address_title veya customer_name kullan
				company_name = delivery_address.address_title or ""

				# Eğer address_title kişi adıyla aynıysa, customer_name'i kontrol et
				if company_name.lower() == customer_name.lower():
					customer_name_from_db = frappe.db.get_value("Customer", customer, "customer_name")
					if customer_name_from_db and customer_name_from_db.lower() != customer_name.lower():
						return customer_name_from_db

				return company_name if company_name.lower() != customer_name.lower() else ""

			# Bireysel müşteri - company_name boş olsun
			return ""

		except Exception as e:
			frappe.log_error(
				message=f"Error determining company name: {str(e)}",
				title="SendCloud - Get Company Name Error"
			)
			# Hata durumunda güvenli tarafta kal, boş döndür
			return ""