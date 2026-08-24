# Copyright (c) 2020, Frappe Technologies and contributors
# For license information, please see license.txt
import re

import frappe
from frappe import _
from frappe.utils.data import get_link_to_form


def get_tracking_url(carrier, tracking_number):
	# Return the formatted Tracking URL.
	tracking_url = ""
	url_reference = frappe.get_value("Parcel Service", carrier, "url_reference")
	if url_reference:
		tracking_url = frappe.render_template(url_reference, {"tracking_number": tracking_number})
	return tracking_url


def get_address(address_name):
	address = frappe.db.get_value(
		"Address",
		address_name,
		[
			"address_title",
			"address_line1",
			"address_line2",
			"city",
			"pincode",
			"country",
		],
		as_dict=1,
	)
	validate_address(address)

	address.country = address.country.strip()
	address.country_code = get_country_code(address.country)
	address.pincode = address.pincode.replace(" ", "")
	address.city = address.city.strip()

	return address


def validate_address(address):
	if not address.country:
		frappe.throw(f"Please add a valid country in Address {address.address_title}.")

	if not address.pincode or address.pincode.strip() == "":
		frappe.throw(_("Please add a valid pincode in Address {0}.").format(address.address_title))


def validate_parcels(doc, method=None):
	if doc.docstatus != 0:
		return

	for parcel in doc.shipment_parcel:
		for field in ("length", "width", "height"):
			if (parcel.get(field) or 0) < 1:
				frappe.throw(
					_("Parcel row {idx}: {field_label} must be at least 1 cm.").format(
						idx=parcel.idx, field_label=_(parcel.meta.get_label(field))
					)
				)


def validate_parcel_items(doc, method=None):
	"""Koli içeriğindeki (custom_parcel_items) toplam adetler ile bağlı Delivery
	Note'lardaki adetler uyuşmazsa kullanıcıyı uyar (engelleme yok)."""
	parcel_items = doc.get("custom_parcel_items") or []
	if not parcel_items:
		# Birden fazla koli var ama eşleştirme yoksa: tüm ürünler her koliye gider.
		# Bu genelde istenmeyen bir durumdur; kullanıcıyı uyar (engelleme yok).
		if len(doc.get("shipment_parcel", [])) > 1:
			frappe.msgprint(
				_(
					"This shipment has multiple parcels but no Parcel Items mapping, so all "
					"Delivery Note items will be sent in every parcel. Use 'Populate Parcels "
					"from Delivery Notes' or fill the Parcel Items table to assign items per parcel."
				),
				title=_("Parcel Items Not Set"),
				indicator="orange",
			)
		return

	# Her koli satırının (Shipment Parcel) count çarpanı: idx -> count
	parcel_counts = {p.idx: (p.count or 1) for p in doc.get("shipment_parcel", [])}

	# İki tablo elle tutulan bir NUMARAYLA bağlı ve o numara kayabiliyor: bir koli
	# satırı silindiğinde kalanların idx'i kayar, ama içerik satırlarındaki
	# `parcel_no` olduğu yerde kalır. Bağ kopar ve hiçbir yerde hata çıkmaz —
	# aşağıdaki count araması olmayan numarada sessizce 1'e düşüyordu.
	#
	# Bedeli görünmez değil: o kolinin SKU'su taşıyıcıya hiç gitmiyor, takip
	# kaydında boş kalıyor ve irsaliyenin Shipping Details sekmesinde görünmüyor.
	# SHIPMENT-01009'da tam bu oldu (2026-08-24): iki yastık kutusu tek kutuda
	# birleştirilmiş, matrasın içerik satırı hâlâ 3 numaralı koliyi gösteriyordu.
	kopuk = []
	for row in parcel_items:
		if row.parcel_no and int(row.parcel_no) not in parcel_counts:
			kopuk.append("%s -> koli %s" % (row.item_code or "?", row.parcel_no))
	if kopuk:
		frappe.throw(
			_(
				"Parcel Items point at parcels that do not exist: {0}. "
				"This happens when a parcel row is deleted after the items were assigned — "
				"the numbers do not renumber themselves. Fix the Parcel No column; "
				"otherwise those items reach neither the carrier nor the delivery note."
			).format(", ".join(kopuk)),
			title=_("Parcel Items Out of Step"),
		)

	# Kolilere atanan toplam adet (count çarpanı ile) - item_code bazında
	assigned = {}
	for row in parcel_items:
		if not row.item_code:
			continue
		count = parcel_counts.get(int(row.parcel_no), 1) if row.parcel_no else 1
		assigned[row.item_code] = assigned.get(row.item_code, 0) + (row.qty or 0) * count

	# Delivery Note'lardaki toplam adet - item_code bazında.
	# Bundle (Product Bundle) ürünlerde DN'de parent görünür ama parça kalemleri
	# BİLEŞENLERİ içerir; bu yüzden bundle parent'ı Packed Item bileşenlerine açarız
	# ki karşılaştırma tutsun.
	dn_totals = {}
	seen_dns = set()
	for dn_row in doc.get("shipment_delivery_note", []):
		# Aynı DN birden fazla listelense bile bir kez say (adet katlanmasın)
		if not dn_row.delivery_note or dn_row.delivery_note in seen_dns:
			continue
		seen_dns.add(dn_row.delivery_note)

		packed_by_parent = {}
		for p in frappe.get_all(
			"Packed Item",
			filters={"parent": dn_row.delivery_note, "parenttype": "Delivery Note"},
			fields=["parent_item", "item_code", "qty"],
		):
			if p.item_code:
				packed_by_parent.setdefault(p.parent_item, []).append(p)

		for item in frappe.get_all(
			"Delivery Note Item",
			filters={"parent": dn_row.delivery_note},
			fields=["item_code", "qty"],
		):
			if item.item_code in packed_by_parent:
				# Bundle: parent yerine bileşenleri say
				for comp in packed_by_parent[item.item_code]:
					dn_totals[comp.item_code] = dn_totals.get(comp.item_code, 0) + (comp.qty or 0)
			else:
				dn_totals[item.item_code] = dn_totals.get(item.item_code, 0) + (item.qty or 0)

	# Karşılaştır — yalnızca Delivery Note'ta olan ürünler kontrol edilir.
	# Kolilere elle eklenen ekstra ürünler (örn. hediye) DN'de yoksa uyarı vermez.
	mismatches = []
	for item_code, d in sorted(dn_totals.items()):
		a = assigned.get(item_code, 0)
		if abs(a - d) > 0.001:
			mismatches.append(
				_("{0}: parcels {1}, Delivery Note {2}").format(item_code, f"{a:g}", f"{d:g}")
			)

	if mismatches:
		frappe.msgprint(
			_("Parcel item quantities do not match the Delivery Note quantities:")
			+ "<br>"
			+ "<br>".join(mismatches),
			title=_("Parcel Items Warning"),
			indicator="orange",
		)


