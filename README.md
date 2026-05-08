# menuj-discovery

Apify Actor pro jednorázové vyhledávání restaurací v Ostravě přes Google Places API.

## Co dělá

1. Spustí sérii vyhledávacích dotazů na Google Places API
2. Deduplikuje výsledky podle `place_id`
3. Načte detaily (web, telefon) pro každou restauraci
4. Zapíše do Supabase jako **neaktivní** (`is_active = false`)

Restaurace pak ručně projdeš, doplníš `scrape_url` a nastavíš `is_active = true`.

## Setup

### 1. Google Maps API klíč

1. Jdi na [console.cloud.google.com](https://console.cloud.google.com)
2. Vytvoř projekt nebo vyber existující
3. APIs & Services → Enable APIs → povol **Places API**
4. APIs & Services → Credentials → Create Credentials → API Key
5. Doporučeno: omez klíč na Places API

### 2. Supabase migrace

Spusť `03_migration.sql` v Supabase SQL Editoru.

### 3. Environment Variables

Zkopíruj `.env.example` → `.env` a doplň klíče.

## Spuštění

### Lokálně
```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python src/main.py
```

### Dry run (bez zápisu do DB)
Nastav v input:
```json
{ "dry_run": true }
```

### Vlastní dotazy
```json
{
  "queries": [
    "restaurace oběd Ostrava Poruba",
    "pizza Ostrava centrum"
  ]
}
```

## Environment Variables

| Proměnná | Popis |
|---|---|
| `GOOGLE_MAPS_API_KEY` | Google Places API klíč |
| `SUPABASE_URL` | URL Supabase projektu |
| `SUPABASE_SERVICE_KEY` | Service role key |
| `APIFY_TOKEN` | Apify token |

## Odhadovaná cena Google API

Pro 10 výchozích dotazů (~200 výsledků):
- Text Search: ~$0.64
- Place Details: ~$3.40
- **Celkem: ~$4** (pokryto free kreditem $200/měsíc)
