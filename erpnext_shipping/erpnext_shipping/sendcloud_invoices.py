# Copyright (c) 2026, Frappe and contributors
# For license information, please see license.txt
"""SendCloud invoices, fetched from SendCloud instead of imported by hand.

docs/plans/kargo-maliyet-ekrani-ve-itiraz.md §4.7. The invoice list and its
lines come from SendCloud's API v3; each line names its parcel (`parcel_id`) and
carries the tracking number as `reference`. Lines are grouped per parcel into the
same Shipping Cost Entry the CSV import makes, under the same name
(`{tracking}-{invoice}`): an invoice read here that was already imported as a CSV
is updated, not added twice. Checked on the live account (2026-10-16): the API's
invoice `reference` is the CSV's file name and the totals match to the cent.

Each parcel is also read once (`/v2/parcels/{id}`) for what the invoice does not
say: the label's weight and size, the contract it went on, the order number and
the receiver. The contract says who bills the freight and who a dispute goes to
(§4.6). That data is written onto every cost entry of the parcel, including the
carrier's own freight line when the carrier bills it (a DPD parcel on our own
contract: DPD bills the freight, SendCloud the fee per parcel).

Only invoices from `sendcloud_invoice_sync_from` (default 2026-10-01) are read:
older ones were imported by hand, and one of them was saved under another file
name, which this would add a second time.

Network first, writes after, commit per invoice: no transaction stays open while
SendCloud answers or while a throttled request waits (docs/olay-islem-icinde-bekleme.md).
"""

import time

import frappe
import requests
from frappe import _
from frappe.utils import flt, getdate, now_datetime

from erpnext_shipping.erpnext_shipping.shipping_cost import (
	SC_KNOWN_CARRIERS,
	SC_WEIGHT_SURCHARGE_HINTS,
	SENDCLOUD_PARCEL_FIELDS,
	_finalize_import,
	_new_stats,
	_norm_parcel,
	_stats_summary,
	_upsert_cost_entry,
	build_match_context,
)

API = "https://panel.sendcloud.sc/api"
SETTINGS = "Shipping Cost Settings"
DEFAULT_SINCE = "2026-10-01"
# A normal day brings one or two invoices. More at once means the start date is
# wrong (2026-10-16: the whole history since 2022 came in); nothing is written.
MAX_INVOICES_PER_RUN = 10
TIMEOUT = 30
MAX_TRIES = 4
TYPE_FREIGHT = "shipment"
CATEGORY_SUBSCRIPTION = "subscription"
SURCHARGE_TYPES = ("surcharge", "surcharge_refund")
DIMENSIONAL_SUBTYPES = ("dimensional",)
CARRIER_NAMES = {
	"dpd": "DPD",
	"fedex": "FedEx",
	"gls_eu": "GLS",
	"gls": "GLS",
	"ups": "UPS",
	"postnl": "PostNL",
	"dhl": "DHL",
	"bpost": "Bpost",
	"colissimo": "Colissimo",
	"hermes": "Hermes",
	"correos": "Correos",
}


# ---------------------------------------------------------------------------
# SendCloud
# ---------------------------------------------------------------------------
def _auth():
	from erpnext_shipping.erpnext_shipping.doctype.sendcloud.sendcloud import SendCloudUtils

	utils = SendCloudUtils()
	return (utils.api_key, utils.api_secret), utils


def _get(url, auth, params=None):
	"""GET with a retry on throttling. Commits before waiting: nothing stays locked."""
	for deneme in range(MAX_TRIES):
		r = requests.get(
			url, auth=auth, params=params, timeout=TIMEOUT, headers={"Accept": "application/json"}
		)
		if r.status_code != 429:
			r.raise_for_status()
			return r
		frappe.db.commit()
		time.sleep(min(int(r.headers.get("Retry-After") or 2 ** (deneme + 1)), 60))
	r.raise_for_status()
	return r


