// Copyright (c) 2020, Frappe and contributors
// For license information, please see license.txt

frappe.ui.form.on("Shipment", {
	onload: function (frm) {
		// Yeni Shipment'ta pickup tarih/saat varsayılanlarını (Shipment Settings) uygula.
		if (!frm.is_new()) return;
		frappe.call({
			method: "erpnext_shipping.erpnext_shipping.shipping.get_shipment_form_defaults",
			callback: function (r) {
				const s = r.message || {};
				if (s.set_pickup_date_today && !frm.doc.pickup_date) {
					frm.set_value("pickup_date", frappe.datetime.get_today());
				}
				if (s.set_default_pickup_time) {
					if (s.default_pickup_from && !frm.doc.pickup_from) {
						frm.set_value("pickup_from", s.default_pickup_from);
					}
					if (s.default_pickup_to && !frm.doc.pickup_to) {
						frm.set_value("pickup_to", s.default_pickup_to);
					}
				}
				// ERPNext, pickup tarafı Company olunca şirketin PRIMARY adres/contact'ını
				// otomatik dolduruyor. Bizim varsayılanımız bunu ezmeli (yeni formda
				// kullanıcı henüz seçim yapmadı). Form oturduktan sonra uygula ki
				// ERPNext'in geç çalışan dolduruşunun da ardına geçelim.
				if (s.default_pickup_address || s.default_pickup_contact_person) {
					frm.__apply_pickup_defaults = function () {
						if (
							s.default_pickup_address &&
							frm.doc.pickup_address_name !== s.default_pickup_address
						) {
							frm.set_value("pickup_address_name", s.default_pickup_address);
						}
						if (
							s.default_pickup_contact_person &&
							frm.doc.pickup_contact_person !== s.default_pickup_contact_person
						) {
							frm.set_value(
								"pickup_contact_person",
								s.default_pickup_contact_person
							);
						}
					};
					setTimeout(frm.__apply_pickup_defaults, 700);
					setTimeout(frm.__apply_pickup_defaults, 1500);
				}
			},
		});
	},

	refresh: function (frm) {
		if (!frm.is_new()) {
			frappe.call({
				method: "erpnext_shipping.erpnext_shipping.shipping.get_shipment_parcel_breakdown",
				args: { shipment: frm.doc.name },
				callback: function (r) {
					render_parcel_breakdown(frm, r.message || []);
				},
			});
		}
		// Description of Content zorunlu bir alan; client mandatory kontrolü sunucu
		// validate hook'undan önce çalıştığı için doldurmayı burada yapıyoruz.
		maybe_fill_description(frm);
		if (frm.doc.docstatus === 0 && (frm.doc.shipment_delivery_note || []).length) {
			frm.add_custom_button(__("Populate Parcels from Delivery Notes"), function () {
				const has_rows =
					(frm.doc.shipment_parcel || []).length ||
					(frm.doc.custom_parcel_items || []).length;
				const run = () => frm.events.populate_parcels(frm);
				if (has_rows) {
					frappe.confirm(
						__(
							"This will replace the existing Shipment Parcel and Parcel Items rows. Continue?"
						),
						run
					);
				} else {
					run();
				}
			});
		}
		// Gönderi kaydedilmiş, takip no var ve henüz teslim edilmemişse: carrier'a
		// gecikme sormak için hazır dolu bir e-posta taslağı aç.
		if (
			frm.doc.docstatus === 1 &&
			frm.doc.awb_number &&
			!frm.doc.custom_delivered_at &&
			frm.doc.tracking_status !== "Delivered"
		) {
			frm.add_custom_button(
				__("Ask Carrier about Delay"),
				function () {
					frappe.call({
						method: "erpnext_shipping.erpnext_shipping.delay.get_carrier_delay_email",
						args: { shipment: frm.doc.name },
						freeze: true,
						callback: function (r) {
							const d = r.message || {};
							new frappe.views.CommunicationComposer({
								doc: frm.doc,
								frm: frm,
								subject: d.subject || "",
								recipients: d.recipients || "",
								content: d.content || "",
								sender: d.sender || undefined,
							});
						},
					});
				},
				__("Delay")
			);
		}
		// Kayıp / teslim sorunu için tazminat talebi (Loss Claim) aç. Birden çok
		// parsel varsa hangisinin/hangilerinin kayıp olduğu seçilir; her seçilen
		// parsel için ayrı claim açılır.
		if (frm.doc.docstatus === 1 && frm.doc.awb_number) {
			const delivered = frm.doc.custom_delivered_at || frm.doc.tracking_status === "Delivered";
			const default_type = delivered ? "Delivered - Not Received" : "Not Delivered";
			frm.add_custom_button(
				__("File Loss Claim"),
				function () {
					file_loss_claim(frm, default_type);
				},
				__("Loss")
			);
		}
		if (frm.doc.docstatus === 1 && !frm.doc.shipment_id) {
			// SendCloud's rate endpoint only offers each carrier's default contract,
			// so a broker contract ("Sendcloud rates") never appears in the rate list
			// even though it can be picked in SendCloud's own panel. Without an
			// explicit choice SendCloud silently uses the account default.
			frm.add_custom_button(
				__("SendCloud Contract"),
				function () {
					frappe.call({
						method: "erpnext_shipping.erpnext_shipping.doctype.sendcloud.sendcloud.get_sendcloud_contracts",
						freeze: true,
						freeze_message: __("Loading contracts..."),
						callback: function (r) {
							const rows = r.message || [];
							if (!rows.length) {
								frappe.msgprint(__("No SendCloud contracts found."));
								return;
							}
							const options = [{ value: "", label: __("Account default") }].concat(
								rows.map((c) => ({
									value: String(c.id),
									label: `${c.label} — ${c.type}${
										c.state && c.state !== "active" ? " [" + c.state + "]" : ""
									}`,
								}))
							);
							const d = new frappe.ui.Dialog({
								title: __("SendCloud Contract"),
								fields: [
									{
										fieldname: "contract",
										label: __("Contract"),
										fieldtype: "Select",
										options: options,
										default: frm.doc.custom_sendcloud_contract_id || "",
										reqd: 0,
									},
									{
										fieldname: "info",
										fieldtype: "HTML",
										options: `<div class="text-muted small">${__(
											"broker = SendCloud's rates; SendCloud invoices us and a loss claim goes to SendCloud. direct = our own contract with the carrier; the carrier invoices us and the claim goes to the carrier. Leave on account default to let SendCloud decide."
										)}</div>`,
									},
								],
								primary_action_label: __("Set"),
								primary_action(values) {
									const picked = rows.find(
										(c) => String(c.id) === String(values.contract)
									);
									frm.set_value(
										"custom_sendcloud_contract_id",
										values.contract || ""
									);
									frm.set_value(
										"custom_sendcloud_contract",
										picked ? `${picked.label} — ${picked.type}` : ""
									);
									d.hide();
									frm.save();
								},
							});
							d.show();
						},
					});
				},
				__("Tools")
			);

			frm.add_custom_button(__("Fetch Shipping Rates"), function () {
				if (frm.doc.shipment_parcel.length > 1) {
					frappe.confirm(
						__(
							"If your shipment contains packages with varying weights, the estimated shipping rates may differ from the final price charged by your carrier. Do you wish to proceed?"
						),
						function () {
							frm.events.fetch_shipping_rates(frm);
						}
					);
				} else {
					frm.events.fetch_shipping_rates(frm);
				}
			});

			if ((frm.doc.shipment_delivery_note || []).length) {
				frm.add_custom_button(__("Sync SendCloud Label"), function () {
					frappe.call({
						method: "erpnext_shipping.erpnext_shipping.shipping.sync_sendcloud_label",
						freeze: true,
						freeze_message: __("Syncing SendCloud Label"),
						args: { shipment: frm.doc.name },
						callback: function (r) {
							if (!r.exc && r.message) {
								frm.reload_doc();
							}
						},
					});
				});
			}

			if ((frm.doc.shipment_delivery_note || []).length) {
				frm.add_custom_button(__("Fulfill SendCloud Order"), function () {
					frappe.confirm(
						__(
							"Find the SendCloud order matching this shipment's PO number, update it with the ERPNext weight/dimensions/carrier/contract and create the label. Continue?"
						),
						function () {
							frm.events.fulfill_sendcloud_order(frm);
						}
					);
				});
			}

			const has_per_parcel = (frm.doc.shipment_parcel || []).some(
				(p) => p.custom_shipping_option_code
			);
			if (has_per_parcel) {
				frm.add_custom_button(__("Create Shipment (Per-Parcel Carriers)"), function () {
					frappe.call({
						method: "erpnext_shipping.erpnext_shipping.shipping.create_shipment_per_parcel",
						freeze: true,
						freeze_message: __("Creating Shipment"),
						args: { shipment: frm.doc.name },
						callback: function (r) {
							if (!r.exc && r.message) {
								frm.reload_doc();
								frappe.msgprint({
									message: __("Shipment {0} created with per-parcel carriers.", [
										r.message.shipment_id ? r.message.shipment_id.bold() : "",
									]),
									title: __("Shipment Created"),
									indicator: "green",
								});
								frm.events.update_tracking(
									frm,
									r.message.service_provider,
									r.message.shipment_id
								);
							}
						},
					});
				});
			}
		}
		if (frm.doc.shipment_id) {
			frm.add_custom_button(
				__("Print Shipping Label"),
				function () {
					return frm.events.print_shipping_label(frm);
				},
				__("Tools")
			);
			if (frm.doc.tracking_status != "Delivered") {
				frm.add_custom_button(
					__("Update Tracking"),
					function () {
						return frm.events.update_tracking(
							frm,
							frm.doc.service_provider,
							frm.doc.shipment_id
						);
					},
					__("Tools")
				);

				frm.add_custom_button(
					__("Track Status"),
					function () {
						if (frm.doc.tracking_url) {
							const urls = frm.doc.tracking_url.split(", ");
							urls.forEach((url) => window.open(url));
						} else {
							let msg = __(
								"Please complete Shipment (ID: {0}) on {1} and Update Tracking.",
								[frm.doc.shipment_id, frm.doc.service_provider]
							);
							frappe.msgprint({ message: msg, title: __("Incomplete Shipment") });
						}
					},
					__("View")
				);
			}
		}
	},

	fetch_shipping_rates: function (frm) {
		if (!frm.doc.shipment_id) {
			frappe.call({
				method: "erpnext_shipping.erpnext_shipping.shipping.fetch_shipping_rates",
				freeze: true,
				freeze_message: __("Fetching Shipping Rates"),
				args: {
					pickup_from_type: frm.doc.pickup_from_type,
					delivery_to_type: frm.doc.delivery_to_type,
					pickup_address_name: frm.doc.pickup_address_name,
					delivery_address_name: frm.doc.delivery_address_name,
					parcels: frm.doc.shipment_parcel,
					description_of_content: frm.doc.description_of_content,
					pickup_date: frm.doc.pickup_date,
					pickup_contact_name:
						frm.doc.pickup_from_type === "Company"
							? frm.doc.pickup_contact_person
							: frm.doc.pickup_contact_name,
					delivery_contact_name: frm.doc.delivery_contact_name,
					value_of_goods: frm.doc.value_of_goods,
				},
				callback: function (r) {
					if (r.message && r.message.length) {
						select_from_available_services(frm, r.message);
					} else {
						frappe.msgprint({
							message: __("No Shipment Services available"),
							title: __("Note"),
						});
					}
				},
			});
		} else {
			frappe.throw(__("Shipment already created"));
		}
	},

	populate_parcels: function (frm) {
		if (frm.is_new() || frm.is_dirty()) {
			frappe.msgprint(__("Please save the Shipment before populating parcels."));
			return;
		}
		frappe.call({
			method: "erpnext_shipping.erpnext_shipping.shipping.populate_parcels_from_delivery_notes",
			freeze: true,
			freeze_message: __("Populating Parcels"),
			args: {
				shipment: frm.doc.name,
			},
			callback: function (r) {
				if (!r.exc) {
					frm.reload_doc();
				}
			},
		});
	},

	print_shipping_label: function (frm) {
		frappe.call({
			method: "erpnext_shipping.erpnext_shipping.shipping.print_shipping_label",
			freeze: true,
			freeze_message: __("Printing Shipping Label"),
			args: {
				shipment: frm.doc.name,
			},
			callback: function (r) {
				if (r.message) {
					if (frm.doc.service_provider == "LetMeShip") {
						var array = JSON.parse(r.message);
						// Uint8Array for unsigned bytes
						array = new Uint8Array(array);
						const file = new Blob([array], { type: "application/pdf" });
						const file_url = URL.createObjectURL(file);
						window.open(file_url);
					} else {
						if (Array.isArray(r.message)) {
							r.message.forEach((url) => window.open(url));
						} else {
							window.open(r.message);
						}
					}
				}
			},
		});
	},

	fulfill_sendcloud_order: function (frm) {
		frappe.call({
			method: "erpnext_shipping.erpnext_shipping.shipping.fulfill_sendcloud_order",
			freeze: true,
			freeze_message: __("Fulfilling SendCloud Order"),
			args: { shipment: frm.doc.name },
			callback: function (r) {
				if (!r.exc && r.message) {
					frm.reload_doc();
				}
			},
		});
	},

	update_tracking: function (frm, service_provider, shipment_id) {
		const delivery_notes = frm.doc.shipment_delivery_note.map((d) => d.delivery_note);

		frappe.call({
			method: "erpnext_shipping.erpnext_shipping.shipping.update_tracking",
			freeze: true,
			freeze_message: __("Updating Tracking"),
			args: {
				shipment: frm.doc.name,
				shipment_id: shipment_id,
				service_provider: service_provider,
				delivery_notes: delivery_notes,
			},
			callback: function (r) {
				if (!r.exc) {
					frm.reload_doc();
				}
			},
		});
	},
});

