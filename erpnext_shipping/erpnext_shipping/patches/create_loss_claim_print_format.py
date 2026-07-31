# Copyright (c) 2026, Frappe Technologies and contributors
# For license information, please see license.txt
"""Create/update the DPD 'Confirmation of Receipt / Declaration of Non-Receipt'
print format for Shipment Loss Claim, pre-filled and language/region aware
(Germany -> DE/EN, DPD Netherlands Oirschot; Belgium/NL/LU -> NL/EN, DPD BELUX
reply@dpd.be). Runs on every migrate so template changes propagate."""
import frappe

PRINT_FORMAT_NAME = "DPD Declaration of Non-Receipt"

HTML = r"""
{%- set country = (doc.delivery_country or "")|lower -%}
{%- set is_de = country in ["germany", "deutschland", "de"] -%}
<div style="font-family: Arial, Helvetica, sans-serif; color:#111; font-size:11px;">
  <div style="text-align:right; color:#d40511; font-weight:bold; font-size:18px;">dpd</div>

  {% if is_de %}
  <h2 style="color:#d40511; margin:0 0 2px;">Empfangsbestätigung / Erklärung des Nicht-Erhalts</h2>
  <div style="font-style:italic; color:#555;">(Confirmation of Receipt / Declaration of Non-Receipt)</div>
  <p><b>Bitte senden Sie das ausgefüllte und unterschriebene Formular an:</b><br>
     <span style="font-style:italic; color:#555;">(Please return the completed and signed form to:)</span><br>
     DPD (Netherlands) B.V. — Department Customer Service<br>
     Westfields 1410, NL-5688 HA Oirschot</p>
  {% else %}
  <h2 style="color:#d40511; margin:0 0 2px;">Bevestiging van ontvangst / Verklaring van niet-ontvangst</h2>
  <div style="font-style:italic; color:#555;">(Confirmation of Receipt / Declaration of Non-Receipt)</div>
  <p><b>Gelieve het ingevulde en ondertekende document terug te bezorgen aan:</b><br>
     <span style="font-style:italic; color:#555;">(Please return the completed and signed form to:)</span><br>
     DPD BELUX — reply@dpd.be<br>
     Indringingsweg 2, 1800 Vilvoorde | dpd.be</p>
  {% endif %}

  <table style="width:100%; border-collapse:collapse; margin-top:8px;" border="1" cellpadding="6">
    <tr>
      <td style="width:45%;"><b>{{ "Versanddatum" if is_de else "Verzenddatum" }}</b><br>
        <span style="font-style:italic; color:#555;">(date of dispatch)</span></td>
      <td>{{ frappe.utils.formatdate(doc.pickup_date) if doc.pickup_date else "" }}</td>
    </tr>
    <tr>
      <td><b>{{ "Paketnummer(n) 14-stellig" if is_de else "Pakketnummer(s) 14 cijfers" }}</b><br>
        <span style="font-style:italic; color:#555;">(Parcel number(s) 14-digits)</span></td>
      <td>{{ doc.tracking_numbers or "" }}</td>
    </tr>
    <tr>
      <td><b>{{ "Versendername" if is_de else "Verzender" }}</b>
        <span style="font-style:italic; color:#555;">(Name Sender)</span></td>
      <td>{{ doc.sender_name or "" }}</td>
    </tr>
    <tr>
      <td><b>{{ "Empfängername + Adresse" if is_de else "Naam ontvanger en adres" }}</b><br>
        <span style="font-style:italic; color:#555;">(Receiver's name + address)</span></td>
      <td>{{ doc.receiver_name or "" }}{% if doc.receiver_address %}<br>{{ doc.receiver_address }}{% endif %}</td>
    </tr>
    <tr>
      <td><b>{{ "Empfänger E-Mail-Adresse" if is_de else "E-mail ontvanger" }}</b>
        <span style="font-style:italic; color:#555;">(Receiver's email)</span></td>
      <td>{{ doc.receiver_email or "" }}</td>
    </tr>
    <tr>
      <td><b>{{ "Empfänger Telefonnummer" if is_de else "Telefoonnummer ontvanger" }}</b>
        <span style="font-style:italic; color:#555;">(Receiver's phone number)</span></td>
      <td>{{ doc.receiver_phone or "" }}</td>
    </tr>
  </table>

  <p style="margin-top:10px;"><b>{{ "Bitte auswählen:" if is_de else "Selecteer a.u.b.:" }}</b>
     <span style="font-style:italic; color:#555;">(Please select)</span></p>

  <p>☐ <b style="color:#d40511;">{{ "Rechtsverbindliche Erklärung des Empfangs" if is_de else "Wettelijk bindende verklaring van ontvangst" }}</b>
     <span style="font-style:italic; color:#555;">(Legally Binding Confirmation of Receipt)</span><br>
     {% if is_de %}Ich/wir bestätige(n) hiermit den Erhalt des oben genannten Pakets am (Datum): _______________
     {% else %}Ik/Wij bevestig(en) dat ik/wij, of een bij mij/ons bekende persoon, het bovenstaande pakket heb(ben) ontvangen. (datum): _______________{% endif %}
  </p>

  <p>☒ <b style="color:#d40511;">{{ "Rechtsverbindliche Erklärung des Nicht-Erhalts" if is_de else "Wettelijk bindende verklaring van niet-ontvangst" }}</b>
     <span style="font-style:italic; color:#555;">(Legally Binding Declaration of Non-Receipt)</span><br>
     {% if is_de %}Ich/wir erkläre(n) hiermit, dass das oben genannte Paket nicht in meinen/unseren Besitz und auch nicht in den Besitz einer mir/uns bekannten Person gelangt ist.
     {% else %}Ik/Wij bevestig(en) dat ik/wij, of een bij mij/ons bekende persoon, het bovenstaande pakket niet heb(ben) ontvangen.{% endif %}
  </p>

  <p style="font-style:italic; color:#555; font-size:10px;">
     {% if is_de %}Bitte beachten Sie, dass Sie sich strafbar machen, wenn Sie in dieser rechtsverbindlichen Erklärung eine falsche Angabe machen.
     {% else %}Houd er rekening mee dat als u een valse verklaring aflegt in deze wettelijk bindende verklaring, u mogelijk een misdrijf pleegt dat wettelijk strafbaar is.{% endif %}
     (Please note that a false statement in this Legally Binding Declaration may be a crime punishable by law.)</p>

  <table style="width:100%; margin-top:28px; border-collapse:collapse;">
    <tr>
      <td style="width:50%; vertical-align:bottom; padding-right:24px;">
        <div style="font-weight:bold; min-height:16px;">{{ frappe.utils.formatdate(frappe.utils.nowdate()) }}</div>
        <div style="border-top:1px solid #333; padding-top:2px;">
          {{ "Datum der Unterzeichnung" if is_de else "Datum van ondertekening" }}
          <span style="font-style:italic; color:#555;">(Date of signing)</span></div>
      </td>
      <td style="width:50%; vertical-align:bottom;">
        <div style="font-weight:bold; min-height:16px;">{{ doc.receiver_name or "" }}</div>
        <div style="border-top:1px solid #333; padding-top:2px;">
          {{ "Name und Funktion des Unterzeichners" if is_de else "Naam en functie ondertekenaar" }}
          <span style="font-style:italic; color:#555;">(Name and Position of Signatory)</span></div>
      </td>
    </tr>
    <tr>
      <td style="vertical-align:bottom; padding-top:34px; padding-right:24px;">
        <div style="min-height:16px;"></div>
        <div style="border-top:1px solid #333; padding-top:2px;">
          {{ "Name der Firma" if is_de else "Bedrijfsnaam" }}
          <span style="font-style:italic; color:#555;">(Name of Company)</span></div>
      </td>
      <td style="vertical-align:bottom; padding-top:34px;">
        <div style="min-height:16px;"></div>
        <div style="border-top:1px solid #333; padding-top:2px;">
          {{ "Unterschrift" if is_de else "Handtekening" }}
          <span style="font-style:italic; color:#555;">(Signature)</span></div>
      </td>
    </tr>
  </table>

  <div style="margin-top:14px; font-size:9px; color:#888;">Ref: {{ doc.name }}{% if doc.carrier_claim_ref %} · {{ doc.carrier_claim_ref }}{% endif %}</div>
</div>
"""


def execute():
	doc = frappe.get_doc("Print Format", PRINT_FORMAT_NAME) if frappe.db.exists(
		"Print Format", PRINT_FORMAT_NAME
	) else frappe.new_doc("Print Format")
	doc.update(
		{
			"name": PRINT_FORMAT_NAME,
			"doc_type": "Shipment Loss Claim",
			"module": "ERPNext Shipping",
			"standard": "Yes",
			"print_format_type": "Jinja",
			"html": HTML,
			"disabled": 0,
		}
	)
	doc.flags.ignore_permissions = True
	doc.save()
