// Gönderi ekranı: durum kartı, irsaliye listesi, sorunlar (docs/plans/gonderi-ekrani.md).
// Saf fonksiyonlar: belgeyi okur, HTML döndürür. Düğmeleri shipment.js bağlar.
// Node'da test ediliyor; frappe yalnız provide, __, flt, escape_html ve str_to_user için.
frappe.provide("erpnext_shipping.shipment_view");

(function (ns) {
	const OWN_TRANSPORT = ["Own Vehicle", "Transport Company"];

	const esc = (s) => frappe.utils.escape_html(s == null ? "" : String(s));
	const day = (v) => (v ? frappe.datetime.str_to_user(String(v).slice(0, 10)) : "");
	const pill = (text, color, extra) =>
		`<span class="indicator-pill ${color}${extra ? " " + extra : ""}">${esc(text)}</span>`;

	ns.ACTION_LABELS = {
		submit: "Submit",
		get_label: "Get Rates & Label",
		print_label: "Print Label",
		track: "Track",
		update_tracking: "Update Tracking",
		ask_delay: "Ask Carrier about Delay",
		loss_claim: "File Loss Claim",
		print_pickup_note: "Print Delivery Pickup Note",
		mark_delivered: "Mark Delivered",
		problems: "See Problems",
		populate: "Fill Parcels from Delivery Notes",
	};

	ns.is_own_transport = (doc) => OWN_TRANSPORT.includes(doc.shipping_transport_mode || "");
	ns.is_delivered = (doc) =>
		Boolean(doc.custom_delivered_at) || doc.tracking_status === "Delivered";
	ns.is_returned = (doc) =>
		Boolean(doc.custom_returned_to_sender) || doc.tracking_status === "Returned";
	ns.is_lost = (doc) => Boolean(doc.custom_presumed_lost) || doc.tracking_status === "Lost";
	ns.has_label = (doc) => Boolean(doc.shipment_id || doc.awb_number);
	ns.expected = (doc) => doc.custom_sla_date || doc.custom_promised_delivery_date || null;

	// Yeni gönderide ERPNext alış adresine şirketin fatura adresini koyuyor, bazen
	// varsayılanı yazan zamanlayıcılardan sonra. O adres gelince varsayılan alış adresi
	// döner; elle seçilen başka bir adres kalır. Sunucu da kayıtta aynısını yapıyor
	// (shipping.py::apply_default_pickup_address). Dönen: yazılacak adres ya da null.
	ns.pickup_override = function (value, defaults, guard_active) {
		const d = defaults || {};
		if (!guard_active || !d.default_pickup_address) return null;
		if (value === d.default_pickup_address) return null;
		if (value && !(d.company_billing_addresses || []).includes(value)) return null;
		return d.default_pickup_address;
	};

	// Onayı bugün de durduran eksikler; yeni kural değil, yalnız önceden söylenmesi.
	ns.missing = function (doc) {
		const out = [];
		const parcels = doc.shipment_parcel || [];
		if (!parcels.length) {
			out.push({ field: "shipment_parcel", label: __("parcels") });
		} else if (parcels.some((p) => !(flt(p.weight) > 0))) {
			out.push({ field: "shipment_parcel", label: __("a parcel without weight") });
		}
		if (!doc.pickup_address_name)
			out.push({ field: "pickup_address_name", label: __("pickup address") });
		if (!doc.delivery_address_name)
			out.push({ field: "delivery_address_name", label: __("delivery address") });
		if (!doc.delivery_contact_name)
			out.push({ field: "delivery_contact_name", label: __("delivery contact") });
		if (!doc.pickup_date) out.push({ field: "pickup_date", label: __("pickup date") });
		return out;
	};

	// Gönderinin şu anki sorunları; Problems sekmesi ve kartın sorun satırı aynı listeyi okur.
	ns.problems = function (doc) {
		if (doc.docstatus !== 1) return [];
		const out = [];
		const delivered = ns.is_delivered(doc);
		if (ns.is_returned(doc)) {
			let text = doc.custom_returned_on
				? __("Came back to us on {0}", [day(doc.custom_returned_on)])
				: __("Came back to us");
			if (doc.custom_return_reason) text += ` (${doc.custom_return_reason})`;
			out.push({ key: "returned", text: text, color: "red" });
		}
		if (ns.is_lost(doc))
			out.push({
				key: "lost",
				text: __("Presumed lost"),
				color: "red",
				action: "loss_claim",
			});
		if (doc.custom_label_removed) {
			out.push({
				key: "label_removed",
				text: __("Label deleted in SendCloud"),
				color: "red",
			});
		}
		if (!delivered && !ns.is_returned(doc)) {
			const exp = ns.expected(doc);
			const late = exp ? __("Late: expected {0}", [day(exp)]) : __("Late");
			if (doc.custom_is_delayed || doc.custom_sla_status === "Breached") {
				out.push({
					key: "late",
					text: late,
					color: "orange",
					action: doc.awb_number ? "ask_delay" : null,
				});
			} else if (doc.custom_sla_status === "At Risk") {
				out.push({
					key: "at_risk",
					text: exp
						? __("May arrive late: expected {0}", [day(exp)])
						: __("May arrive late"),
					color: "yellow",
				});
			}
			if (doc.custom_tracking_stalled) {
				out.push({
					key: "stalled",
					text: __("Carrier stopped reporting"),
					color: "orange",
					action: doc.shipment_id ? "update_tracking" : null,
				});
			}
		}
		return out;
	};

	// Tek cümle ve sıradaki adım. Yukarıdan aşağı ilk tutan kazanır (plan §5).
	ns.state = function (doc) {
		const ret = Boolean(doc.custom_is_return);
		const exp = ns.expected(doc);
		if (doc.docstatus === 2 || doc.status === "Cancelled") {
			return { key: "cancelled", color: "gray", text: __("Cancelled"), actions: [] };
		}
		if (!doc.docstatus) {
			const missing = ns.missing(doc);
			if (missing.length) {
				return {
					key: "not_ready",
					color: "orange",
					text: __("Not ready to submit"),
					missing: missing,
					actions: [],
				};
			}
			return {
				key: "ready",
				color: "blue",
				text: __("Ready to submit"),
				actions: ["submit"],
			};
		}
		if (ns.is_returned(doc)) {
			const p = ns.problems(doc).find((x) => x.key === "returned");
			return { key: "returned", color: "red", text: p.text, actions: ["problems"] };
		}
		if (ns.is_lost(doc)) {
			return {
				key: "lost",
				color: "red",
				text: __("Presumed lost"),
				actions: ["loss_claim"],
			};
		}
		if (doc.custom_label_removed) {
			return {
				key: "label_removed",
				color: "red",
				text: __("Label deleted in SendCloud"),
				actions: ["problems"],
			};
		}
		if (ns.is_delivered(doc)) {
			const on = doc.custom_delivered_at ? day(doc.custom_delivered_at) : "";
			let text = ret
				? on
					? __("Arrived at our warehouse on {0}", [on])
					: __("Arrived at our warehouse")
				: on
				? __("Delivered on {0}", [on])
				: __("Delivered");
			if (doc.shipping_received_by)
				text += " · " + __("received by {0}", [doc.shipping_received_by]);
			return { key: "delivered", color: "green", text: text, actions: [] };
		}
		if (ns.is_own_transport(doc)) {
			return {
				key: "own_delivery",
				color: "blue",
				text: __("Out for own delivery"),
				actions: ["mark_delivered", "print_pickup_note"],
			};
		}
		if (!ns.has_label(doc)) {
			return {
				key: "no_label",
				color: "orange",
				text: ret ? __("Waiting for a return label") : __("Waiting for a label"),
				actions: ["get_label"],
			};
		}
		if (!doc.tracking_status) {
			if (ret) {
				return {
					key: "waiting_customer",
					color: "blue",
					text: __("Waiting for the customer to hand it over"),
					actions: [],
				};
			}
			return {
				key: "label_ready",
				color: "blue",
				text: doc.pickup_date
					? __("Label ready, waiting for pickup on {0}", [day(doc.pickup_date)])
					: __("Label ready, waiting for pickup"),
				actions: doc.shipment_id ? ["print_label"] : [],
			};
		}
		if (doc.custom_is_delayed || doc.custom_sla_status === "Breached") {
			return {
				key: "late",
				color: "orange",
				text: exp ? __("Late: expected {0}", [day(exp)]) : __("Late"),
				actions: doc.awb_number ? ["ask_delay"] : [],
			};
		}
		if (doc.custom_tracking_stalled) {
			return {
				key: "stalled",
				color: "orange",
				text: __("Carrier stopped reporting"),
				actions: doc.shipment_id ? ["update_tracking"] : [],
			};
		}
		return {
			key: "on_the_way",
			color: "blue",
			text: exp ? __("On the way · expected {0}", [day(exp)]) : __("On the way"),
			actions: doc.tracking_url ? ["track"] : doc.shipment_id ? ["update_tracking"] : [],
		};
	};

	// Adres gösterimi (ERPNext'in biçimlediği HTML) tek satıra: ilk iki satır.
	ns.short_address = function (display) {
		const text = String(display || "")
			.replace(/<br\s*\/?>/gi, "\n")
			.replace(/<[^>]+>/g, "")
			.replace(/&amp;/g, "&")
			.replace(/&lt;/g, "<")
			.replace(/&gt;/g, ">")
			.replace(/&quot;/g, '"')
			.replace(/&#39;/g, "'");
		return text
			.split("\n")
			.map((s) => s.trim())
			.filter(Boolean)
			.slice(0, 2)
			.join(", ");
	};

	ns.contact = function (display) {
		const text = ns.short_address(String(display || "").replace(/\n/g, "<br>"));
		const name = text.split(",")[0].trim();
		const raw = String(display || "").replace(/<[^>]+>/g, " ");
		const phone = (raw.match(/\+?\d[\d\s\-()/.]{6,}\d/) || [""])[0].trim();
		return { name: name, phone: phone };
	};

	const button = (action, primary) =>
		`<button class="btn btn-sm ${primary ? "btn-primary" : "btn-default"}" data-action="${esc(
			action
		)}">${esc(__(ns.ACTION_LABELS[action]))}</button>`;

	ns.STYLE = `<style>
		.shp-card { border: 1px solid var(--border-color, #d1d8dd); border-radius: var(--border-radius-md, 8px);
			padding: 12px 14px; background: var(--card-bg, #fff); margin-bottom: 4px; }
		.shp-head { display: flex; flex-wrap: wrap; align-items: center; justify-content: space-between; gap: 8px 12px; }
		.shp-status.indicator-pill { font-size: var(--text-lg, 15px); height: auto; padding: 5px 12px; white-space: normal; line-height: 1.35; }
		.shp-actions { display: flex; flex-wrap: wrap; gap: 6px; }
		.shp-line { margin-top: 8px; color: var(--text-color, #1f272e); overflow-wrap: anywhere; }
		.shp-line .shp-k { color: var(--text-muted, #6c7680); margin-right: 6px; }
		.shp-card a[data-action], .shp-card a[data-goto] { color: var(--text-color, #1f272e);
			text-decoration: underline; cursor: pointer; }
		.shp-list { margin: 0; padding: 0; list-style: none; }
		.shp-list li { display: flex; flex-wrap: wrap; align-items: center; gap: 8px; padding: 6px 0;
			border-bottom: 1px solid var(--border-color, #d1d8dd); }
		.shp-list li:last-child { border-bottom: 0; }
		.shp-list .shp-grow { flex: 1 1 auto; min-width: 0; overflow-wrap: anywhere; }
		.shp-sum { margin: 0 0 6px; }
		.shp-parcels li { display: block; }
		.shp-row { display: flex; flex-wrap: wrap; justify-content: space-between; align-items: center; gap: 6px 12px; }
		.shp-phead { display: inline-flex; flex-wrap: wrap; align-items: center; gap: 6px; }
		.shp-sub { margin-top: 4px; overflow-wrap: anywhere; font-variant-numeric: tabular-nums; }
		.shp-num { font-variant-numeric: tabular-nums; font-weight: 500; }
		@media (max-width: 576px) {
			.shp-actions { width: 100%; }
			.shp-actions .btn { flex: 1 1 100%; }
		}
	</style>`;

	ns.summary_html = function (doc) {
		const st = ns.state(doc);
		const parts = [];
		const actions = (st.actions || []).map((a, i) => button(a, i === 0)).join("");
		parts.push(
			`<div class="shp-head">${pill(st.text, st.color, "shp-status")}${
				actions ? `<div class="shp-actions">${actions}</div>` : ""
			}</div>`
		);

		if (st.missing) {
			const links = st.missing
				.map((m) => `<a data-goto="${esc(m.field)}">${esc(m.label)}</a>`)
				.join(", ");
			parts.push(
				`<div class="shp-line shp-missing"><span class="shp-k">${esc(
					__("Missing:")
				)}</span>${links}</div>`
			);
		}

		// Nasıl gidiyor: taşıyıcı ve takip, ya da etiketsiz taşıma.
		let how = [];
		if (ns.is_own_transport(doc)) {
			how = [__(doc.shipping_transport_mode)];
			if (doc.carrier && doc.carrier !== doc.shipping_transport_mode) how.push(doc.carrier);
		} else {
			if (doc.carrier) how.push(doc.carrier);
			if (doc.carrier_service) how.push(doc.carrier_service);
			if (doc.awb_number) how.push(doc.awb_number);
			if (doc.tracking_status_info) how.push(__("last: {0}", [doc.tracking_status_info]));
		}
		if (how.length) parts.push(`<div class="shp-line">${how.map(esc).join(" · ")}</div>`);

		// Nereden nereye.
		const from = ns.short_address(doc.pickup_address);
		const to = ns.short_address(doc.delivery_address);
		const who = ns.contact(doc.delivery_contact);
		if (from || to || who.name) {
			const dest = [who.name, to].filter(Boolean).join(", ");
			const phone = who.phone ? ` (${esc(who.phone)})` : "";
			parts.push(
				`<div class="shp-line">${esc(from || "?")} → ${esc(dest || "?")}${phone}</div>`
			);
		}

		// Ne gidiyor.
		const parcels = doc.shipment_parcel || [];
		const count = parcels.reduce((s, p) => s + (flt(p.count) || 1), 0);
		const kg =
			flt(doc.total_weight) ||
			parcels.reduce((s, p) => s + flt(p.weight) * (flt(p.count) || 1), 0);
		const what = [];
		if (doc.description_of_content) {
			const d = String(doc.description_of_content).replace(/\s+/g, " ").trim();
			what.push(d.length > 140 ? d.slice(0, 137) + "..." : d);
		}
		if (count) {
			what.push(
				(count === 1 ? __("1 parcel") : __("{0} parcels", [count])) +
					(kg ? ", " + format_number(kg, null, kg % 1 ? 1 : 0) + " kg" : "")
			);
		}
		if (what.length) parts.push(`<div class="shp-line">${what.map(esc).join(" · ")}</div>`);

		if (ns.is_own_transport(doc) && doc.shipping_driver_note) {
			parts.push(
				`<div class="shp-line"><span class="shp-k">${esc(
					__("Note for the driver:")
				)}</span>${esc(doc.shipping_driver_note)}</div>`
			);
		}

		// Kartın kendi durumu olmayan sorunlar: sayı ve Problems'a bağlantı.
		const others = ns.problems(doc).filter((p) => p.key !== st.key);
		if (others.length) {
			const label =
				others.length === 1
					? __("1 problem: {0}", [others[0].text])
					: __("{0} problems", [others.length]);
			parts.push(
				`<div class="shp-line">${pill(
					label,
					others[0].color
				)} <a data-action="problems">${esc(__("See Problems"))}</a></div>`
			);
		}
		return `${ns.STYLE}<div class="shp-card">${parts.join("")}</div>`;
	};

	ns.problems_html = function (doc) {
		const list = ns.problems(doc);
		if (!list.length) {
			return `${ns.STYLE}<p class="text-muted">${esc(
				__("No problems. Returns, delays and losses show up here.")
			)}</p>`;
		}
		const rows = list
			.map(
				(p) =>
					`<li>${pill(p.text, p.color)}<span class="shp-grow"></span>${
						p.action ? button(p.action, true) : ""
					}</li>`
			)
			.join("");
		return `${ns.STYLE}<ul class="shp-list">${rows}</ul>`;
	};

	// Gönderinin irsaliye tablosu her ürün satırı için bir satır tutuyor (ERPNext'in
	// "Create > Shipment"i); burada her irsaliye bir kez.
	ns.delivery_notes = function (doc) {
		const seen = new Map();
		(doc.shipment_delivery_note || []).forEach((r) => {
			if (!r.delivery_note) return;
			seen.set(r.delivery_note, (seen.get(r.delivery_note) || 0) + 1);
		});
		return Array.from(seen, ([name, lines]) => ({ name, lines }));
	};

	ns.delivery_notes_html = function (doc) {
		const notes = ns.delivery_notes(doc);
		if (!notes.length) {
			return `${ns.STYLE}<p class="text-muted">${esc(
				__("No delivery note on this shipment.")
			)}</p>`;
		}
		const rows = notes
			.map(
				(n) =>
					`<li><a href="/app/delivery-note/${encodeURIComponent(n.name)}">${esc(
						n.name
					)}</a>` +
					`<span class="shp-grow text-muted">${esc(
						n.lines === 1 ? __("1 product line") : __("{0} product lines", [n.lines])
					)}</span></li>`
			)
			.join("");
		const fill = !doc.docstatus
			? `<div class="shp-actions" style="margin-top:8px;">${button("populate", false)}</div>`
			: "";
		return `${ns.STYLE}<ul class="shp-list">${rows}</ul>${fill}`;
	};

	// Taşıyıcının koli durumu serbest metin ("Parcel at depot Gent", "Bezorgd"); renk
	// anahtar kelimeden. Bilinmeyen her şey "yolda" (mavi), boş olan gri.
	ns.parcel_status_color = function (status) {
		const s = String(status || "").toLowerCase();
		if (!s) return "gray";
		if (/(return|refus|retour|geweigerd|verweigert|lost|verloren)/.test(s)) return "red";
		if (/(not delivered|failed|attempt|mislukt|nicht zugestellt)/.test(s)) return "orange";
		if (/(deliver|bezorgd|afgeleverd|zugestellt)/.test(s)) return "green";
		return "blue";
	};

	// Koli dökümü: koli başına bir satır, dar ekranda alt alta (2026-10-17: altı sütunlu
	// tablo telefonda sayfayı yana kaydırıyordu). Satırlar get_shipment_parcel_breakdown'dan.
	ns.parcel_breakdown_html = function (rows) {
		rows = rows || [];
		if (!rows.length)
			return `${ns.STYLE}<p class="text-muted">${esc(__("No parcels yet."))}</p>`;
		const items = rows
			.map((p, i) => {
				const number = p.tracking_url
					? `<a href="${encodeURI(p.tracking_url)}" target="_blank" rel="noopener">${esc(
							p.tracking_number || __("Track")
					  )}</a>`
					: esc(p.tracking_number || "");
				const head = [
					`<strong>${esc(__("Parcel {0}", [i + 1]))}</strong>`,
					pill(p.status || __("No status yet"), ns.parcel_status_color(p.status)),
				];
				if (p.label_removed) head.push(pill(__("Label removed"), "red"));
				const cost =
					p.cost || p.cost === 0
						? `<span class="shp-num">${esc(
								format_currency(p.cost, p.currency || "EUR")
						  )}</span>`
						: `<span class="text-muted">${esc(__("no invoice yet"))}</span>`;
				const sub = [number, esc(p.carrier || "")].filter(Boolean);
				if (p.delivered_at) sub.push(esc(__("delivered {0}", [day(p.delivered_at)])));
				return (
					`<li><div class="shp-row"><span class="shp-phead">${head.join(
						" "
					)}</span>${cost}</div>` +
					`<div class="shp-sub text-muted">${sub.join(" · ")}</div></li>`
				);
			})
			.join("");
		let summary = "";
		if (rows.length > 1) {
			const total = rows.reduce((s, p) => s + flt(p.cost), 0);
			const delivered = rows.filter(
				(p) => ns.parcel_status_color(p.status) === "green"
			).length;
			summary = `<p class="text-muted shp-sum">${esc(
				__("{0} parcels · {1} delivered · {2}", [
					rows.length,
					delivered,
					format_currency(total, rows[0].currency || "EUR"),
				])
			)}</p>`;
		}
		return `${ns.STYLE}${summary}<ul class="shp-list shp-parcels">${items}</ul>`;
	};
})(erpnext_shipping.shipment_view);
