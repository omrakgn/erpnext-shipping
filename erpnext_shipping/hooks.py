from . import __version__ as app_version

app_name = "erpnext_shipping"
app_title = "ERPNext Shipping"
app_publisher = "Frappe"
app_description = "A Shipping Integration fir ERPNext"
app_icon = "octicon octicon-file-directory"
app_color = "grey"
app_email = "developers@frappe.io"
app_license = "MIT"

# Includes in <head>
# ------------------

# include js, css files in header of desk.html
# app_include_css = "/assets/erpnext_shipping/css/erpnext_shipping.css"
app_include_js = "shipping.bundle.js"

# include js, css files in header of web template
# web_include_css = "/assets/erpnext_shipping/css/erpnext_shipping.css"
# web_include_js = "/assets/erpnext_shipping/js/erpnext_shipping.js"

# include custom scss in every website theme (without file extension ".scss")
# website_theme_scss = "erpnext_shipping/public/scss/website"

# include js, css files in header of web form
# webform_include_js = {"doctype": "public/js/doctype.js"}
# webform_include_css = {"doctype": "public/css/doctype.css"}

# include js in page
# page_js = {"page" : "public/js/file.js"}

# include js in doctype views
doctype_js = {"Shipment": "public/js/shipment.js", "Delivery Note": "public/js/delivery_note.js"}

# Jinja (print format) yardımcı metotları
jinja = {
	"methods": [
		"erpnext_shipping.erpnext_shipping.doctype.pickup_manifest.pickup_manifest.get_company_logo_src",
		"erpnext_shipping.erpnext_shipping.doctype.pickup_manifest.pickup_manifest.get_manifest_packages",
	]
}
# doctype_list_js = {"doctype" : "public/js/doctype_list.js"}
# doctype_tree_js = {"doctype" : "public/js/doctype_tree.js"}
# doctype_calendar_js = {"doctype" : "public/js/doctype_calendar.js"}

# Home Pages
# ----------

# application home page (will override Website Settings)
# home_page = "login"

# website user home page (by Role)
# role_home_page = {
# "Role": "home_page"
# }

# Generators
# ----------

# automatically create page for each record of this doctype
# website_generators = ["Web Page"]

# Installation
# ------------

# before_install = "erpnext_shipping.install.before_install"
after_install = "erpnext_shipping.install.after_install"

# Her migrate'te Pickup Manifest print format'ını güncel tut (patch'ler bir kez
# çalıştığından, format değişiklikleri ancak böyle yayılır).
after_migrate = [
	# Özel alanlar ve property setter'lar. Yalnız after_install'da çalıştıkları
	# sürece, kurulumdan sonra eklenen bir alan mevcut sitede hiç oluşmuyordu;
	# alana bakan kod "Field ... not found" ile düşüyor ve sebebi görünmüyor,
	# çünkü migrate sırasında hiçbir hata çıkmıyor.
	"erpnext_shipping.install.sync_customisations",
	"erpnext_shipping.erpnext_shipping.patches.create_pickup_manifest_print_format.execute",
	"erpnext_shipping.erpnext_shipping.patches.create_pickup_item_summary_print_format.execute",
	"erpnext_shipping.erpnext_shipping.patches.create_loss_claim_print_format.execute",
	"erpnext_shipping.erpnext_shipping.patches.create_delay_email_template.execute",
	"erpnext_shipping.erpnext_shipping.patches.create_shipping_dashboard.execute",
	"erpnext_shipping.erpnext_shipping.patches.create_shipping_workspace.execute",
]

# Desk Notifications
# ------------------
# See frappe.core.notifications.get_notification_config

# notification_config = "erpnext_shipping.notifications.get_notification_config"

# Permissions
# -----------
# Permissions evaluated in scripted ways

# permission_query_conditions = {
# 	"Event": "frappe.desk.doctype.event.event.get_permission_query_conditions",
# }
#
# has_permission = {
# 	"Event": "frappe.desk.doctype.event.event.has_permission",
# }

# DocType Class
# ---------------
# Override standard doctype classes

# override_doctype_class = {
# 	"ToDo": "custom_app.overrides.CustomToDo"
# }

# Document Events
# ---------------
# Hook on document methods and events

# doc_events = {
# 	"*": {
# 		"on_update": "method",
# 		"on_cancel": "method",
# 		"on_trash": "method"
# }
# }

