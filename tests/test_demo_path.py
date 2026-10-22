"""The scripted demo path: product photos, the daily check, the end date, and the per-order charge count."""
import os

from conftest import buy_by_voice, say, HUMAN
from app import catalog as C

IMG = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "app", "static", "img", "products")


def test_every_catalog_product_has_a_photo_file():
    for p in C.PRODUCTS:
        path = os.path.join(IMG, p.id + ".jpg")
        assert os.path.isfile(path) and os.path.getsize(path) > 2000, p.id


def test_product_view_points_at_the_photo(svc):
    for p in svc.state()["products"]:
        assert p["image"] == f"/img/products/{p['id']}.jpg"


def test_daily_check_buys_inside_limits_and_visibly_declines(svc):
    buy_by_voice(svc, "coffee", "suggestion")
    svc.accept_offer("groceries", HUMAN, svc.offers["groceries"]["proposal_id"])
    svc.run_assistant()
    texts = [f["text"] for f in svc.feed]
    kinds = {f["kind"] for f in svc.feed}
    assert "auto" in kinds or "bought" in kinds
    declined = [f for f in svc.feed if f["kind"] == "did_not"]
    assert declined and "daily limit" in declined[0]["text"] and "waiting for your yes" in declined[0]["text"], texts


def test_offer_shows_the_end_date_before_acceptance(svc):
    buy_by_voice(svc, "coffee", "suggestion")
    offer = svc.state()["categories"]["groceries"]["offer"]["text"]
    assert "This rule would end on Tue, Jan 19, 2027" in offer or "Jan 19, 2027" in offer


def test_charge_count_is_per_order(svc):
    oid = buy_by_voice(svc, "pasta")
    st = svc.state()
    mine = next(o for o in st["orders"] if o["id"] == oid)
    assert mine["charges"] == 1 and st["payments_made"] == svc.base_calls + 1
    seeded = next(o for o in st["orders"] if o["id"] == "O-001")
    assert seeded["charges"] == 1


def test_transcript_messages_carry_increasing_ids_past_the_forty_window(svc):
    for n in range(60):
        say(svc, "what is my rule")
    ids = [m["id"] for m in svc.state()["transcript"]]
    assert len(ids) == 40 and ids == sorted(set(ids)) and ids[-1] > 40
