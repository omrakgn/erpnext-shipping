# Copyright (c) 2020, Frappe Technologies and contributors
# For license information, please see license.txt
import json

import frappe
from frappe import _
from frappe.utils import date_diff, flt, get_datetime
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


def _build_parcels(shipment_doc):
	"""Bağlı Delivery Note'lardan Shipment Parcel + Parcel Items tablolarını YERİNDE
	(kaydetmeden) oluştur. Returns (created_count, skipped_templates). Ürün yoksa
	tabloya dokunmaz (0, [] döner)."""

	# Bağlı Delivery Note'lardan "paketlenebilir birim"ler çıkar (ilk görülme sırası).
	# - Normal ürün: kutu = ürünün template'i, içinde ürünün kendisi.
	# - Product Bundle (paket): DN'de parent olarak görünür, bileşenler Packed Item'da.
	#   Bundle TEK koli olarak gider: kutu = BUNDLE'ın template'i, içinde tüm bileşenler.
	# unit = {"template_item": code, "qty": n, "contents": {component_code: kutu_başına_adet}}
	units = []
	seen_dns = set()
	for dn_row in shipment_doc.get("shipment_delivery_note", []):
		# Aynı DN birden fazla listelense bile bir kez işle (adet katlanmasın)
		if not dn_row.delivery_note or dn_row.delivery_note in seen_dns:
			continue
		seen_dns.add(dn_row.delivery_note)

		packed_by_parent = {}
		for p in frappe.get_all(
			"Packed Item",
			filters={"parent": dn_row.delivery_note, "parenttype": "Delivery Note"},
			fields=["parent_item", "item_code", "qty"],
			order_by="idx",
		):
			if p.item_code:
				packed_by_parent.setdefault(p.parent_item, []).append(p)

		for item in frappe.get_all(
			"Delivery Note Item",
			filters={"parent": dn_row.delivery_note},
			fields=["item_code", "qty"],
			order_by="idx",
		):
			code = item.item_code
			qty = flt(item.qty)
			if not code:
				continue
			if code in packed_by_parent:
				# Bundle: kutu başına bileşen adedi = toplam bileşen / bundle adedi
				contents = {}
				for comp in packed_by_parent[code]:
					per_box = (flt(comp.qty) / qty) if qty else flt(comp.qty)
					contents[comp.item_code] = contents.get(comp.item_code, 0) + per_box
				units.append({"template_item": code, "qty": qty, "contents": contents})
			else:
				units.append({"template_item": code, "qty": qty, "contents": {code: 1.0}})

	if not units:
		return 0, []

	has_parcel_items = shipment_doc.meta.has_field("custom_parcel_items")

	# Mevcut satırları temizle
	shipment_doc.set("shipment_parcel", [])
	if has_parcel_items:
		shipment_doc.set("custom_parcel_items", [])

	skipped = []
	parcel_no = 0
	for unit in units:
		template = frappe.db.get_value("Item", unit["template_item"], "custom_shipment_parcel_template")
		if not template:
			skipped.append(unit["template_item"])
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
		# Kutu ağırlığı: template ağırlığı; yoksa içindeki ürünlerin weight_per_unit toplamı
		box_weight = flt(dims.get("weight") or 0)
		if not box_weight:
			for code, q in unit["contents"].items():
				box_weight += flt(frappe.db.get_value("Item", code, "weight_per_unit") or 0) * q

		# Her birim ayrı kutu (count=1); bundle ise kutu = bundle, içinde bileşenler.
		qty = unit["qty"]
		whole = int(qty)
		box_fractions = [1.0] * whole
		remainder = flt(qty) - whole
		if remainder > 0:
			box_fractions.append(remainder)
		if not box_fractions:
			box_fractions = [flt(qty) or 1]

		for frac in box_fractions:
			parcel_no += 1
			shipment_doc.append(
				"shipment_parcel",
				{
					"length": dims.get("length") or 0,
					"width": dims.get("width") or 0,
					"height": dims.get("height") or 0,
					"weight": box_weight,
					"count": 1,
					"parcel_template": template,
				},
			)
			if has_parcel_items:
				for code, q in unit["contents"].items():
					shipment_doc.append(
						"custom_parcel_items",
						{"parcel_no": parcel_no, "item_code": code, "qty": flt(q) * frac},
					)

	return parcel_no, skipped