function maybe_fill_description(frm) {
	// Description boşsa ve bağlı Delivery Note varsa, ürün adlarından otomatik doldur.
	if (frm.doc.description_of_content) return;
	const dns = (frm.doc.shipment_delivery_note || [])
		.map((d) => d.delivery_note)
		.filter(Boolean);
	if (!dns.length) return;
	frappe.call({
		method: "erpnext_shipping.erpnext_shipping.shipping.get_content_description",
		args: { delivery_notes: JSON.stringify(dns) },
		callback: function (r) {
			if (r.message && !frm.doc.description_of_content) {
				frm.set_value("description_of_content", r.message);
			}
		},
	});
}

const LC_NS = "erpnext_shipping.erpnext_shipping.doctype.shipment_loss_claim.shipment_loss_claim";

// Kayıp parsel(ler)i seçtir; tek parsel varsa doğrudan claim aç.
function file_loss_claim(frm, default_type) {
	frappe.call({
		method: `${LC_NS}.get_claim_parcels`,
		args: { shipment: frm.doc.name },
		freeze: true,
		callback: function (r) {
			const rows = r.message || [];
			if (rows.length <= 1) {
				const tn = rows.length ? rows[0].tracking_number : null;
				create_loss_claims(frm, tn ? [tn] : [], default_type);
				return;
			}
			const esc = frappe.utils.escape_html;
			let html = `<div class="text-muted small" style="margin-bottom:8px;">${__(
				"Select the parcel(s) that are lost. A separate claim is opened for each."
			)}</div>`;
			html +=
				'<table class="table table-bordered" style="font-size:12px;"><thead><tr>' +
				`<th style="width:36px;text-align:center;"><input type="checkbox" class="lc-all"></th>` +
				`<th>${__("Tracking")}</th><th>${__("Carrier")}</th><th>${__("Status")}</th></tr></thead><tbody>`;
			rows.forEach((p) => {
				const has = p.existing_claim;
				html +=
					`<tr><td style="text-align:center;"><input type="checkbox" class="lc-pick" data-tn="${esc(
						p.tracking_number || ""
					)}" ${has ? "disabled" : ""}></td>` +
					`<td>${esc(p.tracking_number || "")}</td><td>${esc(p.carrier || "")}</td>` +
					`<td>${has ? __("Claim {0}", [esc(has)]) : esc(p.status || "")}</td></tr>`;
			});
			html += "</tbody></table>";

			const d = new frappe.ui.Dialog({
				title: __("File Loss Claim"),
				fields: [
					{
						fieldname: "claim_type",
						label: __("Claim Type"),
						fieldtype: "Select",
						options: "Not Delivered\nDelivered - Not Received\nDamaged",
						default: default_type,
						reqd: 1,
					},
					{ fieldname: "parcels", fieldtype: "HTML", options: html },
				],
				primary_action_label: __("Create Claim(s)"),
				primary_action(values) {
					const tns = [];
					d.$wrapper.find(".lc-pick:checked").each(function () {
						tns.push($(this).data("tn"));
					});
					if (!tns.length) {
						frappe.msgprint(__("Select at least one parcel."));
						return;
					}
					d.hide();
					create_loss_claims(frm, tns, values.claim_type);
				},
			});
			d.$wrapper.on("change", ".lc-all", function () {
				d.$wrapper.find(".lc-pick:not(:disabled)").prop("checked", this.checked);
			});
			d.show();
		},
	});
}

