# Copyright (c) 2026, Frappe Technologies and contributors
# For license information, please see license.txt
"""Connections (form dashboard) girdileri.

Shipment ve Delivery Note standart DocType'lar — kendi dashboard'ları ERPNext'ten
geliyor. Bu yüzden onları `override_doctype_dashboards` ile genişletiyoruz.

Önemli: hook zincirleme çalışır. Her fonksiyon o ana kadar birikmiş `data`yı alır
ve **üstüne ekler**; hiçbir şeyi ezmez. Böylece ERPNext'in kendi bağlantıları ve
başka app'lerin eklediği bağlantılar korunur.
"""

import frappe
from frappe import _


def _add_group(data, label, items, fieldname):
	"""Bir bağlantı grubunu data'ya ekle (varsa üstüne yaz değil, birleştir).

	fieldname: bu DocType'ların geri işaret ettiği alan adı. data'nın varsayılan
	fieldname'i farklıysa non_standard_fieldnames'e yazılır — yoksa Frappe yanlış
	alanda arar ve bağlantı boş görünür.
	"""
	data.setdefault("transactions", [])
	data.setdefault("non_standard_fieldnames", {})

	if data.get("fieldname") != fieldname:
		for dt in items:
			data["non_standard_fieldnames"].setdefault(dt, fieldname)

	for group in data["transactions"]:
		if group.get("label") == label:
			merged = list(group.get("items", [])) + list(items)
			# sırayı koruyarak tekilleştir
			group["items"] = list(dict.fromkeys(merged))
			return data

	data["transactions"].append({"label": label, "items": list(items)})
	return data


def get_shipment_dashboard_data(data=None):
	"""Shipment > Connections: kayıp ihbarı, kargo maliyeti, toplama manifestosu."""
	data = frappe._dict(data or {})
	data.setdefault("fieldname", "shipment")

	_add_group(data, _("Loss"), ["Shipment Loss Claim"], "shipment")
	_add_group(data, _("Cost"), ["Shipping Cost Entry"], "shipment")
	# Pickup Manifest'in bağı child tabloda (Pickup Manifest Item.shipment);
	# Frappe alt tabloları da tarar, alan adını vermek yeterli.
	_add_group(data, _("Pickup"), ["Pickup Manifest"], "shipment")
	return data


def get_delivery_note_dashboard_data(data=None):
	"""Delivery Note > Connections: bu teslimata ait kayıp ihbarı ve kargo maliyeti.

	ERPNext'in kendi girdilerine (Sales Invoice, Packing Slip, Installation Note…)
	dokunmaz, üstüne ekler.
	"""
	data = frappe._dict(data or {})
	data.setdefault("fieldname", "delivery_note")

	_add_group(data, _("Shipping"), ["Shipment Loss Claim", "Shipping Cost Entry"], "delivery_note")
	return data