@frappe.whitelist()
def populate_parcels_from_delivery_notes(shipment: str):
	"""Buton: bağlı Delivery Note'lardan parça tablolarını (mevcut satırları ezerek)
	yeniden oluştur ve kaydet."""
	shipment_doc = frappe.get_doc("Shipment", shipment)
	created, skipped = _build_parcels(shipment_doc)
	if not created and not skipped:
		frappe.throw(_("No items found in the linked Delivery Notes."))
	shipment_doc.save()

	message = _("Populated {0} parcel(s) from Delivery Notes.").format(created)
	if skipped:
		message += "<br>" + _("No parcel template set, skipped: {0}").format(", ".join(skipped))
	frappe.msgprint(
		message,
		title=_("Parcels Populated"),
		indicator="green" if created else "orange",
	)

	return {"created": created, "skipped": skipped}


def _has_real_parcels(doc):
	"""Anlamlı (boş olmayan) bir parça var mı? Formun eklediği boş/varsayılan satır
	'dolu' sayılmasın diye ölçü/ağırlık/şablon veya kalem içeriğine bakılır."""
	for p in doc.get("shipment_parcel") or []:
		if (
			p.get("parcel_template")
			or flt(p.get("weight"))
			or flt(p.get("length"))
			or flt(p.get("width"))
			or flt(p.get("height"))
		):
			return True
	for it in doc.get("custom_parcel_items") or []:
		if it.get("item_code"):
			return True
	return False


def auto_populate_parcels(doc, method=None):
	"""Kaydederken anlamlı parça yoksa ve bağlı Delivery Note varsa parça tablolarını
	otomatik doldur (Shipment Settings ile aç/kapa). Kullanıcının elle girdiği ölçülü
	parçaları ezmez; boş/varsayılan satır engel olmaz."""
	if not _get_shipment_setting("auto_populate_parcels", 1):
		return
	if not doc.get("shipment_delivery_note"):
		return
	if _has_real_parcels(doc):
		return
	_build_parcels(doc)


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

		costs, currency = _shipment_parcel_costs(name)
		for p in parcels:
			tn = p.get("tracking_number") or ""
			rows.append(
				{
					"shipment": name,
					"sku": p.get("sku"),
					"carrier": p.get("carrier") or sh.carrier,
					"tracking_number": tn,
					"tracking_url": p.get("tracking_url"),
					"status": p.get("status"),
					"delivered_at": p.get("delivered_at"),
					"cost": _cost_for_tracking(costs, tn),
					"currency": currency,
				}
			)

	return rows


def _shipment_parcel_costs(shipment):
	"""Return ({normalised_tracking: net_cost}, currency) from a shipment's cost entries."""
	costs = {}
	currency = None
	for e in frappe.get_all(
		"Shipping Cost Entry",
		filters={"shipment": shipment},
		fields=["parcel_number", "total_net_amount", "currency"],
	):
		currency = e.currency or currency
		raw = (e.parcel_number or "").strip()
		for key in {raw, raw.lstrip("0")}:
			if key:
				costs[key] = costs.get(key, 0) + flt(e.total_net_amount)
	return costs, (currency or "EUR")


def _cost_for_tracking(costs, tracking):
	tracking = (tracking or "").strip()
	if not tracking:
		return None
	if tracking in costs:
		return costs[tracking]
	return costs.get(tracking.lstrip("0"))


@frappe.whitelist()
def get_shipment_parcel_breakdown(shipment):
	"""Per-parcel breakdown for a Shipment: tracking number, carrier, net cost,
	status, delivery time and label-removed flag. Combines the stored tracking
	details JSON with the matched Shipping Cost Entries."""
	sh = frappe.get_doc("Shipment", shipment)
	try:
		parcels = json.loads(sh.get("custom_tracking_details") or "[]")
	except Exception:
		parcels = []
	if not parcels:
		awbs = [a.strip() for a in (sh.awb_number or "").replace(";", ",").split(",") if a.strip()]
		parcels = [{"tracking_number": a} for a in awbs]

	costs, currency = _shipment_parcel_costs(shipment)
	label_removed = 1 if sh.get("custom_label_removed") else 0
	rows = []
	for p in parcels:
		tn = p.get("tracking_number") or ""
		rows.append(
			{
				"tracking_number": tn,
				"tracking_url": p.get("tracking_url"),
				"carrier": p.get("carrier") or sh.carrier,
				"cost": _cost_for_tracking(costs, tn),
				"currency": currency,
				"status": p.get("status") or "",
				"delivered_at": p.get("delivered_at"),
				"label_removed": label_removed,
			}
		)
	return rows