function create_loss_claims(frm, tns, claim_type) {
	frappe.call({
		method: `${LC_NS}.create_loss_claims`,
		args: { shipment: frm.doc.name, claim_type: claim_type, tracking_numbers: JSON.stringify(tns) },
		freeze: true,
		callback: function (r) {
			const names = r.message || [];
			if (!names.length) return;
			if (names.length === 1) {
				frappe.set_route("Form", "Shipment Loss Claim", names[0]);
			} else {
				frappe.msgprint({
					title: __("Claims created"),
					message: names
						.map(
							(n) =>
								`<a href="/app/shipment-loss-claim/${encodeURIComponent(n)}">${frappe.utils.escape_html(
									n
								)}</a>`
						)
						.join("<br>"),
					indicator: "green",
				});
			}
		},
	});
}

function render_parcel_breakdown(frm, rows) {
	const field = frm.get_field("custom_parcel_breakdown");
	if (!field) return;
	if (!rows.length) {
		field.$wrapper.html(`<div class="text-muted">${__("No parcels yet.")}</div>`);
		return;
	}
	const esc = frappe.utils.escape_html;
	let html = `<table class="table table-bordered" style="margin-top:8px;">
		<thead><tr>
			<th>${__("Tracking No")}</th>
			<th>${__("Carrier")}</th>
			<th style="text-align:right;">${__("Cost")}</th>
			<th>${__("Status")}</th>
			<th>${__("Delivered")}</th>
			<th style="text-align:center;">${__("Label Removed")}</th>
		</tr></thead><tbody>`;
	rows.forEach((p) => {
		const tracking = p.tracking_url
			? `<a href="${encodeURI(p.tracking_url)}" target="_blank">${esc(
					p.tracking_number || __("Track")
			  )}</a>`
			: esc(p.tracking_number || "");
		const cost =
			p.cost || p.cost === 0
				? format_currency(p.cost, p.currency || "EUR")
				: "—";
		const delivered = p.delivered_at ? esc(p.delivered_at) : "—";
		const removed = p.label_removed
			? `<span class="indicator-pill red">${__("Yes")}</span>`
			: "";
		html += `<tr>
			<td>${tracking}</td>
			<td>${esc(p.carrier || "")}</td>
			<td style="text-align:right;">${cost}</td>
			<td>${esc(p.status || "")}</td>
			<td>${delivered}</td>
			<td style="text-align:center;">${removed}</td>
		</tr>`;
	});
	html += `</tbody></table>`;
	field.$wrapper.html(html);
}