def _paged(path, auth, params):
	"""Every row of a v3 list, following the `Link: rel=next` header."""
	url, sayfa = API + path, dict(params)
	while url:
		r = _get(url, auth, sayfa)
		yield from (r.json() or {}).get("data") or []
		url = (r.links.get("next") or {}).get("url")
		sayfa = None


def sync_from():
	"""Start date of the fetch: the setting, else DEFAULT_SINCE.

	Read raw from tabSingles. `get_single_value` casts an empty Date field to
	0001-01-01, which is truthy, so `... or DEFAULT_SINCE` never applied and the
	first run fetched every invoice since 2022 (2026-10-16).
	"""
	deger = frappe.db.sql(
		"select value from `tabSingles` where doctype = %s and field = %s",
		(SETTINGS, "sendcloud_invoice_sync_from"),
	)
	deger = deger[0][0] if deger else None
	return getdate(deger or DEFAULT_SINCE)


def new_invoices(auth, since):
	"""Invoices from `since` on that are not in ERPNext yet, oldest first.

	SendCloud lists the newest first. Reading stops after a page whose every
	invoice is older than `since`.
	"""
	since = getdate(since)
	sonuc, url, params = [], API + "/v3/invoices", {"page_size": 100}
	while url:
		r = _get(url, auth, params)
		sayfa = (r.json() or {}).get("data") or []
		hepsi_eski = bool(sayfa)
		for inv in sayfa:
			if getdate(str(inv.get("created_at"))[:10]) < since:
				continue
			hepsi_eski = False
			# Aylık ücret faturasında koli yok, maliyet kaydı da olmaz; atlanmasa her sabah
			# "yeni" sayılıp yeniden okunurdu.
			if inv.get("category") == CATEGORY_SUBSCRIPTION:
				continue
			if not frappe.db.exists("Shipping Cost Entry", {"invoice_number": inv.get("reference")}):
				sonuc.append(inv)
		if hepsi_eski:
			break
		url = (r.links.get("next") or {}).get("url")
		params = None
	sonuc.sort(key=lambda i: str(i.get("created_at")))
	return sonuc


def invoice_items(auth, invoice_id):
	return list(_paged("/v3/invoice-items", auth, {"invoice_id": invoice_id, "page_size": 500}))


def fetch_parcel(auth, parcel_id):
	"""{} when SendCloud no longer has the parcel: old parcels answer 404. The cost
	entry is still written, from the invoice line alone."""
	try:
		r = _get(f"{API}/v2/parcels/{parcel_id}", auth)
	except requests.HTTPError as e:
		if e.response is not None and e.response.status_code == 404:
			return {}
		raise
	return (r.json() or {}).get("parcel") or {}


# ---------------------------------------------------------------------------
# Lines -> Shipping Cost Entry values
# ---------------------------------------------------------------------------
def parcel_fields(parcel, contract_types):
	"""What one SendCloud parcel adds to its cost entries."""
	if not parcel:
		return {}
	bolunmus = parcel.get("address_divided") or {}
	sokak = " ".join(p for p in (bolunmus.get("street"), bolunmus.get("house_number")) if p) or (
		parcel.get("address") or ""
	)
	sokak = ", ".join(p for p in (sokak.strip(), (parcel.get("address_2") or "").strip()) if p)
	sozlesme = parcel.get("contract")
	if isinstance(sozlesme, dict):
		sozlesme = sozlesme.get("id")
	tur = contract_types.get(sozlesme)
	if tur is None and sozlesme is not None:
		tur = contract_types.get(str(sozlesme))
	return {
		"sendcloud_parcel_id": str(parcel.get("id") or ""),
		"sendcloud_weight": flt(parcel.get("weight")),
		"sendcloud_length": flt(parcel.get("length")),
		"sendcloud_width": flt(parcel.get("width")),
		"sendcloud_height": flt(parcel.get("height")),
		"contract_id": str(sozlesme) if sozlesme is not None else "",
		"contract_type": tur or "",
		"receiver_name": (parcel.get("name") or parcel.get("company_name") or "").strip(),
		"receiver_street": sokak,
		"receiver_zip": (parcel.get("postal_code") or "").strip(),
		"receiver_city": (parcel.get("city") or "").strip(),
		"country": ((parcel.get("country") or {}).get("iso_2") or "")
		if isinstance(parcel.get("country"), dict)
		else "",
		"reference_1": (parcel.get("order_number") or "").strip(),
	}