def validate_phone(doc, method=None):
	if doc.pickup_from_type == "Company":
		phone_number = frappe.db.get_value("User", doc.pickup_contact_person, "phone")
	else:
		phone_number = frappe.db.get_value("Contact", doc.pickup_contact_name, "phone")

	if not phone_number:
		frappe.throw(_("Pickup contact phone is required."))

	if not re.match(r"^\+(?![\s0])[\d\s]+\d$", phone_number):
		frappe.throw(_("Pickup contact phone must consist of a '+' followed by one or more digits."))


def get_country_code(country_name):
	country_code = frappe.db.get_value("Country", country_name, "code")
	if not country_code:
		frappe.throw(_("Country Code not found for {0}").format(country_name))
	return country_code


def get_contact(contact_name):
	fields = ["first_name", "last_name", "email_id", "phone", "mobile_no", "gender"]
	contact = frappe.db.get_value("Contact", contact_name, fields, as_dict=1)

	if not contact.last_name:
		frappe.throw(
			msg=_("Please set Last Name for Contact {0}").format(get_link_to_form("Contact", contact_name)),
			title=_("Last Name is mandatory to continue."),
		)

	if not contact.phone:
		contact.phone = contact.mobile_no

	return contact


def match_parcel_service_type_carrier(
	shipment_prices: list[dict], carrier_fieldname: str, service_fieldname: str
):
	from erpnext_shipping.erpnext_shipping.doctype.parcel_service_type.parcel_service_type import (
		match_parcel_service_type_alias,
	)

	for idx, prices in enumerate(shipment_prices):
		service_name = match_parcel_service_type_alias(
			prices.get(carrier_fieldname), prices.get(service_fieldname)
		)
		is_preferred = frappe.db.get_value(
			"Parcel Service Type", service_name, "show_in_preferred_services_list"
		)
		if is_preferred:
			shipment_prices[idx].is_preferred = is_preferred

	return shipment_prices


def show_error_alert(action):
	log = frappe.log_error(title="Shipping Error")
	link_to_log = get_link_to_form("Error Log", log.name, "See what happened.")
	frappe.msgprint(
		msg=_("An Error occurred while {0}. {1}").format(action, link_to_log), indicator="orange", alert=True
	)


def update_tracking_info():
	"""Scheduled event (hourly) to update Tracking info for not-delivered Shipments.

	Also updates the related Delivery Notes.
	"""
	from erpnext_shipping.erpnext_shipping.shipping import update_tracking

	# shipment_id'si olan, teslim edilmemiş tüm onaylı gönderiler. status="Booked"
	# kısıtını kaldırdık ("Submitted" kalanlar da güncellensin). docstatus=1 zaten
	# iptal edilenleri (docstatus=2) hariç tutar; ek olarak Cancelled/Completed
	# statülerini de açıkça eliyoruz.
	shipments = frappe.get_all(
		"Shipment",
		filters={
			"docstatus": 1,
			"status": ["not in", ["Cancelled", "Completed"]],
			"shipment_id": ["!=", ""],
			# Delivered ve Lost bitmiş durumlar. Lost'u elemezsek, tazminatı ödenmiş
			# ve kapatılmış bir gönderi için taşıyıcıya sonsuza kadar soru sorulur.
			"tracking_status": ["not in", ["Delivered", "Lost"]],
			# SendCloud'dan silindiği tespit edilenleri (etiket yok) tekrar sorgulama.
			"custom_label_removed": 0,
			# Taşıyıcı bu paket hakkında bildirim göndermeyi bırakmış; her saat
			# tekrar sormanın karşılığı yok.
			"custom_tracking_stalled": 0,
		},
	)
	for shipment in shipments:
		# Her shipment'ı ayrı ele al: biri hata verse bile diğerleri güncellensin
		try:
			shipment_doc = frappe.get_doc("Shipment", shipment.name)
			# Delivery Note ADLARINI geçir (child satır nesnelerini değil),
			# yoksa update_delivery_note içinde get_doc patlar ve döngü durur.
			delivery_notes = [
				row.delivery_note
				for row in (shipment_doc.shipment_delivery_note or [])
				if row.delivery_note
			]
			# update_tracking, shipment'ın takip alanlarını kendi içinde db_set eder
			update_tracking(
				shipment.name,
				shipment_doc.service_provider,
				shipment_doc.shipment_id,
				delivery_notes,
			)
		except Exception:
			frappe.log_error(
				title="Shipment tracking auto-update failed",
				message=f"Shipment: {shipment.name}\n{frappe.get_traceback()}",
			)