function select_from_available_services(frm, available_services) {
	const arranged_services = available_services.reduce(
		(prev, curr) => {
			if (curr.is_preferred) {
				prev.preferred_services.push(curr);
			} else {
				prev.other_services.push(curr);
			}
			return prev;
		},
		{ preferred_services: [], other_services: [] }
	);

	const dialog = new frappe.ui.Dialog({
		title: __("Select Service to Create Shipment"),
		size: "extra-large",
		fields: [
			{
				fieldtype: "HTML",
				fieldname: "available_services",
				label: __("Available Services"),
			},
		],
	});

	const delivery_notes = frm.doc.shipment_delivery_note.map((d) => d.delivery_note);

	dialog.fields_dict.available_services.$wrapper.html(
		frappe.render_template("shipment_service_selector", {
			header_columns: [
				__("Platform"),
				__("Carrier"),
				__("Parcel Service"),
				__("Contract"),
				__("Price"),
				"",
			],
			data: arranged_services,
		})
	);

	dialog.$body.on("click", ".btn", function () {
		let service_type = $(this).attr("data-type");
		let service_index = cint($(this).attr("id").split("-")[2]);
		let service_data = arranged_services[service_type][service_index];
		pick_contract_then(service_data, function (sd) {
			frm.select_row(sd);
		});
	});

	dialog.$body.on("click", ".fav-btn", function () {
		toggle_favourite($(this), arranged_services);
	});

	frm.select_row = function (service_data) {
		frappe.call({
			method: "erpnext_shipping.erpnext_shipping.shipping.create_shipment",
			freeze: true,
			freeze_message: __("Creating Shipment"),
			args: {
				shipment: frm.doc.name,
				pickup_from_type: frm.doc.pickup_from_type,
				delivery_to_type: frm.doc.delivery_to_type,
				pickup_address_name: frm.doc.pickup_address_name,
				delivery_address_name: frm.doc.delivery_address_name,
				shipment_parcel: frm.doc.shipment_parcel,
				description_of_content: frm.doc.description_of_content,
				pickup_date: frm.doc.pickup_date,
				pickup_contact_name:
					frm.doc.pickup_from_type === "Company"
						? frm.doc.pickup_contact_person
						: frm.doc.pickup_contact_name,
				delivery_contact_name: frm.doc.delivery_contact_name,
				value_of_goods: frm.doc.value_of_goods,
				service_data: service_data,
				delivery_notes: delivery_notes,
			},
			callback: function (r) {
				if (!r.exc) {
					frm.reload_doc();
					frappe.msgprint({
						message: __("Shipment {1} has been created with {0}.", [
							r.message.service_provider,
							r.message.shipment_id.bold(),
						]),
						title: __("Shipment Created"),
						indicator: "green",
					});
					frm.events.update_tracking(
						frm,
						r.message.service_provider,
						r.message.shipment_id
					);
				}
			},
		});
		dialog.hide();
	};
	dialog.show();
}

