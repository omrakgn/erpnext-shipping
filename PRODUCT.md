# Product

<!-- impeccable:product-schema 1 -->

## Platform

web (Frappe Desk, ERPNext v15). Screens are Desk forms, lists and reports; desktop, tablet and phone.

## Users

- **Warehouse** (confirmed 2026-10-16): creates the shipment from a delivery note, enters the boxes, prints the carrier label or the Delivery Pickup Note, hands the goods over. Works on desktop, and on tablet or phone in the warehouse.
- **Operations** (confirmed 2026-10-16): answers "where is my order": in transit, delivered, came back. Sends the delay e-mail to the carrier, opens loss claims, records an own-transport delivery from the signed copy.
- **Accounting** (secondary): reads shipping cost, surcharges, quote variance and margin.

## Product Purpose

The shipping part of one company's ERPNext: every parcel that leaves or comes back, with whichever way it travels (carrier label through SendCloud, LetMeShip or DPD; FedEx by hand; own vehicle or a hired transport company), what it cost, and whether it arrived. Delivery and return facts feed other apps: the customer's return period, Shopify's "Delivered" status, follow-up shipments.

Success: a person who opens a shipment knows in seconds where it stands and what to do next, without reading technical fields.

## Positioning

One record per shipment joins the order, the carrier's label and tracking feed, the carrier's invoice and the return, so delay, loss, cost and return questions are answered on the same page.

## Terminology

- Shipment: the record of one dispatch, one or more parcels.
- Delivery Note: the goods that left the warehouse; a shipment points at one or more.
- Carrier: who moves the parcel (DPD, FedEx). Billed by: who invoices it (can be SendCloud).
- Own transport: own vehicle or a hired transport company; no label, no tracking feed.

## Constraints

- Screen text is English; NL and DE translations live in the app's `translations/*.csv`.
- Screen text names no company, sector or internal document; the system serves several businesses.
- No em dash as a separator in any text.
- Native Frappe Desk components and indicator colours; no new fonts or colour palette.
- Other apps read and act on shipments (`multichannel_core`, `ecommerce_integrations`); their buttons and links on the form keep working.
