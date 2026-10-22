"""Rule-based command parser for the simulated voice assistant. No model, no network.
Everything a tap can do should be sayable, so one parser serves typed and spoken input.

Safety rules in this file: a negated request ("do not resume", "don't undo") never becomes the action;
an explicit stop or pause (one that is not itself negated) is recognised everywhere and outranks any other
word in the sentence, so "stop, don't buy it" stops while "do not pause" does nothing; undo, resume and
settings changes need an explicit command form (a question or loose mention does nothing); a code counts only when it is the whole utterance."""
from __future__ import annotations
import re
from dataclasses import dataclass, field

from .catalog import CATEGORIES

YES = {"yes", "yeah", "yep", "yup", "sure", "confirm", "approve", "approved", "okay", "ok", "go ahead",
       "do it", "yes please", "please do", "buy it", "yes buy it"}
NO = {"no", "nope", "deny", "decline", "dont", "do not", "never mind", "nevermind", "no thanks"}
LATER = {"later", "not now", "skip", "ask me later", "maybe later"}

TIERS = {"suggest": "suggest", "suggestions": "suggest", "ask first": "ask", "ask": "ask",
         "within limits": "limits", "limits": "limits", "within my limits": "limits"}

# Negations ("didnt", "isnt", ...) never become the action. Reported or conditional speech ("I asked you to
# undo", "if I said set") is not a command either, except that stop and pause always work.
NEGATION = re.compile(r"\b(not|dont|didnt|doesnt|isnt|arent|wasnt|werent|havent|hasnt|hadnt|never|cant|cannot|"
                      r"wont|shouldnt|wouldnt|couldnt|mustnt|aint|without|nothing|neither|nobody)\b")
REPORTED = re.compile(r"\b(no|asked|said|told|wanted|meant|thought|wondered|whether|if)\b")
SAFE = re.compile(r"\b(stop|halt|abort|pause|hold on|wait)\b")
ACTION = re.compile(r"\b(undo|take it back|resume|unpause|start again|pause|stop|limit|cap|set|switch|change|"
                    r"put|move|make|approve|buy|digest|confirm|go ahead|do it|order|cancel)\b")
PERIOD = re.compile(r"\b(week|weekly|month|monthly|year|yearly|annual|hour|hourly)\b")
STOP = re.compile(r"\b(stop|halt|abort|cancel|hold on|wait)\b")
QUESTION = re.compile(r"^(what|whats|why|how|when|where|who|which|does|do|did|is|are|was|were|should)\b|\bmeans?\b")
POLITE = re.compile(r"^(?:(?:please|kindly|can you|could you|would you|will you|go ahead and|i want you to)\s+)+")
UNDO = re.compile(r"(?:undo(?: (?:it|that|this))?|undo (?:my|the) last(?: (?:order|purchase))?|take (?:it|that) back|"
                  r"take back (?:my|the) last (?:order|purchase)|cancel (?:my|the) last(?: (?:order|purchase))?)(?: please)?")
_FILLER = r"(?:(?:my|the|code|is|its|it|here|number|passcode|pin)\s+)*"


@dataclass
class Command:
    kind: str
    params: dict = field(default_factory=dict)


def normalize(text: str) -> str:
    t = (text or "").lower().replace("’", "'")
    t = re.sub(r"[^\w\s$.']", " ", t)
    t = t.replace("'", "")
    return re.sub(r"\s+", " ", t).strip()


def explicit_stops(t: str) -> set[str]:
    """The stop and pause words that are not themselves negated. A stop word counts as negated when a
    negation word sits among the two words before it ("do not pause", "never stop")."""
    found = set()
    for m in SAFE.finditer(t):
        before = t[:m.start()].split()[-2:]
        if not any(NEGATION.fullmatch(w) for w in before):
            found.add(m.group(1))
    return found


def _category(t: str):
    for c in CATEGORIES:
        if c in t or (c == "groceries" and "grocery" in t) or (c == "gifts" and "gift" in t):
            return c
    return None


def _number_minor(t: str):
    """The amount, only when the sentence names exactly one number (otherwise it is ambiguous)."""
    nums = re.findall(r"\$?\s*(\d+(?:\.\d{1,2})?)", t)
    return int(round(float(nums[0]) * 100)) if len(nums) == 1 else None


def code_only(text: str):
    """A 4 digit code, only when the WHOLE utterance is the code (optionally 'my code is ...', or digits
    spoken one at a time). Four digits inside other words are not a code."""
    t = normalize(text).rstrip(". ")
    m = re.fullmatch(_FILLER + r"(\d{4}|\d \d \d \d)", t)
    return m.group(1).replace(" ", "") if m else None


def parse(text: str) -> Command:
    t = normalize(text)
    if not t:
        return Command("empty")
    if t in YES:
        return Command("yes")
    if t in NO:
        return Command("no")
    if t in LATER:
        return Command("later")
    stops = explicit_stops(t)
    if ACTION.search(t) and (NEGATION.search(t) or (REPORTED.search(t) and not SAFE.search(t))) and not stops:
        return Command("negated")          # a negated request must never trigger the action
    if NEGATION.search(t) and SAFE.search(t) and not stops:
        return Command("negated")          # "don't wait", "never stop": no action
    cat = _category(t)
    # 1. An explicit stop or pause outranks everything else in the same sentence ("stop and resume groceries"
    #    stops, "stop, don't buy it" stops), but a negated one ("do not pause") does nothing.
    if "pause" in stops or t.startswith("stop buying") or "stop everything" in t:
        return Command("pause", {"category": cat})
    if stops:
        return Command("stop")
    # 2. Everything below needs an explicit command form. A question ("what does undo mean") or a loose
    #    mention ("avoid undo my last order") is never a command.
    question = bool(QUESTION.search(t))
    u = t if question else POLITE.sub("", t, count=1)
    if not question and UNDO.fullmatch(u):
        return Command("undo_last")
    if STOP.search(t):
        return Command("stop")
    if not question and re.match(r"(resume|unpause|start again)\b", u) and len(u.split()) <= 6:
        return Command("resume", {"category": cat})
    imperative = not question and re.match(r"(set|switch|change|put|move|make|limit|cap|raise|lower)\b", u)
    limit_form = not question and re.match(r"(?:(?:my|the) )?\w+ (?:limit|cap)\b", u)
    if (imperative or limit_form) and re.search(r"\d", t) and cat and re.search(r"\b(limit|cap|up to|most)\b", t):
        if PERIOD.search(t):
            return Command("unsupported_period")
        daily = bool(re.search(r"\b(a day|per day|daily|each day)\b", t))
        return Command("set_limit", {"category": cat, "minor": _number_minor(t), "daily": daily})
    if imperative and re.match(r"(set|switch|change|put|move|make)\b", u) and cat:
        for phrase in sorted(TIERS, key=len, reverse=True):
            if re.search(r"\b" + re.escape(phrase) + r"\b", t):
                return Command("set_tier", {"category": cat, "tier": TIERS[phrase]})
    if "digest" in t and not question:
        return Command("digest")
    if re.search(r"\b(rule|rules|mandate|allowed)\b", t) or "what can you buy" in t:
        return Command("show_rule", {"category": cat})
    if re.search(r"\b(what did you|what have you|activity|what happened|what was bought)\b", t):
        return Command("status")
    if re.search(r"\b(waiting|pending|what needs)\b", t):
        return Command("pending")
    return Command("unknown")
