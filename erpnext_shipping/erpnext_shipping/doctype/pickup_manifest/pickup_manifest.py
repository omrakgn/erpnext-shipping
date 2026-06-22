# Copyright (c) 2026, Frappe and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import flt


class PickupManifest(Document):
	def validate(self):
		packages = {row.package_no for row in self.items if row.package_no}
		self.total_packages = len(packages)
		self.total_qty = sum(flt(row.qty) for row in self.items)


def get_company_logo_src(company=None):
	"""Şirket logosunu print/PDF için güvenli kaynak döndür.

	Private dosyalar wkhtmltopdf'te (PDF) görünmediğinden, dosya içeriği base64
	data URI olarak gömülür. Logosu olan şirket yoksa boş string döner.
	"""
	import base64
	import mimetypes

	if not company:
		company = frappe.db.get_value("Company", {"company_logo": ["is", "set"]}, "name") or frappe.db.get_value(
			"Company", {}, "name"
		)
	logo = frappe.db.get_value("Company", company, "company_logo") if company else None
	if not logo:
		return ""
	try:
		fdoc = frappe.get_all("File", filters={"file_url": logo}, fields=["name"], limit=1)
		if fdoc:
			content = frappe.get_doc("File", fdoc[0].name).get_content()
			if isinstance(content, str):
				content = content.encode()
			mime = mimetypes.guess_type(logo)[0] or "image/png"
			return f"data:{mime};base64,{base64.b64encode(content).decode()}"
	except Exception:
		pass
	return logo  # fallback: ham URL (public ise çalışır)


def _clean_contact(name):
	"""'Cathrin Ralfs-Cathrin Ralfs' gibi yinelenmiş ad-soyad'ı tekille (X-X -> X)."""
	name = (name or "").strip()
	if "-" in name:
		for i, ch in enumerate(name):
			if ch == "-":
				left, right = name[:i].strip(), name[i + 1 :].strip()
				if left and left == right:
					return left
	return name


def _parcel_item_map(sh):
	"""custom_parcel_items -> {parcel_no(int): {item_code: qty}} (yoksa boş dict)."""
	m = {}
	for row in sh.get("custom_parcel_items") or []:
		if not row.item_code or not row.parcel_no:
			continue
		pno = int(row.parcel_no)
		m.setdefault(pno, {})
		m[pno][row.item_code] = m[pno].get(row.item_code, 0) + flt(row.qty)
	return m


def _shipment_items(sh):
	"""Gönderiye bağlı DN'lerden ürünler (item_code ile birleştirilmiş)."""
	dn_names = list({d.delivery_note for d in (sh.get("shipment_delivery_note") or []) if d.delivery_note})
	merged, order = {}, []
	for dn in dn_names:
		for it in frappe.get_all(
			"Delivery Note Item",
			filters={"parent": dn},
			fields=["item_code", "qty"],
			order_by="idx",
		):
			code = it.item_code
			if not code:
				continue
			if code not in merged:
				merged[code] = 0
				order.append(code)
			merged[code] += flt(it.qty)
	return [{"item_code": c, "qty": merged[c]} for c in order]


@frappe.whitelist()
def generate_pickup_manifests(pickup_date, company=None):
	"""Belirli bir pickup tarihindeki gönderileri carrier'a göre gruplayıp her
	carrier için ayrı bir Pickup Manifest (kurye teslim tutanağı) oluştur.

	Her Shipment Parcel (koli) ayrı bir paket satırı olur; bir kolideki ürünler
	o paketin altında listelenir. Daha önce bir manifestoya eklenmiş gönderiler
	atlanır. Returns: [{name, carrier, packages}].
	"""
	shipments = frappe.get_all(
		"Shipment",
		filters={
			"pickup_date": pickup_date,
			"docstatus": ["<", 2],
			"carrier": ["is", "set"],
		},
		fields=["name", "carrier"],
		order_by="carrier asc, name asc",
	)
	if not shipments:
		frappe.throw(_("No shipments with a carrier found for the selected pickup date."))

	already = {
		r.shipment
		for r in frappe.get_all(
			"Pickup Manifest Item", filters={"shipment": ["is", "set"]}, fields=["shipment"]
		)
	}

	by_carrier = {}
	for sh in shipments:
		if sh.name in already:
			continue
		by_carrier.setdefault(sh.carrier, []).append(sh.name)

	if not by_carrier:
		frappe.throw(_("All shipments for this date are already in a pickup manifest."))

	fallback_company = (
		company
		or frappe.defaults.get_user_default("Company")
		or frappe.db.get_single_value("Global Defaults", "default_company")
		or frappe.db.get_value("Company", {"company_logo": ["is", "set"]}, "name")
		or frappe.db.get_value("Company", {}, "name")
	)

	created = []
	for carrier, names in by_carrier.items():
		# Manifesto şirketi = gönderen (pickup) şirket; yoksa logolu/ilk şirkete düş
		mfst_company = frappe.db.get_value("Shipment", names[0], "pickup_company") or fallback_company
		manifest = frappe.new_doc("Pickup Manifest")
		manifest.pickup_date = pickup_date
		manifest.carrier = carrier
		manifest.company = mfst_company

		package_no = 0
		for name in names:
			sh = frappe.get_doc("Shipment", name)
			contact = _clean_contact(sh.get("delivery_contact_name"))
			trackings = [t for t in (sh.awb_number or "").split(", ") if t]
			pmap = _parcel_item_map(sh)
			parcels = sh.get("shipment_parcel") or []
			all_items = _shipment_items(sh)

			def _add(pkg_no, tracking, item_code, qty):
				manifest.append(
					"items",
					{
						"package_no": pkg_no,
						"shipment": name,
						"tracking_number": tracking,
						"contact_person": contact,
						"carrier": carrier,
						"item_code": item_code,
						"qty": qty,
					},
				)

			if parcels:
				track_idx = 0
				for i, prow in enumerate(parcels, start=1):
					for _c in range(int(prow.count or 1)):
						package_no += 1
						tracking = trackings[track_idx] if track_idx < len(trackings) else (sh.awb_number or "")
						track_idx += 1
						if pmap:
							rows = [{"item_code": c, "qty": q} for c, q in (pmap.get(i) or {}).items()]
						elif len(parcels) == 1:
							rows = all_items
						else:
							rows = []
						if not rows:
							rows = [{"item_code": "", "qty": 0}]
						for it in rows:
							_add(package_no, tracking, it["item_code"], it["qty"])
			else:
				package_no += 1
				tracking = sh.awb_number or ""
				rows = all_items or [{"item_code": "", "qty": 0}]
				for it in rows:
					_add(package_no, tracking, it["item_code"], it["qty"])

		manifest.insert(ignore_permissions=True)
		created.append({"name": manifest.name, "carrier": carrier, "packages": package_no})

	return created