def _get_shipment_setting(field, default=None):
	try:
		val = frappe.db.get_single_value("Shipment Settings", field)
	except Exception:
		return default
	return default if val is None else val


def _time_str(val):
	"""Normalise a Time value to a clean 'HH:MM:SS' string. Frappe returns Time
	fields as datetime.timedelta, which the client Time control does not apply."""
	if val is None:
		return None
	if isinstance(val, str):
		return val
	try:
		total = int(val.total_seconds())
	except AttributeError:
		return str(val)
	return f"{total // 3600:02d}:{(total % 3600) // 60:02d}:{total % 60:02d}"


@frappe.whitelist()
def get_shipment_form_defaults():
	"""Pickup defaults for new Shipment forms (from Shipment Settings)."""
	# Geçersiz bir Contact/Address set etmek ERPNext'in kendi handler'ını (ör.
	# get_company_contact) None üzerinde çökertiyor; yalnızca gerçekten var olan
	# kayıtları döndür.
	def _valid(doctype, value):
		return value if (value and frappe.db.exists(doctype, value)) else None

	return {
		"set_pickup_date_today": _get_shipment_setting("set_pickup_date_today", 1),
		"set_default_pickup_time": _get_shipment_setting("set_default_pickup_time", 1),
		"default_pickup_from": _time_str(_get_shipment_setting("default_pickup_from", "15:00:00")),
		"default_pickup_to": _time_str(_get_shipment_setting("default_pickup_to", "17:00:00")),
		"default_pickup_address": _valid("Address", _get_shipment_setting("default_pickup_address")),
		"default_pickup_contact_person": _valid(
			"User", _get_shipment_setting("default_pickup_contact_person")
		),
	}


def _content_description_from_dns(dn_names):
	"""Comma-joined distinct item names across the given Delivery Notes (max 250)."""
	names, seen = [], set()
	for dn in dn_names or []:
		if not dn:
			continue
		for it in frappe.get_all(
			"Delivery Note Item",
			filters={"parent": dn},
			fields=["item_name", "item_code"],
			order_by="idx",
		):
			label = (it.item_name or it.item_code or "").strip()
			if label and label not in seen:
				seen.add(label)
				names.append(label)
	return ", ".join(names)[:250]


@frappe.whitelist()
def get_content_description(delivery_notes):
	"""Item-name summary for the given Delivery Notes, for client-side auto-fill of
	the (mandatory) Description of Content field. Empty if auto-fill is disabled."""
	if isinstance(delivery_notes, str):
		delivery_notes = json.loads(delivery_notes)
	if not _get_shipment_setting("auto_fill_description", 1):
		return ""
	return _content_description_from_dns(delivery_notes or [])