def _carrier(parcel, freight_description):
	kod = ((parcel or {}).get("carrier") or {}).get("code") or ""
	if kod:
		return CARRIER_NAMES.get(kod.lower(), kod.upper())
	tarif = (freight_description or "").lower()
	for ad in SC_KNOWN_CARRIERS:
		if tarif.startswith(ad.lower()):
			return ad
	return ""


def entry_values(invoice, lines, parcel, contract_types):
	"""One parcel's lines on one invoice, as Shipping Cost Entry values."""
	toplam, ek, agirlik_eki, kirilim, para = 0.0, 0.0, 0, {}, None
	navlun = None
	for s in lines:
		fiyat = s.get("price") or {}
		tutar = flt(fiyat.get("value"))
		para = para or fiyat.get("currency")
		toplam += tutar
		tarif = (s.get("description") or s.get("type") or "Charge").strip()
		kirilim[tarif] = flt(kirilim.get(tarif)) + tutar
		if s.get("type") in SURCHARGE_TYPES:
			ek += tutar
		if s.get("subtype") in DIMENSIONAL_SUBTYPES or any(
			h in tarif.lower() for h in SC_WEIGHT_SURCHARGE_HINTS
		):
			agirlik_eki = 1
		if navlun is None and s.get("type") == TYPE_FREIGHT:
			navlun = s

	tarihler = sorted(str(s.get("created_at"))[:10] for s in lines if s.get("created_at"))
	takip = (parcel or {}).get("tracking_number") or next(
		(s.get("reference") for s in lines if s.get("reference")), ""
	)
	degerler = {
		"parcel_number": _norm_parcel(takip),
		"invoice_number": invoice.get("reference"),
		"carrier": _carrier(parcel, (navlun or {}).get("description")),
		"billed_via": "SendCloud",
		"scan_date": getdate(tarihler[0]) if tarihler else getdate(str(invoice.get("created_at"))[:10]),
		"product_name": ((navlun or lines[0]).get("description") or "").strip(),
		"currency": para or "EUR",
		"total_net_amount": flt(toplam, 4),
		"surcharge_amount": flt(ek, 4),
		"weight_surcharge": agirlik_eki,
		"charge_breakdown": frappe.as_json(kirilim, indent=None),
		"source_file": f"SendCloud API invoice {invoice.get('id')}",
	}
	degerler.update(parcel_fields(parcel, contract_types))
	return degerler


def _group_by_parcel(lines):
	"""{key: [lines]} per parcel, and the lines that belong to no parcel."""
	gruplar, kolisiz = {}, []
	for s in lines:
		anahtar = s.get("parcel_id") or s.get("reference")
		if not anahtar:
			kolisiz.append(s)
			continue
		gruplar.setdefault(anahtar, []).append(s)
	return gruplar, kolisiz


def spread_parcel_fields(parcel_number, fields):
	"""Write a parcel's SendCloud data onto its other cost entries that lack it."""
	pn = _norm_parcel(parcel_number)
	if not pn or not fields.get("sendcloud_parcel_id"):
		return
	kisa = pn.lstrip("0") or pn
	atama = ", ".join(f"{f} = %({f})s" for f in SENDCLOUD_PARCEL_FIELDS)
	degerler = {f: fields.get(f) for f in SENDCLOUD_PARCEL_FIELDS}
	degerler.update({"pn": pn, "kisa": kisa})
	frappe.db.sql(
		f"""update `tabShipping Cost Entry` set {atama}
		where parcel_number in (%(pn)s, %(kisa)s) and ifnull(sendcloud_parcel_id, '') = ''""",
		degerler,
	)