frappe.ui.form.on("Shipment Delivery Note", {
	delivery_note: function (frm) {
		// Bir Delivery Note seçilince description'ı (boşsa) ürün adlarından doldur.
		maybe_fill_description(frm);
	},
});

frappe.ui.form.on("Shipment Parcel", {
	custom_value_of_goods: function (frm) {
		// Bir koli değeri değişince Shipment toplam value_of_goods'u (boşsa) güncelle.
		recompute_value_of_goods(frm);
	},
});

function recompute_value_of_goods(frm) {
	// Shipment toplam Value of Goods boşsa koli değerlerinin toplamını yaz (elle gireni ezme).
	if (frm.doc.value_of_goods) return;
	const total = (frm.doc.shipment_parcel || []).reduce(
		(s, r) => s + (r.custom_value_of_goods || 0),
		0
	);
	if (total) frm.set_value("value_of_goods", total);
}

frappe.ui.form.on("Shipment Parcel", {
	custom_select_carrier: function (frm, cdt, cdn) {
		const row = locals[cdt][cdn];
		if (frm.is_new() || frm.is_dirty()) {
			frappe.msgprint(__("Please save the Shipment before selecting a carrier."));
			return;
		}
		if (!(row.length >= 1 && row.width >= 1 && row.height >= 1)) {
			frappe.msgprint(__("Please set parcel dimensions (length/width/height) first."));
			return;
		}
		frappe.call({
			method: "erpnext_shipping.erpnext_shipping.shipping.fetch_parcel_rates",
			freeze: true,
			freeze_message: __("Fetching Shipping Rates"),
			args: {
				shipment: frm.doc.name,
				parcel: JSON.stringify({
					length: row.length,
					width: row.width,
					height: row.height,
					weight: row.weight,
					count: 1,
				}),
			},
			callback: function (r) {
				if (r.message && r.message.length) {
					select_parcel_carrier(frm, cdt, cdn, r.message);
				} else {
					frappe.msgprint({
						message: __("No Shipment Services available"),
						title: __("Note"),
					});
				}
			},
		});
	},
});

