"""Guard tests for the explainer: no third party names, no dash punctuation, scene ids resolve."""
import re
from pathlib import Path

HERE = Path(__file__).parent
FILES = [HERE / n for n in ("index.html", "explainer.css", "explainer.js", "README.md")]

# Built from fragments so this file does not itself contain the names.
BANNED = [
    "ama" + "zon", "ale" + "xa", "ech" + "o dot", "rufus", "pri" + "me video", "pri" + "me day",
    "audi" + "ble", "whole " + "foods", "walm" + "art", "goo" + "gle", "app" + "le", "micro" + "soft",
    "open" + "ai", "mastercard", "visa ", "pay" + "pal", "stri" + "pe",
]
BANNED_WORD = [r"\bpri" + "me\b", r"\bech" + "o\b"]


# The one place third party names may appear: the ownership notice on the end card. Built from fragments
# so this file does not itself contain the names. Everything else stays under the ban.
NOTICE = ("Ama" + "zon, Ale" + "xa+ and related names, marks and page designs are the property of Ama" + "zon.com, "
          "Inc. or its affiliates, shown here only to illustrate an entry in the Build, Ship, Shape Ama" + "zon "
          "Developer Hackathon. Nudge is an independent simulation, not affiliated with or endorsed by Ama" + "zon. "
          "All rights remain with their owners.")


def text(p):
    return p.read_text(encoding="utf-8")


def text_without_notice(p):
    return text(p).replace(NOTICE, "")


def test_files_exist():
    for p in FILES:
        assert p.exists(), p


def test_no_banned_names():
    for p in FILES:
        low = text_without_notice(p).lower()
        for b in BANNED:
            assert b not in low, f"{p.name} contains banned term {b!r}"
        for pat in BANNED_WORD:
            assert not re.search(pat, low), f"{p.name} matches {pat}"


def test_the_exact_notice_is_on_the_end_card_and_only_there():
    js = text(HERE / "explainer.js")
    assert js.count(NOTICE) == 1 and 's9_notice: "' + NOTICE + '"' in js
    assert re.search(r'id:\s*"scene-9",\s*dur:\s*4,', js)
    for name in ("index.html", "explainer.css", "README.md"):
        assert NOTICE not in text(HERE / name), name
    # the notice is the last scene, and its caption is empty so nothing covers it
    refs = re.findall(r'id:\s*"(scene-\d+)"', js)
    assert refs[-1] == "scene-9" and 'cap9: ""' in js
    assert "\u2014" not in NOTICE and "\u2013" not in NOTICE


def test_close_scene_text():
    js = text(HERE / "explainer.js")
    assert "Undo a purchase. Verify the record." in js
    assert "Undo or verify anything" not in js and "Undo or verify anything" not in text(HERE / "README.md")
    assert 's7_open: "Open source, MIT license. Code linked in this submission."' in js
    assert "See the submission page" not in js
    assert 'data-t="s7_open"' in text(HERE / "index.html")


def test_no_em_or_en_dashes():
    for p in FILES:
        t = text(p)
        assert "\u2014" not in t and "\u2013" not in t, p.name


def test_no_spaced_hyphen_punctuation():
    """No ' - ' used as a dash in prose or on-screen strings."""
    for p in (HERE / "index.html", HERE / "README.md"):
        for n, line in enumerate(text(p).splitlines(), 1):
            assert " - " not in line, f"{p.name}:{n}"
    js = text(HERE / "explainer.js")
    block = js.split("const TEXT = {", 1)[1].split("};", 1)[0]
    for n, line in enumerate(block.splitlines(), 1):
        if line.strip().startswith("//"):
            continue
        assert " - " not in line, f"TEXT line {n}: {line}"
        assert "--" not in line, f"TEXT line {n}: {line}"


def test_scene_ids_exist():
    html = text(HERE / "index.html")
    js = text(HERE / "explainer.js")
    ids = set(re.findall(r'<section class="scene" id="([^"]+)"', html))
    refs = re.findall(r'id:\s*"(scene-\d+)"', js)
    assert 6 <= len(refs) <= 9
    for r in refs:
        assert r in ids, r
    assert set(refs) == ids


def test_text_keys_exist():
    html = text(HERE / "index.html")
    js = text(HERE / "explainer.js")
    block = js.split("const TEXT = {", 1)[1].split("};", 1)[0]
    keys = set(re.findall(r'^\s*([A-Za-z0-9_]+):', block, re.M)) | set(re.findall(r',\s*([A-Za-z0-9_]+):', block))
    used = set(re.findall(r'data-ta?="([^"]+)"', html)) | set(re.findall(r'cap:\s*"([^"]+)"', js))
    missing = used - keys
    assert not missing, missing
    assert "DR" + "AFT" not in js


def test_total_duration_in_range():
    js = text(HERE / "explainer.js")
    total = sum(int(x) for x in re.findall(r'dur:\s*(\d+)', js))
    assert 60 <= total <= 95


def test_journey_scene_and_asset():
    html = text(HERE / "index.html")
    js = text(HERE / "explainer.js")
    css = text(HERE / "explainer.css")
    assert (HERE / "assets" / "journey_illustration.png").exists()
    assert 'src="assets/journey_illustration.png"' in html
    assert 's8_illus: "Illustration"' in js
    assert 'badge: "Simulation"' in js
    assert 'id: "scene-8"' in js and "@keyframes jpan" in css
    assert "TO" + "DO" not in js.split("const SCENES")[0].split("const TEXT")[1]
    # reduced motion must still show a single final callout
    assert ".co5 { opacity: 1;" in css


def test_keyboard_jump_covers_all_scenes():
    js = text(HERE / "explainer.js")
    n = len(re.findall(r'id:\s*"(scene-\d+)"', js))
    assert f'k <= "{n}"' in js and f"[1-{n}]" in js


def test_no_external_resources():
    for p in (HERE / "index.html", HERE / "explainer.css", HERE / "explainer.js"):
        assert not re.search(r'(src|href)="https?://|url\(\s*https?:|@import|fetch\(', text(p)), p.name
