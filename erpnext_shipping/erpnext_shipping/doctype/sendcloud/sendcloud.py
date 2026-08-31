# Copyright (c) 2020, Frappe Technologies Pvt. Ltd. and contributors
# For license information, please see license.txt

import json
import re
from datetime import datetime

import frappe
import requests
from frappe import _
from frappe.model.document import Document
from frappe.utils import cint, flt
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
# Parselin tam durum merdiveni. Parcel nesnesi yalnız o anki durumu taşıyor; bu
# uç nokta geçmişe dönük çalışıyor ve webhook'a bağlı değil.
TRACKING_URL = f"{BASE_URL}/v2/tracking"
CONTRACTS_URL = f"{BASE_URL}/v3/contracts"
ORDERS_URL = f"{BASE_URL}/v3/orders"
CREATE_LABEL_SYNC_URL = f"{BASE_URL}/v3/orders/create-label-sync"
# İade ürünleri yalnız v2'de listeleniyor ve sayısal id ile çalışıyor.
SHIPPING_METHODS_URL = f"{BASE_URL}/v2/shipping_methods"

# SendCloud, tarihleri GÜN-AY-YIL ("21-07-2026 14:46:45") verir. frappe.get_datetime
# bunu belirsiz olduğunda AY-GÜN sanıp (gün<=12) yanlış tarihe çeviriyor -> teslim
# tarihi ve Transit Days bozuluyor. Bu yüzden burada kesin (gün-önce) parse edip
# tek tip ISO ("YYYY-MM-DD HH:MM:SS") string döndürüyoruz.
_SENDCLOUD_DT_FORMATS = ("%d-%m-%Y %H:%M:%S", "%d-%m-%Y %H:%M", "%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S")

# Bir daha hareket etmeyecek parça durumları. Etiket taşıyıcıya hiç ulaşmamış ya da
# geri çekilmiş; teslim edilmesi mümkün değil. Gönderi durumunu hesaplarken sayılmaz,
# yoksa tek ölü etiket bütün gönderiyi kalıcı olarak "In Progress"te tutar.
_DEAD_PARCEL_STATUSES = ("announcement failed", "cancelled", "cancellation requested")


def _is_dead_parcel(status):
	"""Bu parça durumundan teslimat çıkabilir mi?"""
	low = (status or "").strip().lower()
	for dead in _DEAD_PARCEL_STATUSES:
		if dead in low:
			return True
	return False


def _service_price(service):
	"""Sort key: cheapest first. Nameless price means last, not free."""
	return flt(service.get("total_price")) or float("inf")


def format_sendcloud_errors(errors):
	"""Readable text from SendCloud's error list, with the field that failed.

	SendCloud puts the offending field in `source.pointer`; without it the
	message is "This field cannot be blank." and gives no way to act on it.
	"""
	parts = []
	for err in errors or []:
		if not isinstance(err, dict):
			parts.append(str(err))
			continue
		pointer = (err.get("source") or {}).get("pointer") or ""
		detail = err.get("detail") or err.get("code") or str(err)
		parts.append(f"{pointer}: {detail}" if pointer else str(detail))
	return "\n".join(parts)