# ---------------------------------------------------------------------------
# Runs
# ---------------------------------------------------------------------------
def import_api_invoice(invoice, auth, contract_types, ctx, stats, parcel_cache):
	"""Read one invoice and its parcels from SendCloud, then write it. Returns the
	lines that belong to no parcel (a monthly fee), which become no cost entry."""
	lines = invoice_items(auth, invoice["id"])
	gruplar, kolisiz = _group_by_parcel(lines)
	for satirlar in gruplar.values():
		pid = satirlar[0].get("parcel_id")
		if pid and pid not in parcel_cache:
			parcel_cache[pid] = fetch_parcel(auth, pid)

	for satirlar in gruplar.values():
		parcel = parcel_cache.get(satirlar[0].get("parcel_id")) or {}
		degerler = entry_values(invoice, satirlar, parcel, contract_types)
		if not degerler["parcel_number"]:
			kolisiz.extend(satirlar)
			continue
		_upsert_cost_entry(degerler, ctx, stats)
		spread_parcel_fields(degerler["parcel_number"], degerler)
	return kolisiz


def sync_sendcloud_invoices(manual=False, user=None):
	"""Scheduler (05:27) and the list's button: fetch the SendCloud invoices ERPNext lacks."""
	if not manual and frappe.db.get_single_value(SETTINGS, "pause_sendcloud_invoice_sync"):
		return None
	since = sync_from()

	stats = _new_stats()
	stats["invoices"], stats["no_parcel"] = [], []
	try:
		auth, utils = _auth()
		contract_types = utils._contract_types()
		invoices = new_invoices(auth, since)
	except Exception as e:  # noqa: BLE001 - SendCloud'a ulaşılamazsa iş düşmez, sonuç ayara yazılır
		frappe.db.rollback()
		frappe.log_error(title="SendCloud invoice fetch failed", message=frappe.get_traceback())
		return _finish(stats, _("SendCloud could not be read: {0}").format(e), user)

	if len(invoices) > MAX_INVOICES_PER_RUN:
		return _finish(
			stats,
			_(
				"{0} SendCloud invoices from {1} on are missing in ERPNext. More than {2} at once is not"
				" fetched: check Fetch Invoices From. Nothing was written."
			).format(len(invoices), since, MAX_INVOICES_PER_RUN),
			user,
		)

	ctx = build_match_context() if invoices else None
	parcel_cache = {}
	for inv in invoices:
		try:
			kolisiz = import_api_invoice(inv, auth, contract_types, ctx, stats, parcel_cache)
			frappe.db.commit()
			stats["invoices"].append(inv.get("reference"))
			for s in kolisiz:
				stats["no_parcel"].append(
					f"{inv.get('reference')}: {s.get('description')} {(s.get('price') or {}).get('value')}"
				)
		except Exception as e:  # noqa: BLE001 - bir fatura ötekileri durdurmaz
			frappe.db.rollback()
			stats["errors"].append(f"{inv.get('reference')}: {e}")
			frappe.log_error(
				title=f"SendCloud invoice {inv.get('reference')}", message=frappe.get_traceback()
			)

	_finalize_import(stats)
	return _finish(stats, None, user)