// Yıldız butonu: favoriye alırken sözleşmeyi de sorar ve birlikte kaydeder.
// Çıkarırken sormaz. Sözleşmesiyle kaydedilen bir favori, teklif listesinden
// tek tıkla o sözleşmeyle gönderilir.
function toggle_favourite(btn, arranged_services) {
	const code = btn.attr("data-code");
	const was_preferred = btn.text().trim() === "★";
	const all = (arranged_services.preferred_services || []).concat(
		arranged_services.other_services || []
	);
	const sd = all.find((s) => String(s.service_id) === String(code)) || {};

	const save = function (contract_id, contract_label) {
		frappe.call({
			method: "erpnext_shipping.erpnext_shipping.doctype.sendcloud.sendcloud.toggle_preferred_shipping_option",
			args: {
				code: code,
				service_label: btn.attr("data-label"),
				carrier: btn.attr("data-carrier"),
				contract_id: contract_id || "",
				contract_label: contract_label || "",
			},
			callback: function (r) {
				if (r.exc) return;
				const pref = r.message && r.message.preferred;
				btn.text(pref ? "★" : "☆");
				btn.css("color", pref ? "#f0ad4e" : "#bbb");
				btn.attr("title", pref ? __("Remove from preferred") : __("Add to preferred"));
				frappe.show_alert({
					message: pref
						? contract_label
							? __("Added to preferred on {0}", [contract_label])
							: __("Added to preferred")
						: __("Removed from preferred"),
					indicator: "green",
				});
			},
		});
	};

	if (was_preferred) {
		save();
		return;
	}
	pick_contract_then(Object.assign({}, sd, { contract_pinned: 0 }), function (picked) {
		save(picked.contract_id, picked.contract_name);
	});
}