def parse_sendcloud_datetime(value):
	"""Parse a SendCloud date string (day-first) into an ISO 'YYYY-MM-DD HH:MM:SS'
	string. Returns None for empty; leaves already-ISO / unknown values as-is."""
	if not value:
		return None
	if isinstance(value, datetime):
		return value.strftime("%Y-%m-%d %H:%M:%S")
	text = str(value).strip()
	for fmt in _SENDCLOUD_DT_FORMATS:
		try:
			return datetime.strptime(text, fmt).strftime("%Y-%m-%d %H:%M:%S")
		except ValueError:
			continue
	return text

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
def toggle_preferred_shipping_option(
	code, service_label=None, carrier=None, contract_id=None, contract_label=None
):
	"""Bir SendCloud shipping option kodunu favorilere ekle/çıkar.

	Fetch Shipping Rates penceresindeki yıldız butonundan çağrılır. SendCloud
	ayarları yalnızca System Manager'a açık olduğundan, kaydı izin atlayarak
	yapar (yalnızca favori listesini günceller).

	Sözleşme de kaydedilir: teklif listesi yalnız varsayılan sözleşmeyi getirdiği
	için, favoriye alınmış bir seçenek sözleşmesiyle birlikte saklanmazsa her
	seferinde elle seçilmek zorunda kalır.
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
			{
				"shipping_option_code": code,
				"service_label": service_label,
				"carrier": carrier,
				"contract_id": contract_id or "",
				"contract_label": contract_label or "",
			},
		)
		preferred = True

	settings.save(ignore_permissions=True)
	return {"preferred": preferred}


@frappe.whitelist()
def sender_company_name(shipment=None):
	"""Kargo etiketine basilacak gonderici firma adi.

	Adres basligi bir cadde adi gibi gorunuyorsa firma adi oradan okunamiyor ve
	baska bir kaynak gerekiyor. Sira: gonderinin KENDI sirketi, sonra sitenin
	varsayilan sirketi, sonra tek sirket kaydi.

	Hicbiri yoksa **bos donuyor**. Eskiden burada sabit bir firma adi vardi ve o
	ad, baska bir kurulumda baskasinin kargo etiketine basilirdi. Eksik bir isim
	yanlis bir isimden iyidir: eksik olan fark edilir, yanlis olan edilmez.
	"""
	if shipment:
		company = frappe.db.get_value("Shipment", shipment, "pickup_company")
		if company:
			return company

	return (
		frappe.defaults.get_global_default("company")
		or frappe.db.get_value("Company", {}, "name", order_by="creation")
		or ""
	)


def return_contract_ids():
	"""İade ürünleri hangi sözleşmeler için sorulacak.

	Tercihli işaretlenmiş sözleşmeler varsa yalnız onlar, yoksa hepsi. Ayarda
	hiç sözleşme yoksa boş liste döner ve çağrı eskisi gibi sözleşmesiz yapılır;
	tek sözleşmeli hesaplarda o çalışıyor.
	"""
	settings = frappe.get_cached_doc("SendCloud", "SendCloud")
	tercihli = []
	tumu = []
	for row in settings.get("contract_options") or []:
		if not row.contract_id:
			continue
		cid = str(row.contract_id)
		if cid not in tumu:
			tumu.append(cid)
		if cint(row.is_preferred) and cid not in tercihli:
			tercihli.append(cid)
	return tercihli or tumu


def fetch_return_methods(api_key, api_secret):
	"""(yöntemler, hatalar) — iade ürünleri listesi.

	SendCloud bir taşıyıcı için birden çok etkin sözleşme varken sözleşmesiz
	sorguyu reddediyor:

	    "You have multiple active contracts for that carrier.
	     Please specify the contract in the query parameters."

	Tek çağrıyla hepsini almanın yolu yok; sözleşme başına bir çağrı yapılıp
	sonuçlar `id` üzerinden birleştiriliyor. Bir sözleşme düşerse diğerleri
	devam ediyor ve düşen ayrıca bildiriliyor: eksik bir liste, sessizce eksik
	kalmamalı.
	"""
	sozlesmeler = return_contract_ids()
	istekler = []
	if sozlesmeler:
		for cid in sozlesmeler:
			istekler.append({"is_return": "true", "contract": cid})
	else:
		istekler.append({"is_return": "true"})

	yontemler = {}
	hatalar = []
	for params in istekler:
		try:
			response = requests.get(
				SHIPPING_METHODS_URL,
				params=params,
				auth=(api_key, api_secret),
				timeout=30,
			)
			if response.status_code >= 400:
				hatalar.append(f'{params.get("contract") or "-"}: {response.text[:200]}')
				continue
			for method in (response.json() or {}).get("shipping_methods", []):
				mid = str(method.get("id"))
				if mid and mid not in yontemler:
					yontemler[mid] = method
		except Exception as e:
			hatalar.append(f'{params.get("contract") or "-"}: {str(e)[:200]}')

	return list(yontemler.values()), hatalar


def sync_sendcloud_return_methods():
	"""Pull the account's return products in, leaving the choice to a person.

	SendCloud lists contract **price bands** — `DPD Return 6-8kg`, `DPD Classic
	10-20kg` — beside real products, and nothing in the data separates them: they
	carry the same fields and the same country lists. Buying a label against a
	band is refused with `Invalid shipment.id`, and the only way to find out is
	to try.

	So the list is fetched and nothing is inferred from it. Which products get
	offered is ticked here, the same way contracts are named here — a decision
	somebody made, written down where it can be read, rather than a rule guessed
	from the shape of a product name.
	"""
	utils = SendCloudUtils()
	methods, hatalar = fetch_return_methods(utils.api_key, utils.api_secret)
	if hatalar:
		frappe.msgprint(
			_("Some contracts could not be read, so the list may be short:<br><br>{0}").format(
				"<br>".join(hatalar)
			),
			title=_("Return products partly read"),
			indicator="orange",
		)
	if not methods:
		frappe.throw(
			_("SendCloud returned no return products. If the message mentions several active contracts, tick the one you use in <b>Contracts</b> below and try again."),
			title=_("No return products"),
		)

	settings = frappe.get_doc("SendCloud", "SendCloud")
	existing = {}
	for row in settings.return_options or []:
		if row.method_id:
			existing[str(row.method_id)] = row

	seen = set()
	added = 0
	for method in methods:
		key = str(method.get("id"))
		seen.add(key)
		row = existing.get(key)
		if not row:
			row = settings.append("return_options", {"method_id": key})
			added += 1
		row.carrier = method.get("carrier") or ""
		row.method_name = method.get("name") or ""
		row.weight_range = f"{flt(method.get('min_weight')):g}-{flt(method.get('max_weight')):g} kg"

	# Hesaptan kalkan ürünler silinmiyor: işaretlenmiş bir ürün geçmiş bir
	# gönderide kullanılmış olabilir ve satırın kaybolması onu da götürür.
	stale = []
	for row in settings.return_options or []:
		if str(row.method_id) not in seen:
			stale.append(str(row.method_id))

	settings.save(ignore_permissions=True)
	return {"total": len(seen), "added": added, "stale": stale}


@frappe.whitelist()
def sync_sendcloud_contracts():
	"""Pull the account's contracts into SendCloud settings, keeping own names.

	The names live in ERPNext rather than at SendCloud because SendCloud has no
	field for them: its own ("broker") contracts come back nameless, so a carrier
	with three of them renders as three identical labels.
	"""
	settings = frappe.get_doc("SendCloud", "SendCloud")
	existing = {}
	for row in settings.contract_options or []:
		if row.contract_id:
			existing[str(row.contract_id)] = row

	seen = set()
	added = 0
	for c in SendCloudUtils().get_contracts(apply_own_labels=False):
		cid = str(c.get("id"))
		seen.add(cid)
		row = existing.get(cid)
		if not row:
			row = settings.append("contract_options", {"contract_id": cid})
			added += 1
		row.carrier = c.get("carrier_name") or ""
		row.contract_type = c.get("type") or ""
		row.state = c.get("state") or ""
		if not row.own_label:
			row.own_label = c.get("name") or ""

	# Hesaptan kalkmış sözleşmeleri silme — geçmiş gönderiler onlara atıfta
	# bulunuyor ve tazminat talebi eski bir etiket için açılabiliyor.
	stale = [str(r.contract_id) for r in (settings.contract_options or []) if str(r.contract_id) not in seen]

	settings.save(ignore_permissions=True)
	return {"total": len(seen), "added": added, "stale": stale}


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
			from_address["company_name"] = sender_company_name(shipment)
		
		if pickup_contact.phone:
			from_address["phone_number"] = pickup_contact.phone
		if pickup_contact.email_id:
			from_address["email"] = pickup_contact.email_id

		# Order number: Önce PO, yoksa SO, yoksa Shipment adı
		api_order_number = shipment
		if shipment_items_data and shipment_items_data.get("order_number"):
			api_order_number = shipment_items_data["order_number"]

		ship_with_properties = {"shipping_option_code": service_info["service_id"]}
		# Sözleşme seçimi (panel'deki "Enabled contract"): varsa açıkça gönder.
		# Shipment üzerindeki elle seçim teklifin sözleşmesini ezer — teklif uç
		# noktası yalnız varsayılan sözleşmeleri döndürdüğü için broker sözleşme
		# ("Sendcloud rates") ancak böyle seçilebiliyor.
		contract_override = frappe.db.get_value(
			"Shipment", shipment, "custom_sendcloud_contract_id"
		) if shipment else None
		contract = self._contract_property(
			ship_with_properties, contract_override or service_info.get("contract_id")
		)

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
					frappe.msgprint(
						_("SendCloud rejected shipment {0}:").format(api_order_number)
						+ f"\n{format_sendcloud_errors(response_data['errors'])}",
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
						"contract_id": self._used_contract(contract, parcels_data),
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
						# Koli numarasıyla söyle. Eskiden parcel.get("order_number")
						# yazılıyordu; parça sözlüğünde o anahtar yok, mesaj hep
						# "for parcel None" diyordu ve hangi kolinin reddedildiği
						# belli olmuyordu.
						frappe.msgprint(
							_("SendCloud rejected parcel {0} of {1}:").format(
								parcels.index(parcel) + 1, api_order_number
							)
							+ f"\n{format_sendcloud_errors(response_data['errors'])}",
							indicator="red",
							alert=True,
						)
						continue

					parcels_data = response_data.get("data", {}).get("parcels", [])
					if parcels_data:
						parcel_data = parcels_data[0]
						used_contract = self._used_contract(contract, parcels_data)
						shipments_results.append(
							{
								"shipment_id": str(parcel_data["id"]),
								"awb_number": parcel_data.get("tracking_number", ""),
								"tracking_url": parcel_data.get("tracking_url", ""),
								"carrier": self.get_carrier(service_info["carrier"], post_or_get="post"),
								"carrier_service": service_info["service_name"],
								"shipment_amount": service_info.get("total_price") or 0,
								"contract_id": used_contract,
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
					"contract_id": shipments_results[0].get("contract_id"),
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
		else:
			# Numara bulunamazsa alan hiç gönderilmiyordu ve SendCloud "This field
			# cannot be blank" diyordu — hangi alan, hangi adres, belli değil. Hata
			# aynı hata, ama artık düzeltilecek yeri söylüyor.
			frappe.throw(
				_(
					"No house number could be read from the delivery address {0}: "
					"'{1}'. SendCloud requires it. Put the number in Address Line 1 "
					"separated by a space, e.g. 'Musterstraße 12'."
				).format(
					delivery_address.get("name") or "",
					delivery_address.address_line1 or "",
				)
			)
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
			from_address["company_name"] = sender_company_name(shipment)
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
				# Koli satırında sözleşme seçilmemişse Shipment başlığındaki seçime düş.
				contract = self._contract_property(
					ship_with_properties,
					service.get("contract_id") or shipment_doc.get("custom_sendcloud_contract_id"),
				)

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
								"contract_id": self._used_contract(contract, parcels_data),
							}
						)
				except Exception:
					show_error_alert(f"creating SendCloud per-parcel shipment {i}")

		if not results:
			return None

		carriers = sorted({r["carrier"] for r in results})
		services = sorted({r["carrier_service"] for r in results})
		# Koliler ayrı sözleşmelerle gidebilir; hepsini yaz, biri seçilmiş gibi
		# görünmesin. Koli bazındaki ayrıntı zaten parça satırlarında duruyor.
		contracts = sorted({str(r["contract_id"]) for r in results if r.get("contract_id")})
		return {
			"service_provider": "SendCloud",
			"shipment_id": ", ".join(r["shipment_id"] for r in results if r.get("shipment_id")),
			"carrier": ", ".join(carriers),
			"carrier_service": ", ".join(services),
			"shipment_amount": sum(flt(r.get("shipment_amount")) for r in results),
			"awb_number": ", ".join(r["awb_number"] for r in results if r.get("awb_number")),
			"tracking_url": ", ".join(r["tracking_url"] for r in results if r.get("tracking_url")),
			"contract_id": ", ".join(contracts),
		}

	# ------------------------------------------------------------------
	# İade etiketleri — ayrı bir API dünyası
	# ------------------------------------------------------------------
	#
	# İade ürünleri v2'de yaşıyor ve sayısal `id` ile çalışıyor; günlük teklif
	# akışı ise v3'te ve metin `code` kullanıyor. İkisi birbirinin yerine
	# geçmediği için iade kendi yolundan gidiyor — v3 tarafına dokunulmuyor.

	def get_return_services(self, pickup_address, parcels: list[dict]):
		"""Return products that can collect from this address, heaviest parcel first.

		SendCloud lists a return method against the countries it can be **sent
		from**, not sent to: `DPD Return 10-20kg` carries only Germany while our
		own addresses are Belgian. So the filter is the customer's country.

		Price does not come from the `price` field, which reads 0. The real amount
		is the sum of `price_breakdown` for that country — label plus fuel
		surcharges — and a quote that showed 0 would be a quote nobody could
		check against an invoice.
		"""
		if not self.enabled or not self.api_key or not self.api_secret:
			return []

		origin = (pickup_address.get("country_code") or "").upper()

		weight = 0
		count = 0
		for parcel in parcels:
			weight = max(weight, flt(parcel.get("weight", 0)))
			count += int(parcel.get("count", 1) or 1)

		chosen = self.get_return_options()
		if not chosen:
			frappe.msgprint(
				_("No return products are ticked in SendCloud Settings, so there is nothing to offer. Press <b>Sync Return Products</b> there and tick the ones you use."),
				title=_("No return products chosen"),
				indicator="orange",
			)
			return []

		methods, hatalar = fetch_return_methods(self.api_key, self.api_secret)
		if hatalar:
			# Sessizce boş dönmek, "iade ürünü yok" gibi okunuyordu ve sebebi
			# yalnızca hata kaydına bakan biri görebiliyordu.
			frappe.msgprint(
				_("SendCloud could not be read for some contracts:<br><br>{0}").format(
					"<br>".join(hatalar)
				),
				title=_("Return options may be incomplete"),
				indicator="orange",
			)
		if not methods:
			return []

		services = []
		for method in methods:
			if str(method.get("id")) not in chosen:
				continue

			# Servis noktası isteyen ürünler burada seçilemez: hangi noktaya
			# bırakılacağını müşteri seçer, biz bilmiyoruz.
			if (method.get("service_point_input") or "none") != "none":
				continue

			low = flt(method.get("min_weight") or 0)
			high = flt(method.get("max_weight") or 0)
			if weight and high and not (low <= weight <= high):
				continue

			# Fiyat müşterinin ülkesinin satırından okunuyor; o satır yoksa fiyat
			# **boş** bırakılıyor. `countries` alanının neyi anlattığı belirsiz —
			# BE listeleyen bir ürün reddedilirken BE listeleyen bir başkası kabul
			# edildi — ve belirsiz bir alandan türetilmiş bir tutar, faturayla
			# karşılaştırıldığında tutmayan bir tutardır.
			price = None
			for entry in method.get("countries") or []:
				if (entry.get("iso_2") or "").upper() != origin:
					continue
				price = 0
				for part in entry.get("price_breakdown") or []:
					price += flt(part.get("value"))
				break

			service = frappe._dict()
			service.service_provider = SENDCLOUD_PROVIDER
			service.carrier = (method.get("carrier") or "").upper()
			service.carrier_code = method.get("carrier")
			service.service_name = f"{method.get('name')} ({low:g}-{high:g} kg)"
			service.service_id = str(method.get("id"))
			service.is_return = True
			service.total_price = None if price is None else price * (count or 1)
			service.currency = "EUR"
			services.append(service)

		services.sort(key=_service_price)
		return services

	def get_return_options(self):
		"""Ids of the return products somebody ticked, as strings."""
		chosen = set()
		settings = frappe.get_single("SendCloud")
		for row in settings.get("return_options") or []:
			if row.enabled and row.method_id:
				chosen.add(str(row.method_id))
		return chosen

	def create_return_shipment(
		self,
		shipment,
		pickup_address,
		pickup_contact,
		delivery_address,
		delivery_contact,
		service_info,
		shipment_parcel,
	):
		"""Buy a return label: the customer sends, we receive.

		On a return parcel the plain address fields are the **recipient** — us —
		and the customer goes in the `from_*` fields. That is the opposite of an
		ordinary parcel and SendCloud enforces it: without `from_*` it refuses the
		call outright.

		Made in two steps anyway. The parcel is created **without** a label and
		its addresses are read back; the label is bought only once SendCloud
		agrees the parcel leaves the customer and arrives at us. A return built
		the wrong way round is a parcel posted to the customer at our expense,
		and nobody finds out until it turns up on their doorstep.
		"""
		our_number, our_street = self.extract_house_number(delivery_address.address_line1)
		their_number, their_street = self.extract_house_number(pickup_address.address_line1)
		if not their_number:
			frappe.throw(
				_("No house number could be read from the customer address {0}. A return label needs one.").format(
					pickup_address.address_line1
				)
			)
		if not our_number:
			frappe.throw(
				_("No house number could be read from the return address {0}.").format(
					delivery_address.address_line1
				)
			)

		parcels = json.loads(shipment_parcel) if isinstance(shipment_parcel, str) else shipment_parcel
		weight = 0
		for parcel in parcels or []:
			weight = max(weight, flt(parcel.get("weight", 0)))

		their_name = f"{pickup_contact.first_name or ''} {pickup_contact.last_name or ''}".strip()
		our_name = frappe.defaults.get_global_default("company") or delivery_address.get("address_title")

		body = {
			# Alıcı: biz.
			"name": our_name,
			"company_name": our_name,
			"address": our_street or delivery_address.address_line1,
			"house_number": our_number,
			"city": delivery_address.city,
			"postal_code": delivery_address.pincode,
			"country": (delivery_address.country_code or "").upper(),
			# Gönderen: müşteri.
			"from_name": their_name or pickup_address.get("address_title") or shipment,
			"from_address_1": their_street or pickup_address.address_line1,
			"from_house_number": their_number,
			"from_city": pickup_address.city,
			"from_postal_code": pickup_address.pincode,
			"from_country": (pickup_address.country_code or "").upper(),
			"order_number": shipment,
			"weight": f"{weight or 1:.3f}",
			"is_return": True,
			"request_label": False,
			"shipment": {"id": int(service_info["service_id"])},
		}
		if pickup_contact.get("email_id"):
			body["from_email"] = pickup_contact.email_id
		if pickup_contact.get("phone"):
			body["from_telephone"] = pickup_contact.phone
		if delivery_contact and delivery_contact.get("email_id"):
			body["email"] = delivery_contact.email_id
		if delivery_contact and delivery_contact.get("phone"):
			body["telephone"] = delivery_contact.phone

		created = self._post_parcel({"parcel": body})
		parcel_id = created.get("id")

		# Doğrulama: paket müşteriden çıkıp bize varmalı. Ters kurulmuş bir iade
		# burada yakalanır ve hiçbir etiket satın alınmamış olur.
		arrives = ((created.get("country") or {}).get("iso_2") or "").upper()
		leaves = (created.get("from_country") or "").upper()
		if arrives and arrives != body["country"]:
			frappe.throw(
				_("SendCloud built the return arriving in {0}, not {1}. No label was bought; parcel {2} can be cancelled in SendCloud.").format(
					arrives, body["country"], parcel_id
				),
				title=_("Return built the wrong way round"),
			)
		if leaves and leaves != body["from_country"]:
			frappe.throw(
				_("SendCloud built the return leaving from {0}, not {1}. No label was bought; parcel {2} can be cancelled in SendCloud.").format(
					leaves, body["from_country"], parcel_id
				),
				title=_("Return built the wrong way round"),
			)

		labelled = self._put_parcel({"parcel": {"id": parcel_id, "request_label": True}})

		return {
			"service_provider": SENDCLOUD_PROVIDER,
			"carrier": service_info.get("carrier"),
			"carrier_service": service_info.get("service_name"),
			"shipment_id": str(parcel_id),
			"awb_number": labelled.get("tracking_number") or created.get("tracking_number"),
			"tracking_url": labelled.get("tracking_url") or created.get("tracking_url"),
			"shipment_amount": service_info.get("total_price"),
		}

	def _post_parcel(self, payload):
		response = requests.post(
			PARCELS_URL, json=payload, auth=(self.api_key, self.api_secret), timeout=60
		)
		return self._parcel_or_throw(response)

	def _put_parcel(self, payload):
		response = requests.put(
			PARCELS_URL, json=payload, auth=(self.api_key, self.api_secret), timeout=60
		)
		return self._parcel_or_throw(response)

	def _parcel_or_throw(self, response):
		try:
			data = response.json()
		except ValueError:
			frappe.throw(_("SendCloud returned {0}: {1}").format(response.status_code, response.text[:300]))

		if response.status_code >= 400 or data.get("error"):
			message = (data.get("error") or {}).get("message") or response.text[:300]
			frappe.throw(_("SendCloud: {0}").format(message), title=_("Return label failed"))

		return data.get("parcel") or {}

	def get_tracking_history(self, tracking_number):
		"""Full status ladder for a tracking number, or None.

		Returns {"expected": date, "statuses": [{"at", "parent_status", "message"}]}.
		"""
		if not tracking_number or not self.api_key:
			return None
		try:
			response = requests.get(
				f"{TRACKING_URL}/{tracking_number}",
				auth=(self.api_key, self.api_secret),
				headers={"Accept": "application/json"},
				timeout=30,
			)
			if not response.ok:
				return None
			data = response.json()
		except Exception:
			return None

		statuses = []
		for s in data.get("statuses") or []:
			statuses.append({
				"at": (s.get("carrier_update_timestamp") or "")[:19],
				"parent_status": s.get("parent_status") or "",
				"message": s.get("carrier_message") or "",
			})
		return {
			"expected": data.get("expected_delivery_date"),
			"is_return": data.get("is_return"),
			"statuses": statuses,
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
		missing_parcel_ids = []
		forbidden_parcel_ids = []

		for ship_id in shipment_id_list:
			try:
				response = requests.get(
					f"{PARCELS_URL}/{ship_id}",
					auth=(self.api_key, self.api_secret),
					headers={"Accept": "application/json"},
				)
				# Parcel SendCloud'dan silinmişse (ör. carrier tarafından) 404 döner;
				# beklenen bir durum: loglama, ama "silinmiş" olarak raporla.
				if response.status_code == 404:
					missing_parcel_ids.append(ship_id)
					continue
				# 403 = kimlik doğrulandı ama erişim reddedildi (token değişmiş ya da
				# parcel başka bir alt hesapta). "Silinmiş" DEĞİL — bu döngüde sessizce
				# atla; bir sonraki denemede erişim gelirse normal güncellenir.
				if response.status_code == 403:
					forbidden_parcel_ids.append(ship_id)
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

			# Teslim zamanı: parça Delivered ise son güncelleme zamanı (date_updated).
			# SendCloud gün-önce formatı verdiği için ISO'ya normalize et (aksi halde
			# gün<=12 teslimlerde tarih AY-GÜN olarak yanlış okunuyor).
			delivered_at = None
			if status_message == "Delivered":
				delivered_at = parse_sendcloud_datetime(parcel_data.get("date_updated"))
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

		# Shipment geneli: tüm CANLI parçalar Delivered ise teslim zamanı = en geç
		# parça zamanı.
		#
		# Ölü parçalar hesaba katılmaz. Etiketi taşıyıcıya hiç bildirilememiş bir
		# parça ("Announcement failed") asla Delivered olmayacak; sayılırsa müşteri
		# paketini almış olsa bile gönderi sonsuza kadar "In Progress" kalır — ve
		# hiçbir yerde hata görünmez, çünkü teknik olarak hâlâ bir parça teslim
		# edilmemiştir. Genelde yeniden oluşturulmuş bir etiketin ölü ilk denemesi
		# olur ve işi asıl taşıyan ikinci parçadır.
		live_parcels = [p for p in parcels if not _is_dead_parcel(p["status"])]
		all_delivered = bool(live_parcels) and all(p["status"] == "Delivered" for p in live_parcels)
		shipment_delivered_at = max(delivered_times) if (all_delivered and delivered_times) else None

		return {
			"awb_number": ", ".join(awb_number),
			"tracking_status": ", ".join(tracking_status),
			"tracking_status_info": ", ".join(tracking_status),
			"tracking_url": ", ".join(tracking_urls),
			"parcels": parcels,
			"delivered_at": shipment_delivered_at,
			# Bulunamayan (silinmiş) parça id'leri. Hiç parça kalmadıysa etiket
			# tamamen silinmiş demektir; çağıran taraf Shipment'ı işaretleyebilir.
			"missing_parcel_ids": missing_parcel_ids,
			"all_parcels_missing": bool(missing_parcel_ids) and not parcels,
			# 403 vb. yüzünden hiçbir parça okunamadıysa: çağıran taraf mevcut
			# awb/durumu boş değerlerle EZMESİN, bu döngüyü atlasın.
			"forbidden_parcel_ids": forbidden_parcel_ids,
			"no_data": not parcels and not missing_parcel_ids,
		}

	def total_parcel_price(self, parcel_price, parcels: list[dict]):
		count = 0
		for parcel in parcels:
			count += parcel.get("count")
		return flt(parcel_price) * count

	def _contract_property(self, properties, contract_id):
		"""Put the chosen contract into a ship_with `properties` dict.

		The key is `contract_id`, not `contract`. SendCloud v3 drops properties it
		does not recognise without raising, so the wrong key produced a label on
		the carrier default contract and nothing anywhere said so — the only
		visible trace was a tracking number with the wrong prefix.

		Returns the id actually sent (or None), for the response check below.
		"""
		if not contract_id:
			return None
		try:
			contract_id = int(contract_id)
		except (TypeError, ValueError):
			pass
		properties["contract_id"] = contract_id
		return contract_id

	def _used_contract(self, requested, parcels_data):
		"""The contract SendCloud actually shipped on; warns if it is not `requested`.

		Which contract carried the parcel decides where a compensation claim is
		filed (own contract → carrier, broker → SendCloud), so a swap has to
		surface at label time rather than weeks later when the claim is rejected.

		Falls back to `requested` when the response carries no contract, so the
		caller never records a guess as fact.
		"""
		used = None
		for parcel in parcels_data or []:
			value = parcel.get("contract")
			if isinstance(value, dict):
				value = value.get("id")
			if value is None:
				continue
			used = value
			if requested and str(value) != str(requested):
				frappe.msgprint(
					_("SendCloud used contract {0} instead of the selected {1} for parcel {2}.").format(
						frappe.bold(value), frappe.bold(requested), parcel.get("tracking_number") or parcel.get("id")
					),
					indicator="orange",
					alert=True,
				)
		return used or requested

	def _contract_index(self):
		"""{contract_id: {name, type}} — cached; the contract list is small and stable.

		The rate response does not always carry the contract type, and a label
		response carries only the id, so both are looked up here.
		"""
		if getattr(self, "_contract_cache", None) is None:
			self._contract_cache = {}
			for c in self.get_contracts():
				if c.get("id"):
					self._contract_cache[c["id"]] = {"name": c.get("name"), "type": c.get("type")}
		return self._contract_cache

	def _contract_types(self):
		"""{contract_id: type}."""
		types = {}
		for cid, info in self._contract_index().items():
			types[cid] = info.get("type")
		return types

	def contract_fields(self, contract_id):
		"""Shipment fields recording which contract carried the parcels.

		Written on every label, not only on a manual pick: once the account
		default is a broker contract the field would otherwise stay empty and
		claim routing would have nothing to go on.
		"""
		if not contract_id:
			return {}
		try:
			key = int(contract_id)
		except (TypeError, ValueError):
			key = contract_id
		info = self._contract_index().get(key) or {}
		label = info.get("name") or str(contract_id)
		if info.get("type"):
			label = f"{label} — {info['type']}"
		return {
			"custom_sendcloud_contract": label,
			"custom_sendcloud_contract_id": str(contract_id),
		}

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
		# Teklif yanıtı SendCloud'un kendi sözleşmeleri için isim döndürmüyor, bu
		# yüzden ham değer kullanılırsa sütun boş kalır ve hangi sözleşmeyle fiyat
		# verildiği görünmez. Sözleşme listesindeki adı kullan — orada bizim kendi
		# verdiğimiz isim de var.
		indexed = self._contract_index().get(contract.get("id")) or {}
		available_service.contract_name = indexed.get("name") or contract.get("name")
		# direct = kendi sözleşmemiz, taşıyıcı bize fatura keser ve tazminat talebi
		# ona açılır. broker = SendCloud'un sözleşmesi, fatura da talep de SendCloud'a.
		# Aynı taşıyıcı iki türlü de gönderilebildiği için taşıyıcı adına bakmak yetmez.
		available_service.contract_type = contract.get("type") or self._contract_types().get(
			contract.get("id")
		)

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

	def _own_contract_labels(self):
		"""{contract_id: {own_label, is_preferred}} — SendCloud ayarlarındaki isimler."""
		if getattr(self, "_own_label_cache", None) is None:
			self._own_label_cache = {}
			settings = frappe.get_cached_doc("SendCloud", "SendCloud")
			for row in settings.get("contract_options") or []:
				if row.contract_id:
					self._own_label_cache[str(row.contract_id)] = {
						"own_label": (row.own_label or "").strip(),
						"is_preferred": bool(row.is_preferred),
					}
		return self._own_label_cache

	def get_contracts(self, carrier_code=None, apply_own_labels=True):
		"""Hesaptaki aktif kontratları döndür (panel'deki 'Enabled contract' listesi).

		Returns: [{id, name, carrier_code, carrier_name, is_default, type, state,
		is_preferred}]

		`apply_own_labels=False` yalnız senkron içindir — kendi isimlerimizi
		SendCloud'dan gelmiş gibi geri yazmamak için.
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

			# Okunur etiket. SendCloud kendi sözleşmeleri için `name` döndürmüyor;
			# panel "DPD — Sendcloud rates" diye gösteriyor. Aynı dili kullanıyoruz,
			# yoksa listede "DPD broker (104790)" yazıyor ve panelde seçtiği
			# sözleşmeyi arayan kullanıcı onu tanıyamıyor.
			# Id kalıyor: bir taşıyıcının birden fazla broker sözleşmesi olabiliyor
			# (DPD'de üç tane) ve isimsiz geldikleri için etiketleri birbirinin aynı
			# olurdu. Hangisiyle gönderildiği tazminat talebini belirlediğinden
			# ayırt edilebilir olmaları şart.
			ctype = c.get("type") or "contract"
			carrier_label = carrier.get("name") or ""
			own = self._own_contract_labels().get(str(c.get("id"))) or {}

			if apply_own_labels and own.get("own_label"):
				# Kendi ismini verdiyse yalnız o kullanılır. SendCloud'un ürettiği
				# ad ("DPD — Sendcloud rates") zaten bir isim değil, tarif; ikisi
				# yan yana yazılınca aynı şey iki kez okunuyor.
				name = f"{own['own_label']} ({c.get('id')})"
			elif c.get("name"):
				name = f"{c['name']} ({c.get('id')})"
			elif ctype == "broker":
				name = f"{carrier_label} — Sendcloud rates ({c.get('id')})"
			elif ctype == "direct":
				name = f"{carrier_label} — own contract ({c.get('id')})"
			else:
				name = f"{carrier_label} {ctype} ({c.get('id')})"

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
					"is_preferred": bool(own.get("is_preferred")) if apply_own_labels else False,
				}
			)
		return contracts

	def get_contract_options(self, carrier_code=None):
		"""Contracts formatted for a picker: the ones you marked first, default noted."""
		rows = []
		for c in self.get_contracts(carrier_code=carrier_code):
			label = c["name"]
			if c.get("is_default"):
				label += " " + _("(account default)")
			rows.append(
				{
					"id": c["id"],
					"label": ("★ " + label) if c.get("is_preferred") else label,
					"type": c.get("type"),
					"carrier": c.get("carrier_name"),
					"state": c.get("state"),
					"is_preferred": c.get("is_preferred"),
				}
			)
		# Önce işaretlediklerin; sonra broker olanlar — teklif listesinde hiç
		# görünmeyen ve bu yüzden elle seçilmesi gereken sözleşmeler bunlar.
		rows.sort(
			key=lambda r: (
				not r.get("is_preferred"),
				r.get("type") != "broker",
				str(r.get("carrier") or ""),
			)
		)
		return rows

	def get_preferred_codes(self):
		"""SendCloud Settings'teki favori (yıldızlı) shipping option kodları."""
		settings = frappe.get_single("SendCloud")
		return {
			row.shipping_option_code
			for row in (settings.get("preferred_shipping_options") or [])
			if row.shipping_option_code
		}

	def get_preferred_contracts(self):
		"""{shipping_option_code: {contract_id, contract_label}} for starred options.

		A starred option that remembers its contract can be shipped straight from
		the rate list. Without it the rate list would keep offering only the
		account default and the contract would have to be picked by hand every
		time — which is what made a broker contract impractical to use.
		"""
		settings = frappe.get_single("SendCloud")
		out = {}
		for row in settings.get("preferred_shipping_options") or []:
			if row.shipping_option_code and row.get("contract_id"):
				out[row.shipping_option_code] = {
					"contract_id": row.get("contract_id"),
					"contract_label": row.get("contract_label") or "",
				}
		return out

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

		exact = self._exact_matches(data, order_number)
		if exact:
			return exact[0]
		return data[0] if data else None

	def find_orders_by_number(self, order_number):
		"""Aynı numaraya sahip TÜM siparişleri döndür.

		Bir sipariş ebat/ağırlık nedeniyle birkaç gönderiye bölündüğünde SendCloud'da
		aynı numarayla birden çok sipariş kaydı oluşuyor. Bu bir veri hatası değil,
		çalışma biçimi — dolayısıyla "ilkini al" yanlış cevap verir ve uyarı vermek de
		her sevkiyatta gürültü olur. Hangisinin sevk edileceğini soran taraf seçer.
		"""
		if not self.enabled or not self.api_key or not self.api_secret:
			return []
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
			show_error_alert("finding SendCloud orders")
			return []
		return self._exact_matches(data, order_number) or data

	def _exact_matches(self, data, order_number):
		out = []
		for order in data or []:
			if str(order.get("order_number")) == str(order_number):
				out.append(order)
		return out

	def describe_order(self, order):
		"""Seçim listesinde gösterilecek özet: kimlik, durum, ürünler."""
		details = order.get("order_details") or {}
		integration = details.get("integration") or {}
		lines = []
		for it in (details.get("order_items") or order.get("order_items") or []):
			name = it.get("name") or it.get("description") or it.get("sku") or ""
			qty = it.get("quantity") or it.get("qty") or 1
			if name:
				lines.append(f"{qty}x {name}")
		return {
			"order_id": order.get("order_id") or order.get("id"),
			"status": (order.get("status") or {}).get("message")
			if isinstance(order.get("status"), dict)
			else order.get("status"),
			"integration": integration.get("name") or integration.get("id"),
			"items": ", ".join(lines)[:160],
		}

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

	def find_parcels_by_order_number(self, order_number):
		"""order_number ile eşleşen, İPTAL EDİLMEMİŞ TÜM parcel'ları (label) döndür.

		Bir gönderi SendCloud'da boyut/ağırlık nedeniyle birden çok pakete (label)
		bölünebilir; hepsi aynı order_number'a bağlıdır. Tek label yerine hepsini
		döndürürüz ki ERPNext bütün parçaların tracking'ini çeksin.
		Sonuç id'ye göre artan sıralı. Hiç aktif yoksa (tümü Cancelled) boş liste.
		"""
		if not self.enabled or not self.api_key or not self.api_secret:
			return []
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
			show_error_alert("finding SendCloud parcels by order number")
			return []

		active = [p for p in parcels if (p.get("status") or {}).get("message") != "Cancelled"]
		return sorted(active, key=lambda p: p.get("id") or 0)

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
		#
		# Sipariş referansı `order_id` — belgeye göre "an external order ID assigned
		# by the shop system", yani pazaryerinin kimliği (Shopify'da
		# "gid://shopify/Order/..."). SendCloud'un kendi `id`'si BURAYA GİRMEZ:
		# denendi, 404 döndü.
		reference = order.get("order_id")
		if not reference:
			frappe.throw(
				_("The SendCloud order has no external order id, so the label cannot be requested for it.")
			)

		payload = {
			"integration_id": int(integration_id),
			"order": {"order_id": str(reference)},
		}
		if shipping_option_code:
			properties = {"shipping_option_code": shipping_option_code}
			contract_id = self._contract_property(properties, contract_id)
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

		# Sipariş SendCloud'da birkaç gönderiye bölünmüşse ("1 of 2", "2 of 2"),
		# parçaların hepsi aynı order_number'ı VE aynı harici order_id'yi taşıyor.
		# Ship an Order yalnız bu ikisiyle seçim yapabiliyor, dolayısıyla tek bir
		# parçayı hedeflemenin yolu yok — kodla çözülebilecek bir şey değil.
		# Kullanıcı bunu "multiple_orders_found" diye görmemeli; ne yapacağını
		# görmeli.
		if "multiple_orders_found" in json.dumps(response_data, default=str):
			frappe.throw(
				_(
					"Order {0} is split into several shipments in SendCloud, and its parts all "
					"carry the same order number and order id — the Ship an Order API cannot "
					"pick one of them.<br><br>"
					"Either print the labels in SendCloud and press <b>Sync SendCloud Label</b>, "
					"or build them here with <b>Fetch Shipping Rates</b>."
				).format(frappe.bold(order.get("order_number") or reference)),
				title=_("Split order"),
			)

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
		used_contract = self._used_contract(contract_id, [parcel])

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
			"contract_id": used_contract,
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
			# Elle girilen koli değeri (Value of Goods) varsa SendCloud'a onu beyan et
			self._apply_parcel_value(parcel_data, parcel)
			# Eşleştirme modunda haritada olmayan koli ürünsüz kalır
			return parcel_data

		# 2) Eşleştirme yoksa: eski davranış (tüm ürünler her koliye)
		if shipment_items_data and shipment_items_data.get("parcel_items"):
			parcel_data["parcel_items"] = shipment_items_data["parcel_items"]

			# Label notes (SKU bilgileri)
			if shipment_items_data.get("label_notes"):
				parcel_data["label_notes"] = shipment_items_data["label_notes"]

		return parcel_data

	def _apply_parcel_value(self, parcel_data, parcel):
		"""Kolinin SendCloud'a beyan edilen toplam değerini, Shipment Parcel'da elle
		girilen custom_value_of_goods'a eşitle. DN kalem tutarları 0 olsa bile (ör.
		bedava topper, bundle bileşeni) kolinin gerçek beyan değeri gider. İtem
		fiyatları hedefe göre ölçeklenir; hepsi 0 ise adete göre dağıtılır."""
		target = flt(parcel.get("custom_value_of_goods"), CURRENCY_DECIMALS)
		items = parcel_data.get("parcel_items") or []
		if target <= 0 or not items:
			return
		# Fiyatlar BİRİM başına; SendCloud'un gördüğü toplam = Σ(birim_fiyat × adet).
		current = sum(
			flt(it.get("price", {}).get("value", 0)) * (int(it.get("quantity") or 1)) for it in items
		)
		if abs(current - target) < 0.005:
			return
		if current > 0:
			factor = target / current
			for it in items:
				it["price"]["value"] = flt(it["price"]["value"] * factor, CURRENCY_DECIMALS)
		else:
			# Tüm fiyatlar 0 → hedefi adete göre birim başına dağıt.
			total_qty = sum(int(it.get("quantity") or 0) for it in items) or len(items)
			per_unit = flt(target / total_qty, CURRENCY_DECIMALS)
			for it in items:
				it["price"]["value"] = per_unit

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
					# Ağırlıkla aynı: SendCloud value'yu da quantity ile çarpar, bu yüzden
					# BİRİM fiyat gönder (satır toplamı değil).
					"value": flt(info.get("unit_price", 0), CURRENCY_DECIMALS),
					"currency": info.get("currency") or currency,
				},
				"weight": {
					# SendCloud koli içeriğini weight × quantity ile hesaplar; bu yüzden
					# BİRİM ağırlık gönder (satır toplamı DEĞİL), yoksa qty ile çift sayılır.
					"value": flt(unit_weight, WEIGHT_DECIMALS) if unit_weight > 0 else 0.1,
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
						# Aynı SKU: yalnızca adeti topla. Birim fiyat/ağırlık sabit kalır
						# (SendCloud value ve weight'i quantity ile çarpıyor).
						items_dict[sku]["quantity"] += int(item.qty)
						sku_qty_dict[sku] += int(item.qty)
					else:
						# BİRİM ağırlık (SendCloud weight × quantity yaptığından satır toplamı
						# DEĞİL). DN satırının weight_per_unit'i, yoksa total/qty, yoksa Item.
						per_unit_weight = flt(item.weight_per_unit or 0)
						if not per_unit_weight and item.qty:
							per_unit_weight = flt(item.total_weight or 0) / flt(item.qty)
						if not per_unit_weight:
							per_unit_weight = flt(item_doc.weight_per_unit or 0)

						# BİRİM fiyat (satır toplamı değil — SendCloud value × quantity yapar).
						per_unit_price = flt(item.rate or 0)
						if not per_unit_price and item.qty:
							per_unit_price = flt(item.amount or 0) / flt(item.qty)

						items_dict[sku] = {
							"description": (item.item_name or item.item_code or "Product")[:200],
							"quantity": int(item.qty),
							"price": {
								"value": flt(per_unit_price, CURRENCY_DECIMALS),
								"currency": default_currency
							},
							"weight": {
								"value": flt(per_unit_weight, WEIGHT_DECIMALS) if per_unit_weight > 0 else 0.1,
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
					# data.price ve data.weight artık BİRİM başına (satır toplamı değil) -> bölme yok.
					"unit_price": flt(data["price"]["value"]),
					"unit_weight": flt(data["weight"]["value"]),
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

@frappe.whitelist()
def get_sendcloud_contracts(carrier_code=None):
	"""Contract list for the Shipment picker.

	SendCloud only offers each carrier default contract through the rate
	endpoint, so a broker contract has to be chosen by hand.
	"""
	return SendCloudUtils().get_contract_options(carrier_code=carrier_code)