def set_shipment_description(doc, method=None):
	"""API fallback: auto-fill Description of Content from linked Delivery Note item
	names when empty (Shipment validate hook). The UI fills it client-side because
	the field is mandatory; this covers documents created via the API."""
	if doc.get("description_of_content"):
		return
	if not _get_shipment_setting("auto_fill_description", 1):
		return
	dns = [r.delivery_note for r in (doc.get("shipment_delivery_note") or []) if r.delivery_note]
	desc = _content_description_from_dns(dns)
	if desc:
		doc.description_of_content = desc


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

	# ERPNext'ten: ilk koliden ağırlık/ölçü (= parcel weight); kargo seçimi olan ilk
	# koliden method/sözleşme
	# Ağırlık = TÜM kolilerin toplamı (ilk koli değil); ölçü = ilk dolu koli.
	# Kargo/sözleşme = ilk seçimi olan koliden.
	total_weight = 0.0
	dimensions = None
	shipping_option_code, contract_id = None, None
	for row in shipment_doc.get("shipment_parcel") or []:
		total_weight += flt(row.weight) * (row.count or 1)
		if dimensions is None and (row.length or row.width or row.height):
			dimensions = {"length": row.length, "width": row.width, "height": row.height}
		if not shipping_option_code and row.get("custom_shipping_option_code"):
			shipping_option_code = row.get("custom_shipping_option_code")
			contract_id = row.get("custom_shipping_contract_id")
	if total_weight <= 0:
		frappe.throw(
			_("Set parcel weight(s) on the Shipment before fulfilling — total weight is 0.")
		)
	# Kargo seçilmemişse SendCloud, ship_with olmadan Shipping Defaults'a düşer
	# (carrier "SendCloud", ağırlık 1kg) ve ERPNext verisini yok sayar. Engelle.
	if not shipping_option_code:
		frappe.throw(
			_("Select a carrier on a parcel before fulfilling (use 'Select Carrier'). "
			  "Without it SendCloud falls back to shipping defaults (1kg).")
		)
	weight = total_weight

	# Delivery notes = ERPNext item code'ları (custom_parcel_items, yoksa bağlı DN'ler;
	# bundle'lar bileşenlerine açılır). SendCloud order'ının SKU'su pazaryeri barkodu
	# (EAN) olabilir; biz ERPNext kodlarını gönderiyoruz.
	erp_item_qty = {}
	for r in shipment_doc.get("custom_parcel_items") or []:
		if r.item_code:
			erp_item_qty[r.item_code] = erp_item_qty.get(r.item_code, 0) + flt(r.qty)
	if not erp_item_qty:
		seen = set()
		for d in shipment_doc.get("shipment_delivery_note") or []:
			if not d.delivery_note or d.delivery_note in seen:
				continue
			seen.add(d.delivery_note)
			packed = {}
			for p in frappe.get_all(
				"Packed Item",
				filters={"parent": d.delivery_note, "parenttype": "Delivery Note"},
				fields=["parent_item", "item_code", "qty"],
			):
				if p.item_code:
					packed.setdefault(p.parent_item, []).append(p)
			for it in frappe.get_all(
				"Delivery Note Item", filters={"parent": d.delivery_note}, fields=["item_code", "qty"]
			):
				if it.item_code in packed:
					for c in packed[it.item_code]:
						erp_item_qty[c.item_code] = erp_item_qty.get(c.item_code, 0) + flt(c.qty)
				elif it.item_code:
					erp_item_qty[it.item_code] = erp_item_qty.get(it.item_code, 0) + flt(it.qty)
	notes = ", ".join(erp_item_qty.keys()) if erp_item_qty else None

	# Birim ağırlık: order item'ını ERPNext'e item_code VEYA barkod (EAN) ile eşle.
	item_weights = {}
	for it in (order.get("order_details") or {}).get("order_items") or []:
		sku = it.get("sku")
		if not sku:
			continue
		erp_code = sku if frappe.db.exists("Item", sku) else frappe.db.get_value(
			"Item Barcode", {"barcode": sku}, "parent"
		)
		if erp_code:
			unit_w = frappe.db.get_value("Item", erp_code, "weight_per_unit")
			if unit_w:
				item_weights[sku] = flt(unit_w)

	shipment_info = sendcloud.ship_order(
		order,
		shipping_option_code=shipping_option_code,
		contract_id=contract_id,
		weight=weight,
		dimensions=dimensions,
		item_weights=item_weights,
		notes=notes,
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


@frappe.whitelist()
def sync_sendcloud_label(shipment):
	"""Shipment'ın po_no'su ile eşleşen SendCloud parcel'ını (label) bulup tracking
	bilgisini ERPNext'e çeker.

	Label SendCloud'da (panel/pazaryeri) doğru ağırlık + marketplace bağıyla
	oluşturulur; ERPNext label OLUŞTURMAZ, sadece order_number = po_no ile eşleşen
	iptal-olmayan en güncel parcel'ı bulup tracking/carrier/durum bilgisini alır.
	Böylece Ship an Order'ın parcel ağırlığı kısıtına takılmayız.
	"""
	shipment_doc = frappe.get_doc("Shipment", shipment)
	po_no = get_shipment_po_no(shipment_doc)
	if not po_no:
		frappe.throw(_("No Customer's Purchase Order (po_no) found on the linked Sales Order(s)."))

	# Bir gönderi SendCloud'da boyut/ağırlık nedeniyle birden çok label'a bölünmüş
	# olabilir; hepsi aynı order_number'a bağlı. Tüm aktif parcel'ları çekip
	# birleştiriyoruz (shipment_id/awb/tracking_url virgülle) — tek label düşmesin.
	parcels = SendCloudUtils().find_parcels_by_order_number(po_no)
	if not parcels:
		frappe.throw(
			_("No SendCloud label found for order number {0}.").format(frappe.bold(po_no))
		)

	pids = [str(p.get("id")) for p in parcels]
	tracking_numbers = [p.get("tracking_number") for p in parcels if p.get("tracking_number")]
	tracking_urls = [p.get("tracking_url") for p in parcels if p.get("tracking_url")]
	first_carrier = parcels[0].get("carrier") or {}
	total_weight = sum(flt(p.get("weight")) for p in parcels)

	shipment_ids = ", ".join(pids)
	shipment_doc.db_set(
		{
			"service_provider": SENDCLOUD_PROVIDER,
			"carrier": first_carrier.get("name") or first_carrier.get("code") or "SendCloud",
			"shipment_id": shipment_ids,
			"awb_number": ", ".join(tracking_numbers),
			"tracking_url": ", ".join(tracking_urls),
			"status": "Booked",
		}
	)

	# Tam tracking detayını çek (parça-bazlı JSON, delivered_at, durum eşlemesi).
	# get_tracking_data virgülle ayrılmış shipment_id'yi tüm parçalar için işler.
	try:
		update_tracking(shipment, SENDCLOUD_PROVIDER, shipment_ids)
	except Exception:
		frappe.log_error(title="SendCloud sync tracking error")

	frappe.msgprint(
		_("Synced {0} SendCloud label(s) for order {1} (tracking {2}, total {3} kg).").format(
			frappe.bold(len(pids)),
			frappe.bold(po_no),
			frappe.bold(", ".join(tracking_numbers) or "-"),
			frappe.bold(total_weight or "?"),
		),
		title=_("Label Synced"),
		indicator="green",
	)
	return {"shipment_id": shipment_ids, "tracking_number": ", ".join(tracking_numbers)}


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

	# SendCloud parçası bulunamıyorsa (ör. carrier etiketi silmiş) — hepsi silinmişse
	# Shipment'ı "Label Removed" olarak işaretle. Böylece listede filtrelenebilir ve
	# saatlik job onları tekrar sorgulamaz. Parça(lar) geri gelirse işareti kaldır.
	if tracking_data.get("all_parcels_missing"):
		if not shipment.get("custom_label_removed"):
			shipment.db_set("custom_label_removed", 1)
			frappe.log_error(
				title="SendCloud label removed",
				message=f"Shipment {shipment.name}: parcel(s) "
				f"{tracking_data.get('missing_parcel_ids')} not found in SendCloud (deleted?).",
			)
		return
	elif shipment.get("custom_label_removed"):
		# Etiket tekrar erişilebilir; işareti temizle.
		shipment.db_set("custom_label_removed", 0)
	# Shipment.tracking_status sabit seçenekli (Select: "", In Progress, Delivered,
	# Returned, Lost). SendCloud'un ham durumu ("Ready to send", "Announced" vb.)
	# buraya yazılamaz — izin verilen değere eşle; ham detayı tracking_status_info'da tut.
	raw_status = (tracking_data.get("tracking_status") or "").strip()
	low = raw_status.lower()
	if tracking_data.get("delivered_at"):
		mapped_status = "Delivered"
	elif "return" in low:
		mapped_status = "Returned"
	elif "lost" in low:
		mapped_status = "Lost"
	elif raw_status:
		mapped_status = "In Progress"
	else:
		mapped_status = ""
	updates = {
		"awb_number": tracking_data.get("awb_number"),
		"tracking_status": mapped_status,
		"tracking_status_info": raw_status or tracking_data.get("tracking_status_info"),
		"tracking_url": tracking_data.get("tracking_url"),
	}
	# Parça-bazlı detaylar (SKU/carrier/status/teslim zamanı) — Delivery Note tablosu bundan beslenir
	if "parcels" in tracking_data:
		updates["custom_tracking_details"] = json.dumps(tracking_data.get("parcels") or [])
	delivered_at = tracking_data.get("delivered_at")
	# SendCloud'un delivered_at'i parcel'ın date_updated'ı; teslimden SONRA kayıt
	# güncellenirse ileri kayar. Bu yüzden yalnızca İLK kez yaz (üzerine yazma) —
	# böylece gerçek teslim anına en yakın değer sabit kalır.
	if delivered_at and not shipment.get("custom_delivered_at"):
		dt = get_datetime(delivered_at)
		updates["custom_delivered_at"] = dt
		# Kurye transit süresi: pickup_date -> teslim (gün). 0-90 gün dışı = bozuk
		# veri (yanlış tarih), kaydetme.
		if shipment.get("pickup_date"):
			transit = date_diff(dt.date(), shipment.pickup_date)
			if transit is not None and 0 <= transit <= 90:
				updates["custom_transit_days"] = transit
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
