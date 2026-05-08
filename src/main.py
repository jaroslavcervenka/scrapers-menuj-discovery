from dotenv import load_dotenv
load_dotenv()

import os
import asyncio
import httpx
import re
from apify import Actor
from supabase import create_client
from unidecode import unidecode


# ── Konfigurace ───────────────────────────────────────────────────────────────

GOOGLE_API_KEY = os.environ["GOOGLE_MAPS_API_KEY"]
PLACES_TEXT_SEARCH_URL = "https://maps.googleapis.com/maps/api/place/textsearch/json"
PLACES_DETAILS_URL = "https://maps.googleapis.com/maps/api/place/details/json"

DEFAULT_QUERIES = [
    "restaurace denní menu Ostrava centrum",
    "restaurace denní menu Moravská Ostrava",
    "restaurace denní menu Ostrava Poruba",
    "restaurace denní menu Ostrava-Jih",
    "restaurace denní menu Mariánské Hory Ostrava",
    "restaurace denní menu Slezská Ostrava",
    "restaurace denní menu Vítkovice Ostrava",
    "hospoda oběd Ostrava centrum",
    "hospoda oběd Poruba Ostrava",
    "restaurant lunch Ostrava",
]

DEFAULT_DISTRICT_MAP = {
    "poruba": "poruba",
    "moravská ostrava": "moravska-ostrava",
    "moravska ostrava": "moravska-ostrava",
    "mariánské hory": "marianske-hory",
    "marianske hory": "marianske-hory",
    "slezská ostrava": "slezska-ostrava",
    "slezska ostrava": "slezska-ostrava",
    "vítkovice": "vitkovice",
    "vitkovice": "vitkovice",
    "ostrava-jih": "ostrava-jih",
    "zábřeh": "ostrava-jih",
    "hrabůvka": "ostrava-jih",
}


# ── Helpers ───────────────────────────────────────────────────────────────────

def slugify(text: str) -> str:
    """Převede název restaurace na URL slug."""
    text = unidecode(text).lower()
    text = re.sub(r"[^a-z0-9]+", "-", text)
    return text.strip("-")


def detect_district(
    address: str,
    district_map: dict,
    default_district: str = "centrum",
) -> str:
    """Odhadne čtvrť z adresy podle předané mapy."""
    addr_lower = address.lower()
    for keyword, district in district_map.items():
        if keyword.lower() in addr_lower:
            return district
    return default_district


def get_supabase():
    return create_client(
        os.environ["SUPABASE_URL"],
        os.environ["SUPABASE_SERVICE_KEY"],
    )


# ── Google Places API ─────────────────────────────────────────────────────────

async def text_search(client: httpx.AsyncClient, query: str) -> list[dict]:
    """Vyhledá místa pomocí Text Search."""
    params = {
        "query": query,
        "language": "cs",
        "region": "cz",
        "key": GOOGLE_API_KEY,
    }
    response = await client.get(PLACES_TEXT_SEARCH_URL, params=params, timeout=15)
    response.raise_for_status()
    data = response.json()

    if data.get("status") not in ("OK", "ZERO_RESULTS"):
        Actor.log.warning(f"Places API status: {data.get('status')} pro query: {query}")
        return []

    results = []
    for place in data.get("results", []):
        results.append({
            "place_id": place["place_id"],
            "name": place["name"],
            "address": place.get("formatted_address", ""),
            "lat": place["geometry"]["location"]["lat"],
            "lng": place["geometry"]["location"]["lng"],
            "rating": place.get("rating"),
        })

    # Stránkování – max 20 výsledků na stránku, další přes next_page_token
    next_token = data.get("next_page_token")
    if next_token:
        await asyncio.sleep(2)  # Google vyžaduje krátkou pauzu
        params["pagetoken"] = next_token
        response2 = await client.get(PLACES_TEXT_SEARCH_URL, params=params, timeout=15)
        if response2.status_code == 200:
            data2 = response2.json()
            for place in data2.get("results", []):
                results.append({
                    "place_id": place["place_id"],
                    "name": place["name"],
                    "address": place.get("formatted_address", ""),
                    "lat": place["geometry"]["location"]["lat"],
                    "lng": place["geometry"]["location"]["lng"],
                    "rating": place.get("rating"),
                })

    return results


async def place_details(client: httpx.AsyncClient, place_id: str) -> dict:
    """Načte detail místa – web, telefon."""
    params = {
        "place_id": place_id,
        "fields": "website,formatted_phone_number,url",
        "language": "cs",
        "key": GOOGLE_API_KEY,
    }
    response = await client.get(PLACES_DETAILS_URL, params=params, timeout=15)
    response.raise_for_status()
    data = response.json()

    if data.get("status") != "OK":
        return {}

    result = data.get("result", {})
    return {
        "website": result.get("website"),
        "phone": result.get("formatted_phone_number"),
        "maps_url": result.get("url"),
    }