# Scheduled Tasks
# ---------------

scheduler_events = {
	"hourly": ["erpnext_shipping.erpnext_shipping.utils.update_tracking_info"],
	"daily": [
		# Geciken gönderileri işaretle ve (açıksa) günlük digest e-postasını yolla.
		"erpnext_shipping.erpnext_shipping.delay.flag_and_notify_delayed",
		# Eşiği aşan teslim edilmemiş gönderileri "Presumed Lost" işaretle.
		"erpnext_shipping.erpnext_shipping.loss.flag_presumed_lost",
		# Süresi yaklaşan (ve henüz gönderilmemiş) tazminat taleplerini hatırlat.
		"erpnext_shipping.erpnext_shipping.doctype.shipment_loss_claim.shipment_loss_claim.remind_claim_deadlines",
		# Teslim sürelerinden carrier x hedef SLA gün ortalamalarını öğren.
		"erpnext_shipping.erpnext_shipping.sla.rebuild_carrier_sla_lanes",
		# SLA Date/Status güncelle; risk/aşım durumunda iç ekibe bildir.
		"erpnext_shipping.erpnext_shipping.sla.flag_and_notify_sla",
	],
}

# Testing
# -------

# before_tests = "erpnext_shipping.install.before_tests"

# Overriding Methods
# ------------------------------
#
# override_whitelisted_methods = {
# 	"frappe.desk.doctype.event.event.get_events": "erpnext_shipping.event.get_events"
# }
#
# each overriding function accepts a `data` argument;
# generated from the base implementation of the doctype dashboard,
# along with any modifications made in other Frappe apps

# Connections sekmesine kargo kayıtlarını ekle. Fonksiyonlar zincirleme çalışır
# ve gelen data'nın üstüne ekler — ERPNext'in ve başka app'lerin girdileri korunur.
override_doctype_dashboards = {
	"Shipment": "erpnext_shipping.erpnext_shipping.dashboard_overrides.get_shipment_dashboard_data",
	"Delivery Note": "erpnext_shipping.erpnext_shipping.dashboard_overrides.get_delivery_note_dashboard_data",
}

# exempt linked doctypes from being automatically cancelled
#
# auto_cancel_exempted_doctypes = ["Auto Repeat"]

doc_events = {
	"Shipment": {
		"validate": [
			# Parçalar boşsa önce doldur ki alttaki parça doğrulamaları çalışabilsin.
			"erpnext_shipping.erpnext_shipping.shipping.auto_populate_parcels",
			"erpnext_shipping.erpnext_shipping.utils.validate_parcels",
			"erpnext_shipping.erpnext_shipping.utils.validate_phone",
			"erpnext_shipping.erpnext_shipping.utils.validate_parcel_items",
			"erpnext_shipping.erpnext_shipping.shipping.set_shipment_description",
			"erpnext_shipping.erpnext_shipping.shipping.set_parcel_values",
			# SLA Date + Status'ı güncel tut (taahhüt tarihi ya da pickup + carrier SLA).
			"erpnext_shipping.erpnext_shipping.sla.set_sla_fields",
			# "Returned to Us" işaretlendiği anı damgala.
			"erpnext_shipping.erpnext_shipping.loss.stamp_returned_on",
		],
		# Bayrak `allow_on_submit`: kutu çoğu zaman onaylanmış belgede
		# işaretleniyor ve o yolda `validate` hiç çalışmıyor.
		"on_update_after_submit": "erpnext_shipping.erpnext_shipping.loss.stamp_returned_on",
		# İptalde de `validate` çalışmıyor. Bu olmadan iptal edilen gönderi son
		# SLA damgasıyla kalıyor ve günlük iş iptalleri atladığı için o damgayı
		# bir daha kimse düzeltmiyor.
		"on_cancel": "erpnext_shipping.erpnext_shipping.sla.clear_sla_on_cancel",
	},
	"Delivery Note": {
		# Yalnızca Shipment Settings'te kargo geliri hesabı tanımlıysa çalışır (aksi
		# halde no-op) — müşteri kargo bedelini vergi satırından doldurur.
		"validate": "erpnext_shipping.erpnext_shipping.shipping_cost.set_dn_customer_shipping_charge",
	},
}
