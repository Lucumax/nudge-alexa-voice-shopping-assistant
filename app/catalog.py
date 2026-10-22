"""Synthetic catalog, merchants, and demo history. All data is made up for the simulation.
Product and store names are Amazon names used for this Amazon hackathon; ratings and prices are sample data. Delivery dates are computed from a fixed demo date."""
from __future__ import annotations
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone

DEMO_TZ = timezone(timedelta(hours=-4))  # fixed offset so no tz database is needed
DEMO_NOW = datetime(2026, 10, 21, 9, 0, tzinfo=DEMO_TZ)  # Wednesday, fixed demo clock start
CUTOFF_HOUR = 14  # orders placed before 2 pm local ship the same day
CATEGORIES = ("groceries", "household", "gifts")
STEP_UP_MINOR = 10_000      # $100.00: at or above this, a phone code is always required
AUTONOMY_CAP_MINOR = 9_999  # within-limits can never exceed $99.99 per item
UNDO_WINDOW_HOURS = 24

MERCHANTS = {
    "amazon-fresh": ("Amazon Fresh", "groceries"),
    "whole-foods": ("Whole Foods Market", "groceries"),
    "amazon-household": ("Amazon", "household"),
    "amazon-gifts": ("Amazon", "gifts"),
}


@dataclass(frozen=True)
class Product:
    id: str
    name: str
    category: str
    merchant: str
    price_minor: int
    rating: float          # sample data, not real reviews
    ship_days: int         # business days after the ship date
    return_days: int       # 0 means perishable: refund if it arrives not right
    shape: str             # svg motif
    hue: int
    blurb: str


P = Product
PRODUCTS: tuple[Product, ...] = (
    P("oat-milk", "365 by Whole Foods Market Oat Drink, 6 pack", "groceries", "whole-foods", 1440, 4.6, 1, 0, "carton", 38, "Shelf-stable, unsweetened."),
    P("pasta", "Happy Belly Whole-Grain Pasta, 4 pack", "groceries", "amazon-fresh", 920, 4.5, 1, 0, "bowl", 28, "Dried pasta, 16 oz bags."),
    P("coffee", "Happy Belly Ground Coffee, 12 oz", "groceries", "amazon-fresh", 1150, 4.4, 1, 0, "bag", 24, "Medium roast."),
    P("olive-oil", "Amazon Fresh Extra Virgin Olive Oil, 500 ml", "groceries", "amazon-fresh", 1380, 4.7, 2, 30, "bottle", 90, "Cold-pressed, glass bottle."),
    P("paper-towels", "Amazon Basics Paper Towels, 8 rolls", "household", "amazon-household", 1699, 4.5, 2, 30, "rolls", 200, "Two-ply, tear-to-size."),
    P("detergent", "Solimo Laundry Detergent, 64 loads", "household", "amazon-household", 1850, 4.3, 2, 30, "jug", 215, "Fragrance-free liquid."),
    P("storage-set", "Amazon Basics Storage Containers, 12 piece", "household", "amazon-household", 3400, 4.6, 3, 30, "boxes", 170, "Nesting set with lids."),
    P("air-filter", "Amazon Basics Air Purifier Filter, large room", "household", "amazon-household", 12900, 4.5, 3, 30, "filter", 190, "Replacement filter, fits the demo purifier."),
    P("candle-set", "Amazon Basics Beeswax Candle Set", "gifts", "amazon-gifts", 2800, 4.8, 3, 45, "candle", 45, "Four hand-poured candles."),
    P("puzzle", "Amazon Basics Landscape Puzzle, 1000 piece", "gifts", "amazon-gifts", 2400, 4.5, 3, 45, "puzzle", 330, "Original illustration."),
    P("notebooks", "Amazon Basics Notebook Trio", "gifts", "amazon-gifts", 1900, 4.6, 3, 45, "book", 12, "Lay-flat, dotted pages."),
    P("saute-pan", "Amazon Basics Cast Iron Skillet, 12 inch", "gifts", "amazon-gifts", 11200, 4.7, 4, 45, "pan", 20, "Pre-seasoned."),
)
BY_ID = {p.id: p for p in PRODUCTS}


def merchant_name(mid: str) -> str:
    return MERCHANTS[mid][0]


def prime_eligible(p: Product) -> bool:
    """Sample data: items that arrive within three business days carry the Prime text badge."""
    return p.ship_days <= 3


def merchants_for(category: str) -> tuple[str, ...]:
    return tuple(m for m, (_, c) in MERCHANTS.items() if c == category)


def add_business_days(d: date, n: int) -> date:
    while n > 0:
        d += timedelta(days=1)
        if d.weekday() < 5:
            n -= 1
    return d


def delivery_date(p: Product, placed: datetime) -> date:
    """Ship date: same business day if before the cutoff, else the next business day.
    Arrival: ship date plus ship_days business days. Weekends are skipped."""
    ship = placed.date()
    if placed.hour >= CUTOFF_HOUR or ship.weekday() >= 5:
        ship = add_business_days(ship, 1)
    return add_business_days(ship, p.ship_days)


# Seeded signals the assistant can reason from. Each suggestion kind carries its own facts.
# Dates are fixed so the demo is deterministic. Reminders are things the user typed in.
SIGNALS = (
    dict(id="s-paper", product="paper-towels", kind="restock", last_bought=date(2026, 9, 26), cycle_days=28),
    dict(id="s-coffee", product="coffee", kind="price_drop", last_bought=date(2026, 9, 30), old_minor=1290),
    dict(id="s-pasta", product="pasta", kind="restock", last_bought=date(2026, 9, 24), cycle_days=26),
    dict(id="s-oat", product="oat-milk", kind="restock", last_bought=date(2026, 9, 28), cycle_days=21),
    dict(id="s-olive", product="olive-oil", kind="restock", last_bought=date(2026, 9, 8), cycle_days=45),
    dict(id="s-detergent", product="detergent", kind="restock", last_bought=date(2026, 9, 22), cycle_days=30),
    dict(id="s-candle", product="candle-set", kind="reminder", note_date=date(2026, 10, 3), event_date=date(2026, 11, 2)),
    dict(id="s-filter", product="air-filter", kind="restock", last_bought=date(2026, 6, 20), cycle_days=120),
)
CARD_LAST4 = "4242"  # fictional demo card ending; no real card data exists in this project
