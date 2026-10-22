"""Generated product illustrations. Abstract shapes only: no text, no logos, no brand marks."""
from __future__ import annotations
from .catalog import BY_ID

_SHAPES = {
    "carton": '<rect x="70" y="40" width="60" height="100" rx="6" fill="{a}"/><polygon points="70,40 100,20 130,40" fill="{b}"/>',
    "bowl": '<path d="M45 85 h110 a55 55 0 0 1 -110 0z" fill="{a}"/><ellipse cx="100" cy="85" rx="55" ry="10" fill="{b}"/>',
    "bag": '<path d="M65 55 h70 l8 85 h-86z" fill="{a}"/><rect x="65" y="42" width="70" height="14" rx="4" fill="{b}"/>',
    "bottle": '<rect x="80" y="70" width="40" height="75" rx="10" fill="{a}"/><rect x="92" y="35" width="16" height="40" rx="4" fill="{b}"/>',
    "rolls": '<rect x="45" y="70" width="40" height="60" rx="8" fill="{a}"/><rect x="90" y="70" width="40" height="60" rx="8" fill="{b}"/><rect x="135" y="70" width="40" height="60" rx="8" fill="{a}"/><ellipse cx="65" cy="70" rx="20" ry="7" fill="{b}"/><ellipse cx="110" cy="70" rx="20" ry="7" fill="{a}"/><ellipse cx="155" cy="70" rx="20" ry="7" fill="{b}"/>',
    "jug": '<rect x="62" y="48" width="76" height="95" rx="12" fill="{a}"/><rect x="82" y="28" width="36" height="24" rx="5" fill="{b}"/><circle cx="100" cy="98" r="20" fill="{b}"/>',
    "boxes": '<rect x="40" y="90" width="55" height="45" rx="5" fill="{a}"/><rect x="105" y="90" width="55" height="45" rx="5" fill="{b}"/><rect x="72" y="42" width="55" height="45" rx="5" fill="{a}"/>',
    "filter": '<rect x="50" y="40" width="100" height="100" rx="6" fill="{a}"/>' + "".join(f'<line x1="{x}" y1="46" x2="{x}" y2="134" stroke="{{b}}" stroke-width="6"/>' for x in range(62, 150, 16)),
    "candle": '<rect x="55" y="80" width="26" height="60" rx="4" fill="{a}"/><rect x="87" y="65" width="26" height="75" rx="4" fill="{b}"/><rect x="119" y="90" width="26" height="50" rx="4" fill="{a}"/><ellipse cx="100" cy="52" rx="7" ry="11" fill="{a}"/>',
    "puzzle": '<rect x="45" y="45" width="55" height="55" fill="{a}"/><rect x="100" y="45" width="55" height="55" fill="{b}"/><rect x="45" y="100" width="55" height="55" fill="{b}"/><rect x="100" y="100" width="55" height="55" fill="{a}"/><circle cx="100" cy="100" r="14" fill="{a}"/>',
    "book": '<rect x="55" y="40" width="90" height="110" rx="6" fill="{a}"/><rect x="55" y="40" width="14" height="110" fill="{b}"/><line x1="85" y1="70" x2="130" y2="70" stroke="{b}" stroke-width="5"/><line x1="85" y1="90" x2="130" y2="90" stroke="{b}" stroke-width="5"/>',
    "pan": '<circle cx="90" cy="95" r="48" fill="{a}"/><circle cx="90" cy="95" r="34" fill="{b}"/><rect x="132" y="88" width="55" height="14" rx="6" fill="{a}"/>',
}


def product_svg(pid: str) -> str:
    p = BY_ID[pid]
    a, b = f"hsl({p.hue},45%,32%)", f"hsl({p.hue},55%,72%)"
    bg = f"hsl({p.hue},40%,92%)"
    body = _SHAPES[p.shape].replace("{a}", a).replace("{b}", b)
    return ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 200 180" role="img" '
            f'aria-label="Product illustration"><rect width="200" height="180" fill="{bg}"/>{body}</svg>')