// SendCloud seçeneği seçildikten sonra, o carrier'ın birden fazla kontratı varsa
// kullanıcıya kontratı seçtirir (panel'deki "Enabled contract" gibi), sonra onDone(sd).
function pick_contract_then(sd, onDone) {
	if (sd.service_provider !== "SendCloud") {
		onDone(sd);
		return;
	}
	// Favoriye alınırken sözleşme kaydedilmişse sormaya gerek yok — favorinin
	// amacı zaten her seferinde aynı seçimi yapmaktan kurtulmak.
	if (sd.contract_pinned && sd.contract_id) {
		onDone(sd);
		return;
	}
	frappe.call({
		method: "erpnext_shipping.erpnext_shipping.shipping.get_sendcloud_contracts",
		args: { carrier: sd.carrier_code || "" },
		callback: function (r) {
			const contracts = r.message || [];
			if (contracts.length <= 1) {
				if (contracts.length === 1) {
					sd.contract_id = contracts[0].id;
					sd.contract_name = contracts[0].name;
				}
				onDone(sd);
				return;
			}
			const names = contracts.map((c) => c.name);
			const current = (
				contracts.find((c) => String(c.id) === String(sd.contract_id)) ||
				contracts.find((c) => c.is_default) ||
				contracts[0]
			).name;
			frappe.prompt(
				[
					{
						fieldtype: "Select",
						fieldname: "contract",
						label: __("Contract"),
						options: names.join("\n"),
						default: current,
						reqd: 1,
					},
				],
				function (values) {
					const chosen = contracts.find((c) => c.name === values.contract);
					if (chosen) {
						sd.contract_id = chosen.id;
						sd.contract_name = chosen.name;
					}
					onDone(sd);
				},
				__("Select Contract"),
				__("OK")
			);
		},
	});
}

function select_parcel_carrier(frm, cdt, cdn, available_services) {
	const arranged_services = available_services.reduce(
		(prev, curr) => {
			(curr.is_preferred ? prev.preferred_services : prev.other_services).push(curr);
			return prev;
		},
		{ preferred_services: [], other_services: [] }
	);

	const dialog = new frappe.ui.Dialog({
		title: __("Select Carrier for this Parcel"),
		size: "extra-large",
		fields: [{ fieldtype: "HTML", fieldname: "available_services" }],
	});

	dialog.fields_dict.available_services.$wrapper.html(
		frappe.render_template("shipment_service_selector", {
			header_columns: [
				__("Platform"),
				__("Carrier"),
				__("Parcel Service"),
				__("Contract"),
				__("Price"),
				"",
			],
			data: arranged_services,
		})
	);

	dialog.$body.on("click", ".btn", function () {
		const service_type = $(this).attr("data-type");
		const service_index = cint($(this).attr("id").split("-")[2]);
		const service_data = arranged_services[service_type][service_index];
		pick_contract_then(service_data, function (sd) {
			frappe.model.set_value(cdt, cdn, "custom_shipping_option_code", sd.service_id);
			frappe.model.set_value(cdt, cdn, "custom_shipping_carrier", sd.carrier);
			frappe.model.set_value(cdt, cdn, "custom_shipping_service", sd.service_name);
			frappe.model.set_value(cdt, cdn, "custom_shipping_price", sd.total_price || 0);
			frappe.model.set_value(cdt, cdn, "custom_shipping_contract", sd.contract_name || "");
			frappe.model.set_value(cdt, cdn, "custom_shipping_contract_type", sd.contract_type || "");
			frappe.model.set_value(cdt, cdn, "custom_shipping_contract_id", sd.contract_id || "");
			dialog.hide();
			frm.save().then(() => {
				frappe.show_alert({
					message: __("Carrier set: {0}", [sd.service_name]),
					indicator: "green",
				});
			});
		});
	});

	dialog.$body.on("click", ".fav-btn", function () {
		toggle_favourite($(this), arranged_services);
	});

	dialog.show();
}
