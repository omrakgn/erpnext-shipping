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
CONTRACTS_URL = f"{BASE_URL}/v3/contracts"
ORDERS_URL = f"{BASE_URL}/v3/orders"
CREATE_LABEL_SYNC_URL = f"{BASE_URL}/v3/orders/create-label-sync"

# shipping_option_code önekinden okunur carrier adı (örn. "dpd:classic/b2b" -> "DPD").
CARRIER_DISPLAY = {
	"dpd": "DPD",
	"fedex": "FedEx",
	"ups": "UPS",
	"dhl": "DHL",
	"dhl_express": "DHL Express",
	"gls": "GLS",
	"postnl": "PostNL",
	"bpost": "bpost",
	"colissimo": "Colissimo",
	"chronopost": "Chronopost",
	"sendcloud": "SendCloud",
}


def carrier_display_from_code(shipping_option_code):
	"""shipping_option_code önekinden okunur carrier adı döndür (yoksa None)."""
	if not shipping_option_code or ":" not in shipping_option_code:
		return None
	prefix = shipping_option_code.split(":")[0].strip().lower()
	return CARRIER_DISPLAY.get(prefix, prefix.upper())


class SendCloud(Document):
	pass


@frappe.whitelist()
def toggle_preferred_shipping_option(code, service_label=None, carrier=None):
	"""Bir SendCloud shipping option kodunu favorilere ekle/çıkar.

	Fetch Shipping Rates penceresindeki yıldız butonundan çağrılır. SendCloud
	ayarları yalnızca System Manager'a açık olduğundan, kaydı izin atlayarak
	yapar (yalnızca favori listesini günceller).
	"""
	if not code:
		return {"preferred": False}

	settings = frappe.get_doc("SendCloud", "SendCloud")
	existing = next(
		(r for r in (settings.preferred_shipping_options or []) if r.shipping_option_code == code),
		None,
	)

	if existing:
		settings.remove(existing)
		preferred = False
	else:
		settings.append(
			"preferred_shipping_options",
			{"shipping_option_code": code, "service_label": service_label, "carrier": carrier},
		)
		preferred = True

	settings.save(ignore_permissions=True)
	return {"preferred": preferred}


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

		# Shipment item bilgilerini ve koli-bazlı ürün eşleştirmesini bir kez al
		shipment_doc = frappe.get_doc("Shipment", shipment)
		shipment_items_data = self.get_shipment_items(shipment_doc)
		parcel_item_map = self.get_parcel_item_map(shipment_doc)

		parcels = []
		for i, parcel in enumerate(json.loads(shipment_parcel), start=1):
			parcel_count = parcel.get("count", 1)
			for j in range(parcel_count):
				parcel_data = self.get_parcel(
					parcel,
					shipment,
					i,
					shipment_items_data,
					parcel_item_map,
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

		ship_with_properties = {"shipping_option_code": service_info["service_id"]}
		# Sözleşme seçimi (panel'deki "Enabled contract"): varsa açıkça gönder
		if service_info.get("contract_id"):
			ship_with_properties["contract"] = service_info["contract_id"]

		payload = {
			"order_number": api_order_number,
			"parcels": parcels,
			"to_address": to_address,
			"from_address": from_address,
			"ship_with": {
				"type": "shipping_option_code",
				"properties": ship_with_properties,
			},
		}
		
		# Brand ID ekle (SendCloud Settings'ten al)
		brand_id = self.get_brand_id()
		if brand_id:
			payload["brand_id"] = brand_id

		if service_info.get("multicollo"):
			# Multicollo Logic: All packages are processed in a single API call
			try:
				response = requests.post(
					SHIPMENTS_URL,
					json=payload,
					auth=(self.api_key, self.api_secret),
				)
				response_data = response.json()

				if "errors" in response_data and response_data["errors"]:
					frappe.log_error(
						message=json.dumps(response_data, indent=2, default=str),
						title="SendCloud Shipment Error (multicollo)",
					)
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
						"shipment_amount": service_info.get("total_price") or 0,
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

					if "errors" in response_data and response_data["errors"]:
						frappe.log_error(
							message=json.dumps(response_data, indent=2, default=str),
							title="SendCloud Shipment Error (non-multicollo)",
						)
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
								"shipment_amount": service_info.get("total_price") or 0,
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
					"shipment_amount": service_info.get("total_price") or 0,
					"awb_number": ", ".join(
						item["awb_number"] for item in shipments_results if item.get("awb_number")
					),
					"tracking_url": ", ".join(
						item["tracking_url"] for item in shipments_results if item.get("tracking_url")
					),
				}
				return combined_result

		return None

	def build_addresses(
		self, shipment, pickup_address, pickup_contact, delivery_address, delivery_contact, shipment_items_data
	):
		"""SendCloud to_address / from_address ve order_number'ı oluştur (create yollarında ortak)."""
		# Müşteri tipi (Şirket / Bireysel)
		customer_name = f"{delivery_contact.first_name} {delivery_contact.last_name}"
		company_name = self.get_company_name(delivery_address, customer_name)

		delivery_house_number, delivery_street = self.extract_house_number(delivery_address.address_line1)
		to_address = {
			"name": customer_name,
			"address_line_1": delivery_street or delivery_address.address_line1,
			"postal_code": delivery_address.pincode,
			"city": delivery_address.city,
			"country_code": delivery_address.country_code.upper(),
		}
		if delivery_house_number:
			to_address["house_number"] = delivery_house_number
		if company_name:
			to_address["company_name"] = company_name
		if delivery_contact.phone:
			to_address["phone_number"] = delivery_contact.phone
		if delivery_contact.email_id:
			to_address["email"] = delivery_contact.email_id

		house_number, address = self.extract_house_number(pickup_address.address_line1)
		from_address = {
			"name": f"{pickup_contact.first_name} {pickup_contact.last_name}",
			"address_line_1": address or pickup_address.address_line1,
			"house_number": house_number or " ",
			"postal_code": pickup_address.pincode,
			"city": pickup_address.city,
			"country_code": pickup_address.country_code.upper(),
		}
		from_company = pickup_address.address_title
		if from_company and not any(
			word in from_company.lower() for word in ["straat", "laan", "weg", "street", "road", "avenue"]
		):
			from_address["company_name"] = from_company
		else:
			from_address["company_name"] = frappe.defaults.get_global_default("company") or "Scarnatti"
		if pickup_contact.phone:
			from_address["phone_number"] = pickup_contact.phone
		if pickup_contact.email_id:
			from_address["email"] = pickup_contact.email_id

		# Order number: Önce PO, yoksa SO, yoksa Shipment adı
		api_order_number = shipment
		if shipment_items_data and shipment_items_data.get("order_number"):
			api_order_number = shipment_items_data["order_number"]

		return to_address, from_address, api_order_number

	def create_shipment_per_parcel(
		self,
		shipment,
		pickup_address,
		pickup_contact,
		delivery_address,
		delivery_contact,
		shipment_parcel,
		parcel_services,
	):
		"""Her koliyi kendi seçilen SendCloud kargosuyla AYRI AYRI oluştur.

		parcel_services: {parcel_no: {"service_id","carrier","service_name","total_price"}}
		parcel_no, Shipment Parcel satırının sıra numarasıdır (1-based).
		"""
		if not self.enabled or not self.api_key or not self.api_secret:
			return None

		shipment_doc = frappe.get_doc("Shipment", shipment)
		shipment_items_data = self.get_shipment_items(shipment_doc)
		parcel_item_map = self.get_parcel_item_map(shipment_doc)
		to_address, from_address, api_order_number = self.build_addresses(
			shipment, pickup_address, pickup_contact, delivery_address, delivery_contact, shipment_items_data
		)

		base_payload = {
			"order_number": api_order_number,
			"to_address": to_address,
			"from_address": from_address,
		}
		brand_id = self.get_brand_id()
		if brand_id:
			base_payload["brand_id"] = brand_id

		results = []
		for i, parcel in enumerate(json.loads(shipment_parcel), start=1):
			service = parcel_services.get(str(i)) or parcel_services.get(i)
			if not service or not service.get("service_id"):
				continue  # Bu koli için kargo seçilmemiş, atla
			parcel_count = parcel.get("count", 1)
			for _j in range(parcel_count):
				parcel_data = self.get_parcel(parcel, shipment, i, shipment_items_data, parcel_item_map)
				ship_with_properties = {"shipping_option_code": service["service_id"]}
				if service.get("contract_id"):
					ship_with_properties["contract"] = service["contract_id"]

				payload = dict(base_payload)
				payload["parcels"] = [parcel_data]
				payload["ship_with"] = {
					"type": "shipping_option_code",
					"properties": ship_with_properties,
				}
				try:
					response = requests.post(
						SHIPMENTS_ANNOUNCE_URL, json=payload, auth=(self.api_key, self.api_secret)
					)
					response_data = response.json()

					if "errors" in response_data and response_data["errors"]:
						frappe.log_error(
							message=json.dumps(response_data, indent=2, default=str),
							title="SendCloud Per-Parcel Shipment Error",
						)
						errs = "; ".join(
							f"{e.get('code', 'N/A')}: {e.get('detail', 'N/A')}"
							for e in response_data["errors"]
						)
						frappe.msgprint(
							_("Parcel {0} ({1}) error: {2}").format(i, service.get("service_name"), errs),
							indicator="red",
							alert=True,
						)
						continue

					parcels_data = response_data.get("data", {}).get("parcels", [])
					if parcels_data:
						pd = parcels_data[0]
						results.append(
							{
								"shipment_id": str(pd["id"]),
								"awb_number": pd.get("tracking_number") or "",
								"tracking_url": pd.get("tracking_url") or "",
								"carrier": self.get_carrier(service["carrier"], post_or_get="post"),
								"carrier_service": service["service_name"],
								"shipment_amount": service.get("total_price") or 0,
							}
						)
				except Exception:
					show_error_alert(f"creating SendCloud per-parcel shipment {i}")

		if not results:
			return None

		carriers = sorted({r["carrier"] for r in results})
		services = sorted({r["carrier_service"] for r in results})
		return {
			"service_provider": "SendCloud",
			"shipment_id": ", ".join(r["shipment_id"] for r in results if r.get("shipment_id")),
			"carrier": ", ".join(carriers),
			"carrier_service": ", ".join(services),
			"shipment_amount": sum(flt(r.get("shipment_amount")) for r in results),
			"awb_number": ", ".join(r["awb_number"] for r in results if r.get("awb_number")),
			"tracking_url": ", ".join(r["tracking_url"] for r in results if r.get("tracking_url")),
		}

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
		# return SendCloud tracking data (combined + per-parcel details)
		shipment_id_list = shipment_id.split(", ")
		awb_number, tracking_status, tracking_urls = [], [], []
		parcels = []
		delivered_times = []

		for ship_id in shipment_id_list:
			try:
				response = requests.get(
					f"{PARCELS_URL}/{ship_id}",
					auth=(self.api_key, self.api_secret),
					headers={"Accept": "application/json"},
				)
				# Parcel SendCloud'dan silinmişse (ör. carrier tarafından) 404 döner;
				# bu beklenen bir durum, sessizce atla (saatlik job'u loglarla doldurma).
				if response.status_code == 404:
					continue
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

			status_message = (parcel_data.get("status") or {}).get("message")
			if status_message:
				tracking_status.append(status_message)

			# Parçadaki ürün SKU'ları
			skus = [it.get("sku") for it in (parcel_data.get("parcel_items") or []) if it.get("sku")]

			# Teslim zamanı: parça Delivered ise son güncelleme zamanı (date_updated)
			delivered_at = None
			if status_message == "Delivered":
				delivered_at = parcel_data.get("date_updated")
				if delivered_at:
					delivered_times.append(delivered_at)

			parcels.append(
				{
					"parcel_id": str(parcel_data.get("id") or ship_id),
					"sku": ", ".join(skus),
					"carrier": (parcel_data.get("carrier") or {}).get("name")
					or (parcel_data.get("carrier") or {}).get("code"),
					"tracking_number": tracking_number or "",
					"tracking_url": tracking_url or "",
					"status": status_message or "",
					"delivered_at": delivered_at,
				}
			)

		# Shipment geneli: tüm parçalar Delivered ise teslim zamanı = en geç parça zamanı
		all_delivered = bool(parcels) and all(p["status"] == "Delivered" for p in parcels)
		shipment_delivered_at = max(delivered_times) if (all_delivered and delivered_times) else None

		return {
			"awb_number": ", ".join(awb_number),
			"tracking_status": ", ".join(tracking_status),
			"tracking_status_info": ", ".join(tracking_status),
			"tracking_url": ", ".join(tracking_urls),
			"parcels": parcels,
			"delivered_at": shipment_delivered_at,
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
		available_service.carrier = (service.get("carrier") or {}).get("name")
		available_service.carrier_code = (service.get("carrier") or {}).get("code")
		available_service.service_name = (service.get("product") or {}).get("name")
		available_service.service_id = service.get("code")
		available_service.multicollo = (service.get("functionalities") or {}).get("multicollo", False)

		# Sözleşme (panel'deki "Enabled contract" karşılığı)
		contract = service.get("contract") or {}
		available_service.contract_id = contract.get("id")
		available_service.contract_name = contract.get("name")

		quotes = service.get("quotes", [])
		if quotes:
			price_data = (quotes[0].get("price") or {}).get("total") or {}
			available_service.total_price = self.total_parcel_price(
				float(price_data.get("value", 0)), parcels
			)
			available_service.currency = price_data.get("currency")
		else:
			# Bazı taşıyıcılar (örn. kendi sözleşmenizle FedEx) API'den fiyat (quote)
			# döndürmez. Bu seçeneklerle de gönderi yapılabildiği için listede
			# tutuyoruz; fiyat alanı boş gösterilir.
			available_service.total_price = None
			available_service.currency = None

		return available_service

	def get_carrier(self, carrier_name, post_or_get=None):
		# make 'sendcloud' => 'SendCloud' while displaying rates
		# reverse the same while creating shipment
		if carrier_name in ("sendcloud", "SendCloud"):
			return "SendCloud" if post_or_get == "get" else "sendcloud"
		else:
			return carrier_name.upper() if post_or_get == "get" else carrier_name.lower()

	def get_contracts(self, carrier_code=None):
		"""Hesaptaki aktif kontratları döndür (panel'deki 'Enabled contract' listesi).

		Returns: [{id, name, carrier_code, carrier_name, is_default, type}]
		"""
		if not self.enabled or not self.api_key or not self.api_secret:
			return []
		try:
			response = requests.get(
				CONTRACTS_URL, auth=(self.api_key, self.api_secret), headers={"Accept": "application/json"}
			)
			response.raise_for_status()
			data = response.json().get("data", [])
		except Exception:
			show_error_alert("fetching SendCloud contracts")
			return []

		contracts = []
		for c in data:
			carrier = c.get("carrier") or {}
			if carrier_code and carrier.get("code") != carrier_code:
				continue

			# Okunur etiket: isim boşsa carrier + tip + id; aktif değilse durumu ekle
			ctype = c.get("type") or "contract"
			name = c.get("name") or f"{carrier.get('name')} {ctype} ({c.get('id')})"
			state = c.get("state")
			if state and state != "active":
				name = f"{name} [{state}]"

			contracts.append(
				{
					"id": c.get("id"),
					"name": name,
					"carrier_code": carrier.get("code"),
					"carrier_name": carrier.get("name"),
					"is_default": c.get("is_default_per_carrier"),
					"type": ctype,
					"state": state,
				}
			)
		return contracts

	def get_preferred_codes(self):
		"""SendCloud Settings'teki favori (yıldızlı) shipping option kodları."""
		settings = frappe.get_single("SendCloud")
		return {
			row.shipping_option_code
			for row in (settings.get("preferred_shipping_options") or [])
			if row.shipping_option_code
		}

	def only_show_preferred(self):
		"""Sadece favori seçenekler gösterilsin mi?"""
		return bool(frappe.db.get_single_value("SendCloud", "only_show_preferred"))

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

	def find_order_by_number(self, order_number):
		"""SendCloud incoming order'ı order_number (ERPNext po_no) ile bul.

		Pazaryeri entegrasyonlarından (Amazon/Bol/Shopify) gelen siparişleri arar.
		Bulursa order dict döner, yoksa None.
		"""
		if not self.enabled or not self.api_key or not self.api_secret:
			return None
		try:
			response = requests.get(
				ORDERS_URL,
				params={"order_number": order_number},
				auth=(self.api_key, self.api_secret),
				headers={"Accept": "application/json"},
			)
			response.raise_for_status()
			data = response.json().get("data", [])
		except Exception:
			show_error_alert("finding SendCloud order")
			return None

		# order_number tam eşleşeni öncele; bulunamazsa ilk sonucu döndür
		for order in data:
			if str(order.get("order_number")) == str(order_number):
				return order
		return data[0] if data else None

	def find_parcel_by_order_number(self, order_number):
		"""order_number ile eşleşen, İPTAL EDİLMEMİŞ en güncel parcel'ı (label) bul.

		Label SendCloud'da (panel/pazaryeri) oluşturulur; ERPNext bununla tracking
		bilgisini çeker. Aynı order için birden çok parcel olabilir (iptaller dahil);
		Cancelled olmayan en güncel (en büyük id) olanı seçeriz.
		"""
		if not self.enabled or not self.api_key or not self.api_secret:
			return None
		try:
			response = requests.get(
				PARCELS_URL,
				params={"order_number": order_number},
				auth=(self.api_key, self.api_secret),
				headers={"Accept": "application/json"},
			)
			response.raise_for_status()
			parcels = response.json().get("parcels", [])
		except Exception:
			show_error_alert("finding SendCloud parcel by order number")
			return None

		if not parcels:
			return None
		active = [p for p in parcels if (p.get("status") or {}).get("message") != "Cancelled"]
		pool = active or parcels
		return max(pool, key=lambda p: p.get("id") or 0)

	def get_order_integration_id(self, order):
		"""Order dict'inden integration id'yi güvenli biçimde çıkar."""
		integration = (order.get("order_details") or {}).get("integration") or {}
		integration_id = integration.get("id") or order.get("integration_id")
		return integration_id

	def update_order_measurements(self, order_id, weight=None, dimensions=None):
		"""Order'ın ağırlık/ölçü bilgilerini ERPNext değerleriyle güncelle (PATCH).

		Ship an Order (create-label) çağrısı parça ağırlığı/ölçüsünü desteklemediği
		için, label oluşturmadan ÖNCE order bu bilgilerle güncellenir.
		"""
		measurement = {}
		if weight:
			measurement["weight"] = {"value": flt(weight, WEIGHT_DECIMALS), "unit": "kg"}
		# NOT: SendCloud order measurement'ı "dimension" (TEKİL) anahtarı bekliyor
		# (order_items[].measurement.dimension ile aynı yapı), "dimensions" değil.
		if dimensions and any(flt(dimensions.get(k)) for k in ("length", "width", "height")):
			measurement["dimension"] = {
				"length": flt(dimensions.get("length") or 0),
				"width": flt(dimensions.get("width") or 0),
				"height": flt(dimensions.get("height") or 0),
				"unit": "cm",
			}
		if not measurement:
			return True

		payload = {"shipping_details": {"measurement": measurement}}
		try:
			response = requests.patch(
				f"{ORDERS_URL}/{order_id}",
				json=payload,
				auth=(self.api_key, self.api_secret),
				headers={"Accept": "application/json", "Content-Type": "application/json"},
			)
			if response.status_code >= 400:
				frappe.log_error(
					message=f"PATCH {ORDERS_URL}/{order_id}\n{json.dumps(payload)}\n\n{response.text}",
					title="SendCloud Order Update Error",
				)
				return False
			return True
		except Exception:
			show_error_alert("updating SendCloud order measurements")
			return False

	def update_order_details(self, order_id, order, item_weights=None, notes=None):
		"""Order'ın order_details'ını güncelle: notes (Delivery notes) + order_items
		birim ağırlıkları (Unit weight).

		- notes: SendCloud'daki "Delivery notes" alanı = order_details.notes (string).
		- item_weights: {sku: birim_ağırlık_kg}. Her order_item'ın measurement.weight'i
		  ERPNext birim ağırlığıyla güncellenir (SKU=item_code ile eşleşir).

		Measurement (parcel weight/dimension) PATCH'inden AYRI tutulur ki kanıtlanmış
		o akış bu deneysel alanlardan etkilenmesin.
		"""
		order_details = {}
		if notes is not None:
			order_details["notes"] = notes

		if item_weights:
			existing_items = (order.get("order_details") or {}).get("order_items") or []
			new_items, changed = [], False
			for it in existing_items:
				it = dict(it)
				unit_w = item_weights.get(it.get("sku"))
				if unit_w:
					meas = dict(it.get("measurement") or {})
					meas["weight"] = {"value": flt(unit_w, WEIGHT_DECIMALS), "unit": "kg"}
					it["measurement"] = meas
					changed = True
				new_items.append(it)
			if changed:
				order_details["order_items"] = new_items

		if not order_details:
			return True

		payload = {"order_details": order_details}
		try:
			response = requests.patch(
				f"{ORDERS_URL}/{order_id}",
				json=payload,
				auth=(self.api_key, self.api_secret),
				headers={"Accept": "application/json", "Content-Type": "application/json"},
			)
			if response.status_code >= 400:
				frappe.log_error(
					message=f"PATCH {ORDERS_URL}/{order_id}\n{json.dumps(payload, default=str)}\n\n{response.text}",
					title="SendCloud Order Details Update Error",
				)
				return False
			return True
		except Exception:
			show_error_alert("updating SendCloud order details")
			return False

	def ship_order(
		self,
		order,
		shipping_option_code=None,
		contract_id=None,
		weight=None,
		dimensions=None,
		item_weights=None,
		notes=None,
	):
		"""Mevcut bir SendCloud order'ı ERPNext bilgileriyle sevk et (label oluştur).

		Akış: (1) order ağırlık/ölçüsünü güncelle, (2) create-label-sync ile label
		oluştur (Shipping method = shipping_option_code, Enabled contract = contract_id).
		Pazaryeri (Amazon/Bol/Shopify) bağı korunur; teslim feedback'i SendCloud'dan akar.

		Returns: shipment_info dict (label_file base64 dahil) ya da None.
		"""
		if not self.enabled or not self.api_key or not self.api_secret:
			return None

		order_id = order.get("id")
		order_number = order.get("order_number")
		integration_id = self.get_order_integration_id(order)
		if not integration_id:
			frappe.throw(_("Could not determine the SendCloud integration for this order."))

		# 1) Parcel weight/dimension (shipping_details.measurement) — kanıtlanmış akış
		self.update_order_measurements(order_id, weight=weight, dimensions=dimensions)
		# 1b) Delivery notes + Unit weight (order_details) — ayrı/best-effort PATCH
		self.update_order_details(order_id, order, item_weights=item_weights, notes=notes)

		# 2) Label oluştur (create-label-sync)
		# NOT: ship_with, Shipments API ile aynı yapıda olmalı: {type, properties}.
		# contract, properties içinde "contract_id" anahtarıyla (integer) gönderilir.
		# Bu endpoint top-level "label" kabul etmiyor (varsayılan PDF döner).
		# Order referansı: order_number kullan (dahili `id` create-label'da 404 verir).
		# Order'ı zaten order_number=po_no ile bulduğumuz için bu güvenilir; yoksa
		# order'ın kendi `order_id` (harici/pazaryeri) alanına düş.
		if order_number:
			order_ref = {"order_number": str(order_number)}
		elif order.get("order_id"):
			order_ref = {"order_id": str(order.get("order_id"))}
		else:
			order_ref = {"order_id": str(order_id)}
		payload = {
			"integration_id": int(integration_id),
			"order": order_ref,
		}
		if shipping_option_code:
			# NOT: Ship an Order, Shipments API'den farklı olarak properties içinde
			# "contract" değil "contract_id" anahtarını bekler.
			properties = {"shipping_option_code": shipping_option_code}
			if contract_id:
				# API contract_id'yi integer bekliyor (Data alanından string gelebilir)
				try:
					properties["contract_id"] = int(contract_id)
				except (TypeError, ValueError):
					properties["contract_id"] = contract_id
			payload["ship_with"] = {"type": "shipping_option_code", "properties": properties}
		brand_id = self.get_brand_id()
		if brand_id:
			payload["brand_id"] = brand_id

		try:
			response = requests.post(
				CREATE_LABEL_SYNC_URL,
				json=payload,
				auth=(self.api_key, self.api_secret),
				headers={"Accept": "application/json", "Content-Type": "application/json"},
			)
			response_data = response.json()
		except Exception:
			show_error_alert("shipping SendCloud order")
			return None

		if response.status_code >= 400 or (isinstance(response_data, dict) and response_data.get("errors")):
			frappe.log_error(
				message=f"POST {CREATE_LABEL_SYNC_URL}\n{json.dumps(payload)}\n\n"
				+ json.dumps(response_data, indent=2, default=str),
				title="SendCloud Ship an Order Error",
			)
			errors = response_data.get("errors") if isinstance(response_data, dict) else None
			if errors:
				parts = []
				for e in errors:
					if isinstance(e, dict):
						pointer = (e.get("source") or {}).get("pointer", "")
						parts.append(f"{pointer + ': ' if pointer else ''}{e.get('detail', e)}")
					else:
						parts.append(str(e))
				msg = "; ".join(parts)
			else:
				msg = json.dumps(response_data, default=str)[:500]
			frappe.throw(_("SendCloud Ship an Order failed: {0}").format(msg))

		# Yanıt çeşitli biçimlerde gelebilir: {"data": {...}}, {"data": [ {...} ]},
		# ya da JSON:API {"data": [{"attributes": {...}}]}. Hepsini normalize et.
		data = response_data.get("data") if isinstance(response_data, dict) else response_data
		if data is None:
			data = response_data
		if isinstance(data, list):
			data = data[0] if data else {}
		if not isinstance(data, dict):
			data = {}
		if isinstance(data.get("attributes"), dict):
			data = data["attributes"]

		parcel = data.get("parcel")
		if not isinstance(parcel, dict):
			parcel = data
		parcel_id = parcel.get("parcel_id") or parcel.get("id")
		label = parcel.get("label") or data.get("label") or {}

		# Hiç parcel_id bulunamazsa yanıtı logla ki yapıyı görebilelim
		if not parcel_id:
			frappe.log_error(
				message=json.dumps(response_data, indent=2, default=str),
				title="SendCloud Ship an Order - unparsed response",
			)

		# Gerçek carrier'ı belirle: yanıttaki carrier > option kodu öneki > "SendCloud".
		# (Ship an Order yanıtı genelde carrier döndürmüyor; o yüzden koddan türetiyoruz.)
		carrier = parcel.get("carrier") or {}
		carrier_name = (
			carrier.get("name")
			or carrier_display_from_code(shipping_option_code)
			or carrier.get("code")
			or "SendCloud"
		)
		return {
			"service_provider": "SendCloud",
			"shipment_id": str(parcel_id) if parcel_id else "",
			"awb_number": parcel.get("tracking_number") or "",
			"tracking_url": parcel.get("tracking_url") or "",
			"carrier": carrier_name,
			"carrier_service": (parcel.get("shipment") or {}).get("name") or shipping_option_code or "",
			"label_file": label.get("file"),
			"label_mime_type": label.get("mime_type"),
		}

	def get_parcel(self, parcel, shipment, index, shipment_items_data=None, parcel_item_map=None):
		"""Parcel verilerini SendCloud API formatında hazırla.

		`index`, Shipment Parcel tablosundaki koli sıra numarasıdır (1-based) ve
		`parcel_item_map`'teki 'Parcel No' ile eşleşir.
		"""
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

		# 1) Koli-bazlı eşleştirme aktifse: her koliye SADECE kendi ürün/adetleri
		if parcel_item_map:
			if index in parcel_item_map and shipment_items_data:
				item_info = shipment_items_data.get("item_info") or {}
				currency = shipment_items_data.get("currency", "EUR")
				parcel_items, label_notes = self.build_parcel_items_from_map(
					parcel_item_map[index], item_info, currency
				)
				if parcel_items:
					parcel_data["parcel_items"] = parcel_items
					if label_notes:
						parcel_data["label_notes"] = label_notes
			# Eşleştirme modunda haritada olmayan koli ürünsüz kalır
			return parcel_data

		# 2) Eşleştirme yoksa: eski davranış (tüm ürünler her koliye)
		if shipment_items_data and shipment_items_data.get("parcel_items"):
			parcel_data["parcel_items"] = shipment_items_data["parcel_items"]

			# Label notes (SKU bilgileri)
			if shipment_items_data.get("label_notes"):
				parcel_data["label_notes"] = shipment_items_data["label_notes"]

		return parcel_data

	def get_parcel_item_map(self, shipment_doc):
		"""custom_parcel_items child tablosundan koli -> {item_code: qty} haritası çıkar.

		Returns: {parcel_no(int): {item_code: qty}}. Tablo boşsa boş dict döner
		ve eski (tüm ürünler her koliye) davranışı devreye girer.
		"""
		parcel_item_map = {}
		for row in shipment_doc.get("custom_parcel_items") or []:
			if not row.get("item_code") or not row.get("parcel_no"):
				continue
			qty = flt(row.qty)
			if qty <= 0:
				continue
			parcel_no = int(row.parcel_no)
			parcel_item_map.setdefault(parcel_no, {})
			parcel_item_map[parcel_no][row.item_code] = (
				parcel_item_map[parcel_no].get(row.item_code, 0) + qty
			)
		return parcel_item_map

	def build_parcel_items_from_map(self, items_qty: dict, item_info: dict, currency="EUR"):
		"""Bir koli için {item_code: qty} -> (SendCloud parcel_items, label_notes).

		Birim fiyat/ağırlık DN'den türetilen `item_info`'dan alınır ve atanan
		adetle çarpılır; DN'de yoksa Item master'dan fallback yapılır.
		"""
		parcel_items = []
		label_notes = []

		for item_code, qty in items_qty.items():
			info = item_info.get(item_code) or self.get_item_info_fallback(item_code, currency)
			qty_int = int(qty)

			unit_weight = flt(info.get("unit_weight", 0))
			item = {
				"description": (info.get("description") or item_code)[:200],
				"quantity": qty_int,
				"price": {
					"value": flt(info.get("unit_price", 0) * qty, CURRENCY_DECIMALS),
					"currency": info.get("currency") or currency,
				},
				"weight": {
					"value": flt(unit_weight * qty, WEIGHT_DECIMALS) if unit_weight > 0 else 0.1,
					"unit": "kg",
				},
				"sku": item_code[:50],
			}
			if info.get("hs_code"):
				item["hs_code"] = info["hs_code"][:20]
			if info.get("origin_country"):
				item["origin_country"] = info["origin_country"]
			parcel_items.append(item)

			# Label notes: SKU[adet], max 50 karakter
			note = f"{item_code}[{qty_int}]"
			if len(note) > 50:
				note = f"{item_code[:45]}[{qty_int}]"
			label_notes.append(note)

		return parcel_items, label_notes

	def get_item_info_fallback(self, item_code, currency="EUR"):
		"""DN'de bulunamayan ürün için Item master'dan birim bilgi al."""
		info = {"description": item_code, "unit_price": 0, "unit_weight": 0, "currency": currency}
		try:
			item_doc = frappe.get_cached_doc("Item", item_code)
			info["description"] = item_doc.item_name or item_code
			info["unit_price"] = flt(item_doc.get("standard_rate") or 0)
			info["unit_weight"] = flt(item_doc.get("weight_per_unit") or 0)
			if item_doc.customs_tariff_number:
				info["hs_code"] = item_doc.customs_tariff_number
			if item_doc.country_of_origin:
				country_code = frappe.db.get_value("Country", item_doc.country_of_origin, "code")
				if country_code:
					info["origin_country"] = country_code.upper()
		except Exception:
			pass
		return info

	def get_shipment_items(self, shipment):
		"""
		Shipment'a bağlı Delivery Note'lardan item bilgilerini al.
		SendCloud API formatında parcel_items listesi döndürür.

		`shipment` parametresi Shipment adı (str) ya da doc olabilir.

		Returns:
			dict: {
				"parcel_items": [...],          # tüm ürünler (eşleştirme yoksa fallback)
				"order_number": "PO veya SO numarası",
				"label_notes": ["SKU[qty]", ...],
				"total_value": toplam değer,
				"item_info": {item_code: {unit_price, unit_weight, ...}},  # koli eşleştirmesi için
				"currency": "EUR"
			}
		"""
		items_dict = {}  # SKU bazlı birleştirme için
		sku_qty_dict = {}  # Label notes için SKU -> toplam adet
		order_number = None
		total_value = 0
		default_currency = "EUR"
		shipment_name = shipment if isinstance(shipment, str) else shipment.name

		try:
			shipment_doc = frappe.get_doc("Shipment", shipment) if isinstance(shipment, str) else shipment

			# Shipment'a bağlı Delivery Note'ları kontrol et
			delivery_notes = shipment_doc.get("shipment_delivery_note", [])

			if not delivery_notes:
				# Delivery Note yoksa None döndür
				return None

			seen_dns = set()
			for dn_row in delivery_notes:
				# Aynı DN birden fazla listelense bile bir kez işle (adet katlanmasın)
				if not dn_row.delivery_note or dn_row.delivery_note in seen_dns:
					continue
				seen_dns.add(dn_row.delivery_note)

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
					sku = item.item_code

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

			# item_info: koli-bazlı eşleştirme için birim fiyat/ağırlık türet
			item_info = {}
			for code, data in items_dict.items():
				dn_qty = data["quantity"] or 1
				item_info[code] = {
					"description": data["description"],
					"unit_price": flt(data["price"]["value"]) / dn_qty if dn_qty else 0,
					"unit_weight": flt(data["weight"]["value"]) / dn_qty if dn_qty else 0,
					"currency": default_currency,
					"hs_code": data.get("hs_code"),
					"origin_country": data.get("origin_country"),
					"dn_qty": data["quantity"],
				}

			return {
				"parcel_items": list(items_dict.values()),
				"order_number": order_number,
				"label_notes": label_notes if label_notes else None,
				"total_value": total_value,
				"item_info": item_info,
				"currency": default_currency,
			}

		except Exception as e:
			frappe.log_error(
				message=f"Error getting shipment items for {shipment_name}: {str(e)}",
				title="SendCloud - Get Shipment Items Error"
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