def _finish(stats, failure, user):
	ozet = _stats_summary(stats)
	ozet["invoices"] = stats.get("invoices") or []
	ozet["no_parcel"] = stats.get("no_parcel") or []
	if failure:
		metin = failure
	elif not ozet["invoices"] and not ozet["errors"]:
		metin = _("No new SendCloud invoices.")
	else:
		metin = _("Invoices: {0}. Created: {1}, updated: {2}. Unmatched parcels: {3}.").format(
			", ".join(ozet["invoices"]) or "-", ozet["created"], ozet["updated"], ozet["unmatched_parcels"]
		)
		if ozet["no_parcel"]:
			metin += " " + _("Lines without a parcel (no cost entry): {0}.").format(
				"; ".join(ozet["no_parcel"][:10])
			)
		if ozet["errors"]:
			metin += " " + _("Errors: {0}.").format("; ".join(ozet["errors"][:10]))
	frappe.db.set_single_value(
		SETTINGS, {"sendcloud_last_sync": now_datetime(), "sendcloud_last_sync_result": metin[:1000]}
	)
	frappe.db.commit()
	if user:
		frappe.publish_realtime(
			"msgprint",
			{"title": _("SendCloud Invoices"), "message": frappe.utils.escape_html(metin)},
			user=user,
		)
	return ozet


@frappe.whitelist()
def fetch_now():
	"""The list's button: run the fetch now, in the background."""
	frappe.only_for(("System Manager", "Stock Manager", "Accounts Manager"))
	frappe.enqueue(
		"erpnext_shipping.erpnext_shipping.sendcloud_invoices.sync_sendcloud_invoices",
		queue="long",
		timeout=1800,
		job_id="sendcloud_invoice_sync",
		deduplicate=True,
		manual=True,
		user=frappe.session.user,
	)
	return _("Fetching SendCloud invoices in the background. You will see the result here when it is done.")


# ---------------------------------------------------------------------------
# One-off: SendCloud data for parcels imported before this existed
# ---------------------------------------------------------------------------
@frappe.whitelist()
def start_parcel_backfill():
	frappe.only_for("System Manager")
	frappe.enqueue(
		"erpnext_shipping.erpnext_shipping.sendcloud_invoices.backfill_parcel_fields",
		queue="long",
		timeout=7200,
		job_id="sendcloud_parcel_backfill",
		deduplicate=True,
	)
	return _("Started in the background.")


def backfill_parcel_fields():
	"""Label weight, size and contract for the cost entries of every SendCloud invoice
	already in ERPNext. Receivers stay as they are: those entries have theirs.

	Invoices are found by their reference; each invoice's lines give the parcel ids.
	One commit per invoice.
	"""
	auth, utils = _auth()
	contract_types = utils._contract_types()
	eksik = set(
		frappe.db.sql_list(
			"""select distinct invoice_number from `tabShipping Cost Entry`
			where billed_via = 'SendCloud' and ifnull(sendcloud_parcel_id, '') = ''"""
		)
	)
	if not eksik:
		return 0
	numaralar = {}
	for inv in _paged("/v3/invoices", auth, {"page_size": 100}):
		if inv.get("reference") in eksik:
			numaralar[inv["reference"]] = inv["id"]
	frappe.db.commit()

	yazilan, cache = 0, {}
	for ref, inv_id in numaralar.items():
		try:
			satirlar = invoice_items(auth, inv_id)
			gruplar, _kolisiz = _group_by_parcel(satirlar)
			hazir = []
			for grup in gruplar.values():
				pid = grup[0].get("parcel_id")
				if not pid:
					continue
				if pid not in cache:
					cache[pid] = fetch_parcel(auth, pid)
				parcel = cache[pid]
				takip = parcel.get("tracking_number") or grup[0].get("reference")
				alanlar = parcel_fields(parcel, contract_types)
				hazir.append((takip, alanlar))
			for takip, alanlar in hazir:
				spread_parcel_fields(takip, alanlar)
				yazilan += 1
			frappe.db.commit()
		except Exception:  # noqa: BLE001 - bir fatura ötekileri durdurmaz
			frappe.db.rollback()
			frappe.log_error(title=f"SendCloud parcel backfill {ref}", message=frappe.get_traceback())
	return yazilan
