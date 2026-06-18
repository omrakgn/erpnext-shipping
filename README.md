## ERPNext Shipping

A Shipping Integration for ERPNext with various platforms. Platforms integrated in this app are:

- [LetMeShip](https://www.letmeship.com/en/)
- [SendCloud](https://www.sendcloud.com/home-new/)

> [!TIP]
> Please make sure to get your API access enabled first, by contacting the LetMeShip support.

## Features
- Creation of shipment to a carrier service (e.g. FedEx, UPS) via LetMeShip and SendCloud. 
- Compare shipping rates. 
- Printing the shipping label is also made available within the Shipment doctype.
- Templates for the parcel dimensions.
- Shipment tracking.

## Installation

Install [on Frappe Cloud](https://frappecloud.com/marketplace/apps/shipping) or your own server:

```bash
cd ~/frappe-bench
bench get-app https://github.com/frappe/erpnext-shipping.git --branch version-14
bench --site $MY_SITE install-app erpnext_shipping
```

## Setup

Some shipping providers require the contact details of the pickup contact. Please make sure that the **User** selected as the _Pickup Contact Person_ has a first name, last name, email address, and phone number before submitting the **Shipment**.

For the 'compare shipping rates' feature to work as expected, you need to generate an API key from your service provider. Service providers have their own specific doctypes similar to those from the `Integrations`. They can be enabled or disabled depending on your needs.

![LetMeShip 2020-08-05 09-54-28](https://user-images.githubusercontent.com/17470909/89377411-500c4f80-d724-11ea-8fe5-b11fec2a5c27.png)

### Fetch Shipping Rates
![core2](https://user-images.githubusercontent.com/17470909/89377460-70d4a500-d724-11ea-8550-a2813b936651.gif)

You can see the list of shipping rates by clicking the `Fetch Shipping Rates` button. Once you picked a rate, it will create the shipment for you. 

### Shipping Label
![71bcfc9d-9d66-4a58-8238-1eeab4e9a24f 2020-08-05 09-48-32](https://user-images.githubusercontent.com/17470909/89377478-78944980-d724-11ea-8120-a5374c6e4c5e.png)

The service provider will also provide the shipping label and to generate the label, click on the `Print Shipping Label` on top of the doctype.

-----------------------


# SendCloud ERPNext Entegrasyonu

Bu doküman, ERPNext için özelleştirilmiş SendCloud entegrasyonunun özelliklerini ve yapılandırmasını açıklar.

## 📦 Özellikler

### 1. Parcel Items Desteği
Shipment oluşturulduğunda, bağlı Delivery Note'lardan ürün bilgileri otomatik olarak SendCloud'a gönderilir:

- **Description**: Ürün adı
- **SKU**: `item_code` (ürün kodu doğrudan SKU olarak kullanılır)
- **Quantity**: Ürün adedi (aynı SKU'lar birleştirilir)
- **Price**: Ürün tutarı (EUR)
- **Weight**: Ürün ağırlığı (kg)
- **HS Code**: Gümrük tarife numarası (uluslararası gönderiler için)
- **Origin Country**: Menşei ülke kodu

### 2. Akıllı Order Number
SendCloud'a gönderilen `order_number` alanı için öncelik sırası:

1. **Customer's Purchase Order** (`po_no`): Sales Order'daki müşteri PO numarası
2. **Sales Order Name**: PO yoksa Sales Order adı
3. **Shipment Name**: Hiçbiri yoksa Shipment adı (fallback)

### 3. Label Notes (Etiket Notları)
Kargo etiketi üzerinde SKU ve adet bilgisi görüntülenir:

```
SC-MAT-090-200-18[2]
SC-VAC-DOUBLE[1]
```

- Her SKU ayrı satırda
- Format: `SKU[adet]`
- Maksimum 50 karakter limiti
- Aynı SKU'lar birleştirilir

### 4. Müşteri Tipi Kontrolü
Bireysel ve kurumsal müşteriler farklı işlenir:

| Müşteri Tipi | company_name Alanı |
|--------------|-------------------|
| **Company** | Şirket adı gösterilir |
| **Individual** | Boş bırakılır |

### 5. Adres House Number Extraction
Hem gönderici hem alıcı adresleri için ev numarası otomatik ayrıştırılır:

```
"Reichelstr. 42/a" → address_line_1: "Reichelstr.", house_number: "42/a"
```

### 6. Duplicate SKU Birleştirme
Aynı siparişte birden fazla aynı ürün varsa:

- Parcel items'da tek satırda birleştirilir
- Quantity, price ve weight toplanır
- Label notes'da tek SKU ile toplam adet gösterilir

### 7. Koli-Bazlı Ürün Eşleştirme (Parcel Items)
Bir gönderi birden fazla koliye bölündüğünde, hangi ürünün hangi koliye / kaç adet
konduğu **Shipment > Parcel Items** tablosundan belirlenir:

| Alan | Açıklama |
|------|----------|
| `Parcel No` | Shipment Parcel tablosundaki koli sıra numarası (1, 2, 3 ...) |
| `Item` | Ürün (Item) |
| `Qty` | O koliye konan adet |

- Tablo **doluysa**: SendCloud'a her koli için **sadece o kolideki ürün/adetler** gönderilir.
  Birim fiyat ve ağırlık, bağlı Delivery Note'tan türetilip atanan adetle çarpılır.
- Tablo **boşsa**: eski davranış korunur (tüm DN ürünleri her koliye gönderilir) —
  tek koli senaryosu için doğru sonuç.
- Atanan toplam adetler bağlı Delivery Note adetleriyle uyuşmazsa kayıt sırasında
  **uyarı** gösterilir (engellenmez).

### 8. Şablondan Otomatik Koli Doldurma
Her ürün, Item kartındaki **Shipment Parcel Template** alanı ile bir koli şablonuna
bağlanabilir. Shipment formunda (taslak halindeyken) **"Populate Parcels from Delivery
Notes"** butonu:

- Bağlı Delivery Note'lardaki her ürün için, **her birim ayrı kutu** olacak şekilde
  şablon ölçüleriyle ayrı **Shipment Parcel** satırları oluşturur (`count = 1`). Örn.
  2 adet yatak → 2 ayrı koli satırı.
- Eşleşen **Parcel Items** satırlarını otomatik doldurur (kutu başına 1 adet).
- Sonuç tamamen **düzenlenebilir** — belirli bir kutuya elle başka ürün (örn. **hediye**)
  eklemek serbesttir. DN'de olmayan ekstra ürünler **uyarı vermez**; doğrulama yalnızca
  DN'deki ürünlerin adetlerini kontrol eder.
- Şablonu olmayan ürünler atlanır ve listelenir (ölçüleri elle girilmeli).
- Tablolar doluysa buton **üzerine yazmadan önce onay** ister.

> ⚠️ Buton, Shipment **kaydedildikten sonra** çalışır (kaydedilmemiş değişiklik varsa uyarır).

### 9. Kargo Seçeneği Etiketi, Favoriler ve Filtre
Fetch Shipping Rates penceresinde SendCloud seçenekleri için:

- **Ayırt edici etiket**: Servis adının altında **shipping option kodu** gösterilir
  (örn. `fedex:regional/economy,signature`). Aynı isimli FedEx varyantları artık ayırt edilir.
- **Fiyatsız seçenekler**: Kendi sözleşmenizle kullandığınız taşıyıcılar (FedEx vb.) fiyat
  döndürmese bile listelenir ("Price on request") ve seçilip gönderilebilir.
- **Favoriler (★)**: Her SendCloud satırındaki yıldıza tıklayarak o seçeneği favorilere
  ekler/çıkarırsınız. Favoriler **SendCloud Settings > Preferred Options** tablosunda saklanır
  ve sonraki sorgularda **Preferred Services** bölümünde üstte görünür.
- **Sadece favorileri göster**: SendCloud Settings'te **"Only show preferred options when
  fetching rates"** işaretlenirse, sorguda yalnızca yıldızladığınız seçenekler listelenir.

### 10. Koli Başına Farklı Kargo Seçimi
Her koli için **ayrı bir kargo** seçilebilir (örn. bir kutu FedEx, diğeri DPD):

- Shipment Parcel tablosunda her satırda **"Select Carrier"** butonu vardır. Tıklayınca
  o kolinin **kendi ağırlık/ölçüsüyle** fiyat sorgulanır ve bir kargo seçersin; seçim
  satıra yazılır (kod, taşıyıcı, servis, fiyat).
- Tüm koliler için seçim yapıldıktan sonra, üstteki **"Create Shipment (Per-Parcel
  Carriers)"** butonu her koliyi **kendi kargosuyla ayrı ayrı** SendCloud'a gönderir.
- SendCloud'da `ship_with` gönderi seviyesinde olduğundan, her koli ayrı bir API
  çağrısıyla (announce) oluşturulur. Sonuçlar (shipment_id, takip no, URL) birleştirilir.
- Bu özellik **SendCloud** içindir. Tek kargoyla göndermek istersen normal **"Fetch
  Shipping Rates"** akışı aynen çalışır.

## ⚙️ Yapılandırma

### SendCloud Settings
ERPNext'te `SendCloud` DocType'ında şu alanlar yapılandırılmalı:

| Alan | Açıklama |
|------|----------|
| `api_key` | SendCloud Public Key |
| `api_secret` | SendCloud Secret Key |
| `enabled` | Entegrasyonu aktif et |
| `brand_id` | (Opsiyonel) SendCloud Brand ID |

### Item Master Alanları
Ürünlerde şu alanlar kullanılır:

| Alan | Açıklama |
|------|----------|
| `item_code` | Ürün kodu (SKU olarak kullanılır) |
| `custom_shipment_parcel_template` | Koli şablonu (otomatik doldurma için) |
| `customs_tariff_number` | HS Code (gümrük) |
| `country_of_origin` | Menşei ülke |
| `weight_per_unit` | Birim ağırlığı |

### Customer Ayarları
Müşteri tipinin doğru belirlenmesi için:

- `Customer.customer_type`: "Company" veya "Individual"

### Address Ayarları
Gönderici adresi için:

- `address_title`: Şirket adı olmalı (adres değil)

## 🔄 API Versiyonu

Bu entegrasyon **SendCloud API v3** kullanır:

- Endpoint: `https://panel.sendcloud.sc/api/v3/shipments`
- Multicollo desteği
- Label notes desteği
- Brand ID desteği

## 📋 Payload Örneği

```json
{
  "order_number": "305-8095352-1473946",
  "brand_id": 12345,
  "parcels": [
    {
      "dimensions": {
        "length": 100,
        "width": 33,
        "height": 33,
        "unit": "cm"
      },
      "weight": {
        "value": 17.0,
        "unit": "kg"
      },
      "order_number": "SHIPMENT-00047-1",
      "parcel_items": [
        {
          "description": "Mattress 180x200x18",
          "quantity": 2,
          "price": {
            "value": 1058.0,
            "currency": "EUR"
          },
          "weight": {
            "value": 24.0,
            "unit": "kg"
          },
          "sku": "SC-MAT-180-200-18",
          "hs_code": "940421",
          "origin_country": "BE"
        }
      ],
      "label_notes": ["SC-MAT-180-200-18[2]"]
    }
  ],
  "to_address": {
    "name": "John Doe",
    "address_line_1": "Hauptstraße",
    "house_number": "123",
    "postal_code": "10115",
    "city": "Berlin",
    "country_code": "DE",
    "phone_number": "+49123456789",
    "email": "john@example.com"
  },
  "from_address": {
    "name": "Scarnatti",
    "company_name": "Scarnatti B.V.",
    "address_line_1": "Veldstraat",
    "house_number": "2",
    "postal_code": "2930",
    "city": "Brasschaat",
    "country_code": "BE",
    "phone_number": "+32495813358",
    "email": "info@scarnatti.com"
  },
  "ship_with": {
    "type": "shipping_option_code",
    "properties": {
      "shipping_option_code": "gls_eu:eurobusinessparcel,be/flexdelivery"
    }
  }
}
```

## 🐛 Debug Logging

Geliştirme sırasında debug logları Error Log'a yazılır:

- **SendCloud Debug Payload**: API'ye gönderilen payload
- **SendCloud API Response**: API'den dönen yanıt

Production'da bu logları kapatmak için `sendcloud.py`'deki `frappe.log_error` satırlarını yorum satırı yapın.

## 📁 Dosya Konumu

```
erpnext_shipping/
└── erpnext_shipping/
    └── doctype/
        └── sendcloud/
            ├── sendcloud.py      # Ana entegrasyon kodu
            ├── sendcloud.json    # DocType tanımı
            └── sendcloud.js      # Frontend kodu
```

## 🔧 Kurulum

### Fork'tan Kurulum

```bash
cd ~/frappe-bench
bench get-app https://github.com/omrakgn/erpnext-shipping.git --branch develop
bench --site [SITE_ADI] install-app erpnext_shipping
bench --site [SITE_ADI] migrate
bench build
bench restart
```

### Güncelleme

```bash
cd ~/frappe-bench/apps/erpnext_shipping
git pull origin develop
cd ~/frappe-bench
bench --site [SITE_ADI] migrate
bench restart
```

## 📝 Değişiklik Geçmişi

### v1.1.0 (Haziran 2026)
- ✅ Koli-bazlı ürün eşleştirme (Parcel Items tablosu) — çoklu kolide doğru ürün/adet
- ✅ Item ↔ Shipment Parcel Template bağı + DN'den otomatik koli doldurma butonu
- ✅ Koli adetleri ile Delivery Note adetleri uyuşmazlığında uyarı
- ✅ Fiyatsız (kendi sözleşmeli) kargo seçenekleri artık gösteriliyor (FedEx vb.)
- ✅ Seçenek etiketinde kod gösterimi + favori (★) kaydetme + "sadece favoriler" filtresi
- ✅ Koli başına farklı kargo seçimi (her kutu ayrı taşıyıcı/servis ile gönderilir)
- ✅ `brand_id` artık SendCloud Settings'te gerçek bir alan
- ✅ SKU kaynağı `item_code` (custom_sku kaldırıldı)
- 🧹 Ölü kod (`format_parcel_item`) ve sürekli debug logları temizlendi

### v1.0.0 (Ocak 2026)
- ✅ Parcel items desteği eklendi
- ✅ API v3 formatına güncellendi (`price` alanı)
- ✅ `order_number` root seviyeye eklendi
- ✅ Customer's Purchase Order desteği
- ✅ Label notes (SKU[adet]) desteği
- ✅ Bireysel/Kurumsal müşteri ayrımı
- ✅ House number extraction (to_address)
- ✅ Duplicate SKU birleştirme
- ✅ Brand ID desteği
- ✅ Debug logging

## 🤝 Katkıda Bulunma

Fork: [https://github.com/omrakgn/erpnext-shipping](https://github.com/omrakgn/erpnext-shipping)

## 📄 Lisans

MIT License - Orijinal frappe/erpnext-shipping lisansına tabidir.