# ── Supabase operace ──────────────────────────────────────────────────────────

def restaurant_exists(db, place_id: str) -> bool:
    """Zkontroluje zda restaurace s daným Google place_id už existuje."""
    res = (
        db.table("restaurants")
        .select("id")
        .eq("google_place_id", place_id)
        .execute()
    )
    return len(res.data) > 0


def save_restaurant(db, restaurant: dict, district_map: dict, default_district: str) -> bool:
    """
    Uloží restauraci do Supabase.
    Vrátí True pokud byla vložena, False pokud už existovala.
    """
    if restaurant_exists(db, restaurant["place_id"]):
        return False

    # Ošetři duplicitní slug
    slug = slugify(restaurant["name"])
    existing_slug = (
        db.table("restaurants")
        .select("id")
        .eq("slug", slug)
        .execute()
    )
    if existing_slug.data:
        slug = f"{slug}-{restaurant['place_id'][-6:]}"

    db.table("restaurants").insert({
        "name": restaurant["name"],
        "slug": slug,
        "address": restaurant["address"],
        "lat": restaurant["lat"],
        "lng": restaurant["lng"],
        "district": detect_district(
            restaurant["address"],
            district_map,
            default_district,
        ),
        "cuisine_type": "Česká",       # výchozí, ručně upřesnit
        "scrape_method": "manual",      # výchozí, ručně nastavit
        "scrape_url": restaurant.get("website"),
        "website": restaurant.get("website"),
        "phone": restaurant.get("phone"),
        "rating": restaurant.get("rating"),
        "is_active": False,             # aktivovat ručně po kontrole
        "google_place_id": restaurant["place_id"],
    }).execute()

    return True


# ── Hlavní logika ─────────────────────────────────────────────────────────────

async def main() -> None:
    async with Actor:
        inp = await Actor.get_input() or {}
        queries = inp.get("queries") or DEFAULT_QUERIES
        dry_run = inp.get("dry_run", False)
        district_map = inp.get("district_map") or DEFAULT_DISTRICT_MAP
        default_district = inp.get("default_district", "centrum")

        if dry_run:
            Actor.log.info("DRY RUN – data se nezapíší do Supabase")

        Actor.log.info(f"Spouštím {len(queries)} vyhledávacích dotazů...")
        Actor.log.info(f"District map: {len(district_map)} záznamů, default: {default_district}")

        # 1. Vyhledej všechna místa
        all_places: dict[str, dict] = {}

        async with httpx.AsyncClient() as client:
            for query in queries:
                Actor.log.info(f"  Hledám: {query}")
                try:
                    results = await text_search(client, query)
                    new = 0
                    for place in results:
                        if place["place_id"] not in all_places:
                            all_places[place["place_id"]] = place
                            new += 1
                    Actor.log.info(f"  -> {len(results)} výsledků, {new} nových unikátních")
                    await asyncio.sleep(0.5)
                except Exception as e:
                    Actor.log.error(f"  Chyba při dotazu '{query}': {e}")

        Actor.log.info(f"Celkem unikátních míst: {len(all_places)}")

        # 2. Načti detaily pro každé místo
        Actor.log.info("Načítám detaily (web, telefon)...")
        enriched = []

        async with httpx.AsyncClient() as client:
            for i, (place_id, place) in enumerate(all_places.items()):
                try:
                    details = await place_details(client, place_id)
                    place.update(details)
                    enriched.append(place)
                    if (i + 1) % 10 == 0:
                        Actor.log.info(f"  {i + 1}/{len(all_places)} zpracováno")
                    await asyncio.sleep(0.2)
                except Exception as e:
                    Actor.log.error(f"  Detail error pro {place['name']}: {e}")
                    enriched.append(place)

        # 3. Zápis do Supabase nebo dry run výpis
        if dry_run:
            Actor.log.info("\n=== DRY RUN – nalezené restaurace ===")
            for p in enriched:
                district = detect_district(p["address"], district_map, default_district)
                Actor.log.info(
                    f"  {p['name']} | {district} | {p['address']} | web: {p.get('website', '-')}"
                )
            Actor.log.info(f"\nCelkem: {len(enriched)} restaurací")
        else:
            Actor.log.info("Zapisuji do Supabase...")
            db = get_supabase()
            inserted = 0
            skipped = 0

            for restaurant in enriched:
                try:
                    if save_restaurant(db, restaurant, district_map, default_district):
                        inserted += 1
                        Actor.log.info(f"  + {restaurant['name']}")
                    else:
                        skipped += 1
                        Actor.log.info(f"  ~ preskoceno (existuje): {restaurant['name']}")
                except Exception as e:
                    Actor.log.error(f"  Chyba pri zapisu {restaurant['name']}: {e}")

            Actor.log.info(f"\nHotovo: {inserted} novych, {skipped} preskocených")

        await Actor.push_data(enriched)


asyncio.run(main())
