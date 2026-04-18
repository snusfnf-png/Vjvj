# ╔══════════════════════════════════════════════════════════════════╗
# ║                        🎨 JellyColor v2                        ║
# ║  Перекраска стикеров/эмодзи + текстовые шаблоны + SVG-вставка  ║
# ╚══════════════════════════════════════════════════════════════════╝
#
# meta developer: @Iklyu
# scope: hikka_only
# scope: hikka_min 1.6.3
# requires: Pillow fonttools

__version__ = (2, 0, 0)

import asyncio
import glob
import gzip
import io
import json
import math
import os
import re
import time
import xml.etree.ElementTree as ET
from typing import Any, Dict, List, Optional, Tuple

from PIL import Image

from telethon.tl import functions, types
from telethon.tl.types import (
    DocumentAttributeSticker,
    DocumentAttributeCustomEmoji,
    InputStickerSetShortName,
    InputStickerSetID,
    InputStickerSetEmpty,
    Message,
    MessageEntityCustomEmoji,
)

from .. import loader, utils


PRESET_COLORS: Dict[str, str] = {
    "🔴 Красный":    "#FF3B30",
    "🟠 Оранжевый":  "#FF9500",
    "🟡 Жёлтый":     "#FFCC00",
    "🟢 Зелёный":    "#34C759",
    "🔵 Синий":      "#007AFF",
    "🟣 Фиолетовый": "#AF52DE",
    "⚫️ Чёрный":     "#1C1C1E",
    "⚪️ Белый":      "#F2F2F7",
    "🩷 Розовый":    "#FF2D55",
    "🩵 Голубой":    "#5AC8FA",
    "🟤 Коричневый": "#A2845E",
    "🩶 Серый":      "#8E8E93",
}

PE = {
    "ok":      "5870633910337015697",
    "err":     "5870657884844462243",
    "brush":   "6050679691004612757",
    "pack":    "5778672437122045013",
    "palette": "5870676941614354370",
    "link":    "5769289093221454192",
    "stats":   "5870921681735781843",
    "clock":   "5983150113483134607",
    "sticker": "5886285355279193209",
    "write":   "5870753782874246579",
    "media":   "6035128606563241721",
    "eye":     "6037397706505195857",
    "trash":   "5870875489362513438",
    "export":  "5963103826075456248",
    "info":    "6028435952299413210",
}

# ─── Gradient presets ────────────────────────────────────────────────────────
GRADIENT_PRESETS = [
    {"id":"sunset",   "name":"🌅 Закат",      "colors":["#E63946","#F4A261","#FFD700"], "dir":"d"},
    {"id":"ocean",    "name":"🌊 Океан",      "colors":["#023E8A","#0096C7","#90E0EF"], "dir":"dr"},
    {"id":"aurora",   "name":"💫 Аврора",     "colors":["#7B2FBE","#00B4D8","#00F5D4"], "dir":"h"},
    {"id":"fire",     "name":"🔥 Огонь",      "colors":["#6A040F","#D62828","#FCBF49"], "dir":"v"},
    {"id":"sakura",   "name":"🌸 Сакура",     "colors":["#5C0A5E","#C77DFF","#FFCCD5"], "dir":"d"},
    {"id":"galaxy",   "name":"🌌 Галактика",  "colors":["#03045E","#7B2FBE","#E040FB"], "dir":"dr"},
    {"id":"forest",   "name":"🌿 Лес",        "colors":["#0A2208","#2D8B2D","#C8E6C9"], "dir":"v"},
    {"id":"neon",     "name":"⚡ Неон",       "colors":["#00D4FF","#7B2FBE","#FF006E"], "dir":"h"},
    {"id":"gold",     "name":"👑 Золото",     "colors":["#7B5200","#FFC300","#FFF9C4"], "dir":"d"},
    {"id":"candy",    "name":"🍭 Конфета",    "colors":["#FF0099","#FF7B00","#FFD700"], "dir":"dr"},
]

TEMPLATE_SETS = [
    {"title": "♣️ BLACK HOLE",  "short_name": "main_by_emojicreationbot"},
    {"title": "🎨 COLOR",       "short_name": "main2_by_emojimakers_bot"},
    {"title": "⭐ EXCLUSIVE",   "short_name": "main2_by_emojimakers_bot"},
]

TEMPLATE_PLACEHOLDER = "emc"

SESSION_TTL = 600
CACHE_DIR = "/tmp/jelly_cache"
MAX_TGS_SIZE = 63 * 1024
RECOLOR_CONCURRENCY = 4

os.makedirs(CACHE_DIR, exist_ok=True)


def pe(emoji: str, eid: str) -> str:
    return '<tg-emoji emoji-id="' + eid + '">' + emoji + '</tg-emoji>'


def hex_to_rgb(hex_color: str):
    h = hex_color.lstrip("#")
    return tuple(int(h[i:i+2], 16) for i in (0, 2, 4))


def rgb_to_hex(r: int, g: int, b: int) -> str:
    return "#{:02X}{:02X}{:02X}".format(r, g, b)


# ─── Image tinting ────────────────────────────────────────────────────────────

def tint_image(img: Image.Image, hex_color: str) -> Image.Image:
    r, g, b = hex_to_rgb(hex_color)
    img = img.convert("RGBA")
    data = img.load()
    for y in range(img.height):
        for x in range(img.width):
            ro, go, bo, ao = data[x, y]
            if ao > 0:
                gray = int(0.299 * ro + 0.587 * go + 0.114 * bo)
                data[x, y] = (int(r * gray / 255), int(g * gray / 255), int(b * gray / 255), ao)
    return img


# ─── Lottie gradient ──────────────────────────────────────────────────────────

def apply_gradient_lottie(lottie_json: dict, gradient: dict) -> dict:
    """
    Слойный градиент: каждый fl/st-слой получает свой уникальный цвет из
    спектра градиента (по порядку в стеке), сохраняя оригинальную яркость.
    """
    colors_hex = gradient["colors"]
    n = len(colors_hex)
    rgb_n = [[r/255, g/255, b/255] for r,g,b in [hex_to_rgb(c) for c in colors_hex]]

    def lerp(t):
        t = max(0.0, min(1.0, t))
        if n == 1: return list(rgb_n[0])
        s = t * (n-1); i = int(s)
        if i >= n-1: return list(rgb_n[-1])
        f = s - i
        return [rgb_n[i][j] + (rgb_n[i+1][j]-rgb_n[i][j])*f for j in range(3)]

    color_shapes = []
    def _collect(obj):
        if isinstance(obj, dict):
            ty = obj.get("ty","")
            if ty in ("fl","st"):
                k = obj.get("c",{}).get("k")
                if k is not None:
                    color_shapes.append(obj)
            else:
                for v in obj.values(): _collect(v)
        elif isinstance(obj, list):
            for item in obj: _collect(item)
    _collect(lottie_json)

    total = len(color_shapes)
    if total == 0: return lottie_json

    for idx, shape in enumerate(color_shapes):
        t = idx / (total-1) if total > 1 else 0.5
        tc = lerp(t)
        k = shape["c"]["k"]
        if isinstance(k, list) and len(k) >= 3 and isinstance(k[0], (int,float)):
            lum = max(0.01, 0.299*k[0] + 0.587*k[1] + 0.114*k[2])
            shape["c"]["k"] = [tc[j]*lum for j in range(3)] + list(k[3:] or [1.0])
        elif isinstance(k, list):
            for kf in k:
                if isinstance(kf, dict) and "s" in kf:
                    s = kf["s"]
                    if isinstance(s, list) and len(s) >= 3 and isinstance(s[0], (int,float)):
                        lum = max(0.01, 0.299*s[0] + 0.587*s[1] + 0.114*s[2])
                        kf["s"] = [tc[j]*lum for j in range(3)] + list(s[3:] or [1.0])

    return lottie_json


# ─── Lottie tinting ───────────────────────────────────────────────────────────

def tint_lottie(lottie_json: dict, hex_color: str) -> dict:
    r, g, b = hex_to_rgb(hex_color)
    nr, ng, nb = r / 255, g / 255, b / 255

    def _walk(obj):
        if isinstance(obj, dict):
            if "c" in obj and isinstance(obj["c"], dict) and "k" in obj["c"]:
                k = obj["c"]["k"]
                if isinstance(k, list) and len(k) >= 3 and isinstance(k[0], (int, float)):
                    gray = 0.299 * k[0] + 0.587 * k[1] + 0.114 * k[2]
                    obj["c"]["k"] = [nr * gray, ng * gray, nb * gray] + (k[3:] or [1.0])
                elif isinstance(k, list):
                    for kf in k:
                        if isinstance(kf, dict) and "s" in kf:
                            s = kf["s"]
                            if isinstance(s, list) and len(s) >= 3:
                                gray = 0.299 * s[0] + 0.587 * s[1] + 0.114 * s[2]
                                kf["s"] = [nr * gray, ng * gray, nb * gray] + (s[3:] or [1.0])
            for v in obj.values():
                _walk(v)
        elif isinstance(obj, list):
            for item in obj:
                _walk(item)

    _walk(lottie_json)
    return lottie_json


def get_dominant_lottie_color(lottie_json: dict) -> Optional[str]:
    def _walk(obj):
        if isinstance(obj, dict):
            if obj.get("ty") == "fl":
                k = obj.get("c", {}).get("k", [])
                if isinstance(k, list) and len(k) >= 3 and isinstance(k[0], (int, float)):
                    return rgb_to_hex(int(k[0]*255), int(k[1]*255), int(k[2]*255))
            for v in obj.values():
                r = _walk(v)
                if r:
                    return r
        elif isinstance(obj, list):
            for item in obj:
                r = _walk(item)
                if r:
                    return r
        return None
    return _walk(lottie_json)


# ─── Sticker cache ────────────────────────────────────────────────────────────

def _cache_key(doc) -> str:
    return os.path.join(CACHE_DIR, f"{doc.id}.bin")


async def download_cached(client, doc) -> bytes:
    path = _cache_key(doc)
    if os.path.exists(path):
        try:
            with open(path, "rb") as f:
                return f.read()
        except Exception:
            pass
    data = await client.download_media(doc, bytes)
    try:
        with open(path, "wb") as f:
            f.write(data)
    except Exception:
        pass
    return data


# ─── TGS size guard ───────────────────────────────────────────────────────────

def compress_tgs(lottie: dict) -> bytes:
    raw = json.dumps(lottie, separators=(",", ":")).encode("utf-8")
    compressed = gzip.compress(raw, compresslevel=9)
    if len(compressed) > MAX_TGS_SIZE:
        def _strip_names(obj):
            if isinstance(obj, dict):
                obj.pop("nm", None)
                obj.pop("mn", None)
                for v in obj.values():
                    _strip_names(v)
            elif isinstance(obj, list):
                for item in obj:
                    _strip_names(item)
        _strip_names(lottie)
        compressed = gzip.compress(
            json.dumps(lottie, separators=(",", ":")).encode("utf-8"),
            compresslevel=9,
        )
    return compressed


# ─── SVG → Lottie shapes ──────────────────────────────────────────────────────

def _parse_svg_style(style_str: str) -> dict:
    d = {}
    for part in style_str.split(";"):
        part = part.strip()
        if ":" in part:
            k, v = part.split(":", 1)
            d[k.strip().lower()] = v.strip()
    return d


def _parse_svg_path_d(d: str) -> List[dict]:
    tokens = re.findall(
        r"[MmLlHhVvCcQqAaZzSsTt]|[-+]?[0-9]*\.?[0-9]+(?:[eE][-+]?[0-9]+)?",
        d,
    )
    shapes = []
    vs, ii, oo = [], [], []
    cur_x, cur_y = 0.0, 0.0
    start_x, start_y = 0.0, 0.0
    cmd = None
    idx = 0

    def _close_path(closed: bool = False):
        nonlocal vs, ii, oo
        if len(vs) >= 2:
            shapes.append({
                "ty": "sh", "nm": "p",
                "ks": {"a": 0, "k": {
                    "c": closed,
                    "v": [list(v) for v in vs],
                    "i": [list(v) for v in ii],
                    "o": [list(v) for v in oo],
                }},
            })
        vs.clear(); ii.clear(); oo.clear()

    def _num():
        nonlocal idx
        while idx < len(tokens) and not re.match(r"[-+]?[0-9]", tokens[idx]):
            idx += 1
        if idx >= len(tokens):
            return 0.0
        v = float(tokens[idx]); idx += 1
        return v

    while idx < len(tokens):
        t = tokens[idx]
        if re.match(r"[MmLlHhVvCcQqAaZzSsTt]", t):
            cmd = t; idx += 1

        if cmd in ("M", "m"):
            _close_path()
            x = _num(); y = _num()
            if cmd == "m": cur_x += x; cur_y += y
            else: cur_x = x; cur_y = y
            start_x, start_y = cur_x, cur_y
            vs.append([cur_x, cur_y]); ii.append([0.0, 0.0]); oo.append([0.0, 0.0])
            cmd = "L" if cmd == "M" else "l"

        elif cmd in ("L", "l"):
            x = _num(); y = _num()
            if cmd == "l": cur_x += x; cur_y += y
            else: cur_x = x; cur_y = y
            vs.append([cur_x, cur_y]); ii.append([0.0, 0.0]); oo.append([0.0, 0.0])

        elif cmd == "H": cur_x = _num(); vs.append([cur_x, cur_y]); ii.append([0.0,0.0]); oo.append([0.0,0.0])
        elif cmd == "h": cur_x += _num(); vs.append([cur_x, cur_y]); ii.append([0.0,0.0]); oo.append([0.0,0.0])
        elif cmd == "V": cur_y = _num(); vs.append([cur_x, cur_y]); ii.append([0.0,0.0]); oo.append([0.0,0.0])
        elif cmd == "v": cur_y += _num(); vs.append([cur_x, cur_y]); ii.append([0.0,0.0]); oo.append([0.0,0.0])

        elif cmd in ("C", "c"):
            c1x=_num(); c1y=_num(); c2x=_num(); c2y=_num(); ex=_num(); ey=_num()
            if cmd=="c": c1x+=cur_x; c1y+=cur_y; c2x+=cur_x; c2y+=cur_y; ex+=cur_x; ey+=cur_y
            pvx,pvy=vs[-1]
            oo[-1]=[c1x-pvx,c1y-pvy]
            vs.append([ex,ey]); ii.append([c2x-ex,c2y-ey]); oo.append([0.0,0.0])
            cur_x,cur_y=ex,ey

        elif cmd in ("S", "s"):
            c2x=_num(); c2y=_num(); ex=_num(); ey=_num()
            if cmd=="s": c2x+=cur_x; c2y+=cur_y; ex+=cur_x; ey+=cur_y
            pvx,pvy=vs[-1]; prev_oo=oo[-1]
            c1x=pvx-prev_oo[0]; c1y=pvy-prev_oo[1]
            oo[-1]=[c1x-pvx,c1y-pvy]
            vs.append([ex,ey]); ii.append([c2x-ex,c2y-ey]); oo.append([0.0,0.0])
            cur_x,cur_y=ex,ey

        elif cmd in ("Q", "q"):
            qcx=_num(); qcy=_num(); ex=_num(); ey=_num()
            if cmd=="q": qcx+=cur_x; qcy+=cur_y; ex+=cur_x; ey+=cur_y
            pvx,pvy=vs[-1]
            c1x=pvx+2/3*(qcx-pvx); c1y=pvy+2/3*(qcy-pvy)
            c2x=ex+2/3*(qcx-ex);   c2y=ey+2/3*(qcy-ey)
            oo[-1]=[c1x-pvx,c1y-pvy]
            vs.append([ex,ey]); ii.append([c2x-ex,c2y-ey]); oo.append([0.0,0.0])
            cur_x,cur_y=ex,ey

        elif cmd in ("T", "t"):
            ex=_num(); ey=_num()
            if cmd=="t": ex+=cur_x; ey+=cur_y
            pvx,pvy=vs[-1]; prev_oo=oo[-1]
            qcx=pvx-prev_oo[0]; qcy=pvy-prev_oo[1]
            c1x=pvx+2/3*(qcx-pvx); c1y=pvy+2/3*(qcy-pvy)
            c2x=ex+2/3*(qcx-ex);   c2y=ey+2/3*(qcy-ey)
            oo[-1]=[c1x-pvx,c1y-pvy]
            vs.append([ex,ey]); ii.append([c2x-ex,c2y-ey]); oo.append([0.0,0.0])
            cur_x,cur_y=ex,ey

        elif cmd in ("A", "a"):
            _num(); _num(); _num(); _num(); _num()
            ex=_num(); ey=_num()
            if cmd=="a": ex+=cur_x; ey+=cur_y
            vs.append([ex,ey]); ii.append([0.0,0.0]); oo.append([0.0,0.0])
            cur_x,cur_y=ex,ey

        elif cmd in ("Z", "z"):
            if vs:
                vs.append([start_x,start_y]); ii.append([0.0,0.0]); oo.append([0.0,0.0])
            _close_path(closed=True)
            cur_x,cur_y=start_x,start_y; cmd=None

    _close_path(closed=False)
    return shapes


def _apply_svg_transform(shapes: List[dict], transform_str: str) -> List[dict]:
    if not transform_str:
        return shapes
    m = re.match(r"matrix\(([^)]+)\)", transform_str)
    if m:
        vals = [float(x) for x in re.split(r"[,\s]+", m.group(1).strip())]
        if len(vals) == 6:
            a,b,c,d,e,f = vals
            for sh in shapes:
                k = sh.get("ks",{}).get("k",{})
                if isinstance(k,dict):
                    k["v"] = [[a*pt[0]+c*pt[1]+e, b*pt[0]+d*pt[1]+f] for pt in k.get("v",[])]
                    k["i"] = [[a*t[0]+c*t[1], b*t[0]+d*t[1]] for t in k.get("i",[])]
                    k["o"] = [[a*t[0]+c*t[1], b*t[0]+d*t[1]] for t in k.get("o",[])]
    t = re.match(r"translate\(([^)]+)\)", transform_str)
    if t:
        vals = [float(x) for x in re.split(r"[,\s]+", t.group(1).strip())]
        tx=vals[0]; ty=vals[1] if len(vals)>1 else 0.0
        for sh in shapes:
            k = sh.get("ks",{}).get("k",{})
            if isinstance(k,dict):
                k["v"] = [[pt[0]+tx,pt[1]+ty] for pt in k.get("v",[])]
    s_ = re.match(r"scale\(([^)]+)\)", transform_str)
    if s_:
        vals = [float(x) for x in re.split(r"[,\s]+", s_.group(1).strip())]
        sx=vals[0]; sy=vals[1] if len(vals)>1 else sx
        for sh in shapes:
            k=sh.get("ks",{}).get("k",{})
            if isinstance(k,dict):
                k["v"] = [[pt[0]*sx,pt[1]*sy] for pt in k.get("v",[])]
                k["i"] = [[pt[0]*sx,pt[1]*sy] for pt in k.get("i",[])]
                k["o"] = [[pt[0]*sx,pt[1]*sy] for pt in k.get("o",[])]
    return shapes


def svg_to_lottie_shapes(
    svg_bytes: bytes,
    cx: float, cy: float,
    target_w: float, target_h: float,
    hex_color: Optional[str] = None,
    padding: float = 0.1,
) -> List[dict]:
    """
    Парсит SVG и возвращает Lottie shape-пути,
    отмасштабированные и отцентрованные по (cx,cy).
    hex_color — цвет fill (None = авто из эмодзи).

    ИСПРАВЛЕНО: убран Y-флип. SVG и Lottie оба используют Y вниз,
    поэтому флип не нужен и ломал отображение SVG.
    """
    import logging
    log = logging.getLogger("JellyColor")
    try:
        svg_str = svg_bytes.decode("utf-8", errors="replace")
        svg_str = re.sub(r'\s+xmlns(?::\w+)?="[^"]*"', '', svg_str)
        svg_str = re.sub(r'<\?xml[^>]*\?>', '', svg_str)
        root = ET.fromstring(svg_str)
    except Exception as e:
        log.error(f"svg_to_lottie_shapes: XML parse error: {e}")
        return []

    vb = root.get("viewBox", "")
    if vb:
        parts = re.split(r"[,\s]+", vb.strip())
        vb_x,vb_y,vb_w,vb_h = (float(p) for p in parts[:4]) if len(parts)==4 else (0,0,100,100)
    else:
        sw = root.get("width","100"); sh_ = root.get("height","100")
        vb_x=vb_y=0.0
        vb_w=float(re.sub(r"[^0-9.]","",sw) or "100")
        vb_h=float(re.sub(r"[^0-9.]","",sh_) or "100")

    all_shapes = []

    def _collect(elem, inh_tf=""):
        tf = elem.get("transform", inh_tf)
        tag = elem.tag.split("}")[-1] if "}" in elem.tag else elem.tag
        if tag == "path":
            d = elem.get("d","")
            if d:
                s = _parse_svg_path_d(d)
                all_shapes.extend(_apply_svg_transform(s, tf))
        elif tag == "rect":
            x=float(elem.get("x",0)); y=float(elem.get("y",0))
            w=float(elem.get("width",0)); h=float(elem.get("height",0))
            rx=float(elem.get("rx",0))
            d=(f"M {x+rx},{y} H {x+w-rx} Q {x+w},{y} {x+w},{y+rx} "
               f"V {y+h-rx} Q {x+w},{y+h} {x+w-rx},{y+h} "
               f"H {x+rx} Q {x},{y+h} {x},{y+h-rx} V {y+rx} Q {x},{y} {x+rx},{y} Z")
            all_shapes.extend(_apply_svg_transform(_parse_svg_path_d(d), tf))
        elif tag == "circle":
            cx_=float(elem.get("cx",0)); cy_=float(elem.get("cy",0)); r=float(elem.get("r",0))
            k=0.5522847498
            d=(f"M {cx_},{cy_-r} C {cx_+r*k},{cy_-r} {cx_+r},{cy_-r*k} {cx_+r},{cy_} "
               f"C {cx_+r},{cy_+r*k} {cx_+r*k},{cy_+r} {cx_},{cy_+r} "
               f"C {cx_-r*k},{cy_+r} {cx_-r},{cy_+r*k} {cx_-r},{cy_} "
               f"C {cx_-r},{cy_-r*k} {cx_-r*k},{cy_-r} {cx_},{cy_-r} Z")
            all_shapes.extend(_apply_svg_transform(_parse_svg_path_d(d), tf))
        elif tag == "ellipse":
            ecx=float(elem.get("cx",0)); ecy=float(elem.get("cy",0))
            erx=float(elem.get("rx",0)); ery=float(elem.get("ry",0)); k=0.5522847498
            d=(f"M {ecx},{ecy-ery} C {ecx+erx*k},{ecy-ery} {ecx+erx},{ecy-ery*k} {ecx+erx},{ecy} "
               f"C {ecx+erx},{ecy+ery*k} {ecx+erx*k},{ecy+ery} {ecx},{ecy+ery} "
               f"C {ecx-erx*k},{ecy+ery} {ecx-erx},{ecy+ery*k} {ecx-erx},{ecy} "
               f"C {ecx-erx},{ecy-ery*k} {ecx-erx*k},{ecy-ery} {ecx},{ecy-ery} Z")
            all_shapes.extend(_apply_svg_transform(_parse_svg_path_d(d), tf))
        elif tag == "polygon":
            pts=elem.get("points","").strip()
            nums=re.findall(r"[-+]?[0-9]*\.?[0-9]+",pts)
            if len(nums)>=4:
                pairs=[(float(nums[i]),float(nums[i+1])) for i in range(0,len(nums)-1,2)]
                d="M "+" L ".join(f"{p[0]},{p[1]}" for p in pairs)+" Z"
                all_shapes.extend(_apply_svg_transform(_parse_svg_path_d(d), tf))
        for child in elem:
            _collect(child, tf)

    _collect(root)
    if not all_shapes:
        log.error("svg_to_lottie_shapes: no shapes parsed")
        return []

    all_verts = []
    for sh in all_shapes:
        k = sh.get("ks",{}).get("k",{})
        if isinstance(k,dict):
            all_verts.extend(k.get("v",[]))
    if not all_verts:
        return []

    xs=[v[0] for v in all_verts]; ys=[v[1] for v in all_verts]
    svg_x1,svg_x2=min(xs),max(xs); svg_y1,svg_y2=min(ys),max(ys)
    svg_w=svg_x2-svg_x1+1e-9; svg_h=svg_y2-svg_y1+1e-9

    avail_w=target_w*(1.0-padding*2); avail_h=target_h*(1.0-padding*2)
    scale=min(avail_w/svg_w, avail_h/svg_h)
    off_x=cx-(svg_x1+svg_w/2.0)*scale
    off_y=cy-(svg_y1+svg_h/2.0)*scale

    for sh in all_shapes:
        k=sh.get("ks",{}).get("k",{})
        if isinstance(k,dict):
            # Y-флип нужен: Lottie shape path data ожидает Y вверх (подтверждено старым рабочим кодом).
            # Без флипа SVG отображается невидимым (Y видимо сдвинут за пределы canvas или инвертирован).
            # evenodd (r:2) используется т..к. Y-флип меняет направление намотки путей; nonzero давал бы blob-эффект.
            k["v"]=[[pt[0]*scale+off_x, 2*cy-(pt[1]*scale+off_y)] for pt in k.get("v",[])]
            k["i"]=[[t[0]*scale, -t[1]*scale] for t in k.get("i",[])]
            k["o"]=[[t[0]*scale, -t[1]*scale] for t in k.get("o",[])]

    # Автодетекция: stroke-иконка vs fill-иконка
    svg_has_stroke = False
    svg_fill_is_none = False
    svg_stroke_width = 1.5
    svg_fill_rule = 1

    for _el in [root] + list(root.iter()):
        _f  = _el.get("fill", "")
        _s  = _el.get("stroke", "")
        _sw = _el.get("stroke-width", "")
        _fr = _el.get("fill-rule","") or _el.get("clip-rule","")
        _css = _parse_svg_style(_el.get("style", ""))
        if not _f: _f = _css.get("fill", "")
        if not _s: _s = _css.get("stroke", "")
        if not _sw: _sw = _css.get("stroke-width", "")
        if not _fr: _fr = _css.get("fill-rule", "") or _css.get("clip-rule", "")
        if _f == "none":
            svg_fill_is_none = True
        if _s and _s != "none":
            svg_has_stroke = True
            if _sw:
                try: svg_stroke_width = float(re.sub(r"[^0-9.]","",_sw))
                except: pass
        if _fr == "evenodd":
            svg_fill_rule = 2

    # Определяем цвет
    if hex_color:
        r,g,b=hex_to_rgb(hex_color)
        color_k=[r/255,g/255,b/255,1.0]
    else:
        fa=root.get("fill") or root.get("color") or "#FFFFFF"
        try:
            r,g,b=hex_to_rgb(fa) if fa not in ("none","currentColor") else (255,255,255)
            color_k=[r/255,g/255,b/255,1.0]
        except Exception:
            color_k=[1.0,1.0,1.0,1.0]

    if svg_has_stroke and svg_fill_is_none:
        lw = svg_stroke_width * scale
        lw = min(lw, target_w * 0.04)
        style_shape = {
            "ty": "st",
            "nm": "Stroke",
            "o": {"a": 0, "k": 100},
            "c": {"a": 0, "k": color_k},
            "w": {"a": 0, "k": lw},
            "lc": 2,
            "lj": 2,
        }
    else:
        style_shape = {
            "ty": "fl",
            "nm": "Fill",
            "o": {"a": 0, "k": 100},
            "c": {"a": 0, "k": color_k},
            "r": 2,
        }

    return all_shapes + [style_shape]


# ─── Replace text group with SVG ──────────────────────────────────────────────

def _add_svg_overlay_layer(lottie: dict, svg_shapes: list) -> None:
    """
    Добавляет SVG как новый shape-слой поверх всех слоёв.
    Используется как фолбэк, если _replace_textgroup не нашёл текстовую группу.
    """
    layers = lottie.setdefault("layers", [])
    op = lottie.get("op", 60)
    # tr (transform) обязателен внутри gr
    tr = {
        "ty": "tr",
        "o": {"a": 0, "k": 100},
        "r": {"a": 0, "k": 0},
        "p": {"a": 0, "k": [0, 0]},
        "a": {"a": 0, "k": [0, 0]},
        "s": {"a": 0, "k": [100, 100]},
        "sk": {"a": 0, "k": 0},
        "sa": {"a": 0, "k": 0},
    }
    new_layer = {
        "ty": 4,
        "nm": "SVGOverlay",
        "ind": max((l.get("ind", 0) for l in layers), default=0) + 1,
        "ip": 0,
        "op": op,
        "st": 0,
        "sr": 1,
        "ks": {
            "o": {"a": 0, "k": 100},
            "r": {"a": 0, "k": 0},
            "p": {"a": 0, "k": [0, 0, 0]},
            "a": {"a": 0, "k": [0, 0, 0]},
            "s": {"a": 0, "k": [100, 100, 100]},
        },
        "shapes": [
            {"ty": "gr", "nm": "SVGGroup", "it": svg_shapes + [tr]}
        ],
    }
    # Вставляем в начало — в Lottie слои с меньшим индексом рендерятся поверх
    layers.insert(0, new_layer)


def replace_textgroup_with_svg(
    tgs_bytes: bytes, svg_bytes: bytes, hex_color: Optional[str] = None
) -> bytes:
    """Находит text-группу в TGS и заменяет её SVG-путями."""
    import logging
    log = logging.getLogger("JellyColor")
    raw = gzip.decompress(tgs_bytes)
    lottie = json.loads(raw.decode("utf-8"))
    if hex_color is None:
        hex_color = get_dominant_lottie_color(lottie) or "#FFFFFF"

    canvas_w = float(lottie.get("w", 512))
    canvas_h = float(lottie.get("h", 512))
    target_w = canvas_w * 0.52
    target_h = canvas_h * 0.52

    bounds = _get_textgroup_bounds(lottie)
    if bounds:
        x1, y1, x2, y2 = bounds
        cx = (x1 + x2) / 2.0
        cy = (y1 + y2) / 2.0
    else:
        cx = canvas_w / 2.0
        cy = canvas_h / 2.0

    svg_shapes = svg_to_lottie_shapes(svg_bytes, cx, cy, target_w, target_h, hex_color)
    if not svg_shapes:
        log.error("replace_textgroup_with_svg: no shapes"); return tgs_bytes
    if not _replace_textgroup(lottie, svg_shapes):
        log.error("replace_textgroup_with_svg: _replace_textgroup failed"); return tgs_bytes
    log.info(f"replace_textgroup_with_svg: OK {len(svg_shapes)} shapes color={hex_color}")
    return compress_tgs(lottie)


# ─── fonttools helpers ────────────────────────────────────────────────────────

_FONT_SEARCH = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
    "/usr/share/fonts/truetype/freefont/FreeSansBold.ttf",
    "/usr/share/fonts/truetype/ubuntu/Ubuntu-B.ttf",
    "/usr/share/fonts/TTF/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/noto/NotoSans-Bold.ttf",
    "/usr/share/fonts/noto/NotoSans-Bold.ttf",
    "/usr/local/share/fonts/NotoSans-Bold.ttf",
]
_CACHED_FONT_PATH = "/tmp/jelly_color_font.ttf"
_FONT_CDN_URL = (
    "https://github.com/googlefonts/noto-fonts/raw/main/"
    "hinted/ttf/NotoSans/NotoSans-Bold.ttf"
)


def _find_font():
    for p in _FONT_SEARCH:
        if os.path.exists(p): return p
    for p in glob.glob("/usr/share/fonts/**/*Bold*.ttf", recursive=True): return p
    found = glob.glob("/usr/share/fonts/**/*.ttf", recursive=True)
    return found[0] if found else None


def _ensure_font():
    import logging; log = logging.getLogger("JellyColor")
    p = _find_font()
    if p: return p
    if os.path.exists(_CACHED_FONT_PATH) and os.path.getsize(_CACHED_FONT_PATH) > 50000:
        return _CACHED_FONT_PATH
    log.info("_ensure_font: downloading from CDN...")
    try:
        import urllib.request
        urllib.request.urlretrieve(_FONT_CDN_URL, _CACHED_FONT_PATH)
        if os.path.exists(_CACHED_FONT_PATH) and os.path.getsize(_CACHED_FONT_PATH) > 50000:
            return _CACHED_FONT_PATH
    except Exception as e:
        log.error(f"_ensure_font: download failed: {e}")
    return None


def _collect_path_verts(obj):
    verts = []
    def _walk(o):
        if isinstance(o, dict):
            if o.get("ty") == "sh":
                k = o.get("ks", {}).get("k", {})
                if isinstance(k, list) and k and isinstance(k[0], dict):
                    k = k[0].get("s", k[0])
                if isinstance(k, dict):
                    for v in k.get("v", []):
                        if isinstance(v, (list, tuple)) and len(v) >= 2:
                            verts.append((float(v[0]), float(v[1])))
            for val in o.values(): _walk(val)
        elif isinstance(o, list):
            for item in o: _walk(item)
    _walk(obj)
    return verts


def _verts_to_bounds(verts):
    if not verts: return None
    xs=[v[0] for v in verts]; ys=[v[1] for v in verts]
    return (min(xs), min(ys), max(xs), max(ys))


def _get_textgroup_bounds(lottie):
    def find_named(obj):
        if isinstance(obj, dict):
            if obj.get("ty")=="gr" and obj.get("nm")=="TextGroup":
                b=_verts_to_bounds(_collect_path_verts(obj))
                if b: return b
            for v in obj.values():
                r=find_named(v)
                if r: return r
        elif isinstance(obj, list):
            for item in obj:
                r=find_named(item)
                if r: return r
        return None
    b=find_named(lottie)
    if b: return b

    def find_text_layer(layers):
        for layer in layers:
            if layer.get("ty")!=4: continue
            nm=layer.get("nm",""); shapes=layer.get("shapes",[])
            n_sh=sum(1 for s in shapes if s.get("ty")=="sh")
            has_fl=any(s.get("ty")=="fl" for s in shapes)
            if ("text" in nm.lower() or "Text" in nm) and n_sh>=2 and has_fl:
                b=_verts_to_bounds(_collect_path_verts({"shapes":shapes}))
                if b: return b
        return None

    all_ll=[lottie.get("layers",[])]+[a.get("layers",[]) for a in lottie.get("assets",[])]
    for ll in all_ll:
        b=find_text_layer(ll)
        if b: return b

    def _gfl(gr): return any(x.get("ty")=="fl" for x in gr.get("it",[]))
    def _cdsh(gr): return sum(1 for x in gr.get("it",[]) if x.get("ty")=="sh")
    def _cnsh(gr):
        n=0
        for x in gr.get("it",[]):
            n+=1 if x.get("ty")=="sh" else (_cnsh(x) if x.get("ty")=="gr" else 0)
        return n

    def find_unnamed(obj):
        if isinstance(obj, dict):
            if obj.get("ty")=="gr" and _gfl(obj) and _cdsh(obj)==0 and _cnsh(obj)>=3:
                verts=_collect_path_verts(obj)
                if verts:
                    xs=[v[0] for v in verts]; ys=[v[1] for v in verts]
                    w=max(xs)-min(xs); h=max(ys)-min(ys)+1e-9
                    if w>h*1.3 or w>0: return _verts_to_bounds(verts)
            for v in obj.values():
                r=find_unnamed(v)
                if r: return r
        elif isinstance(obj, list):
            for item in obj:
                r=find_unnamed(item)
                if r: return r
        return None
    return find_unnamed(lottie)


def _text_to_lottie_shapes(text, font_path, cx, cy, height, max_width=None):
    try:
        from fontTools.ttLib import TTFont
        from fontTools.pens.recordingPen import RecordingPen
    except ImportError as e:
        import logging; logging.getLogger("JellyColor").error(f"fontTools: {e}")
        return []
    ft=TTFont(font_path); gs=ft.getGlyphSet(); cm=ft.getBestCmap() or {}
    upm=ft["head"].unitsPerEm
    os2=ft.get("OS/2")
    cap_h=float(getattr(os2,"sCapHeight",0) or getattr(os2,"sTypoAscender",upm*0.72))
    if cap_h<=0: cap_h=upm*0.72
    sc=height/cap_h
    total_adv=0.0; glyph_list=[]
    for ch in text:
        gn=cm.get(ord(ch))
        if not gn or gn not in gs:
            fb={ord("'"): [0x2019,0x02BC], ord("–"): [0x002D], ord("—"): [0x002D]}
            for alt in fb.get(ord(ch),[]):
                gn=cm.get(alt)
                if gn and gn in gs: break
            else: gn=None
        adv=float(gs[gn].width) if gn and gn in gs else upm*0.35
        glyph_list.append((gn,adv)); total_adv+=adv
    if max_width and total_adv>0:
        sc=min(sc,(max_width/(total_adv*sc)*sc)*0.92)
    start_x=cx-total_adv*sc/2.0; base_y=cy+(cap_h/2.0)*sc
    shapes=[]; cur_x=start_x
    for gn,adv in glyph_list:
        if gn is None: cur_x+=adv*sc; continue
        pen=RecordingPen(); gs[gn].draw(pen)
        vs_,ii_,oo_=[],[],[]
        def _close():
            if vs_:
                shapes.append({"ty":"sh","nm":"p","ks":{"a":0,"k":{"c":True,
                    "v":[list(v) for v in vs_],"i":[list(v) for v in ii_],"o":[list(v) for v in oo_]}}})
        for op,args in pen.value:
            if op=="moveTo":
                _close(); vs_.clear(); ii_.clear(); oo_.clear()
                fx,fy=args[0]; lx=fx*sc+cur_x; ly=base_y-fy*sc
                vs_.append([lx,ly]); ii_.append([0.,0.]); oo_.append([0.,0.])
            elif op=="lineTo":
                fx,fy=args[0]; lx=fx*sc+cur_x; ly=base_y-fy*sc
                vs_.append([lx,ly]); ii_.append([0.,0.]); oo_.append([0.,0.])
            elif op=="curveTo":
                (c1x,c1y),(c2x,c2y),(ex,ey)=args
                pvx,pvy=vs_[-1]
                oo_[-1]=[c1x*sc+cur_x-pvx,base_y-c1y*sc-pvy]
                nvx=ex*sc+cur_x; nvy=base_y-ey*sc
                vs_.append([nvx,nvy]); ii_.append([c2x*sc+cur_x-nvx,base_y-c2y*sc-nvy]); oo_.append([0.,0.])
            elif op=="qCurveTo":
                pts=list(args); p0x,p0y=vs_[-1]
                for qi in range(len(pts)-1):
                    qcx,qcy=pts[qi]
                    qex,qey=pts[qi+1] if qi==len(pts)-2 else ((pts[qi][0]+pts[qi+1][0])/2,(pts[qi][1]+pts[qi+1][1])/2)
                    qcs=(qcx*sc+cur_x,base_y-qcy*sc); qes=(qex*sc+cur_x,base_y-qey*sc)
                    c1s=(p0x+2/3*(qcs[0]-p0x),p0y+2/3*(qcs[1]-p0y))
                    c2s=(qes[0]+2/3*(qcs[0]-qes[0]),qes[1]+2/3*(qcs[1]-qes[1]))
                    oo_[-1]=[c1s[0]-p0x,c1s[1]-p0y]
                    vs_.append(list(qes)); ii_.append([c2s[0]-qes[0],c2s[1]-qes[1]]); oo_.append([0.,0.])
                    p0x,p0y=qes
            elif op in ("endPath","closePath"):
                _close(); vs_.clear(); ii_.clear(); oo_.clear()
        _close(); cur_x+=adv*sc
    return shapes


def _replace_textgroup(lottie, new_shapes):
    def _hfl(items): return any(x.get("ty")=="fl" for x in items)
    def _islc(item):
        if item.get("ty")!="gr": return False
        return not _hfl(item.get("it",[])) and not any(x.get("ty")=="st" for x in item.get("it",[]))
    def _patch(lst):
        style=[x for x in lst if x.get("ty") not in ("sh","el","rc","sr") and not _islc(x)]
        lst[:]=new_shapes+style; return True
    def walk_gr(obj):
        if isinstance(obj,dict):
            if obj.get("ty")=="gr" and obj.get("nm")=="TextGroup": return _patch(obj.setdefault("it",[]))
            for v in obj.values():
                if walk_gr(v): return True
        elif isinstance(obj,list):
            for item in obj:
                if walk_gr(item): return True
        return False
    if walk_gr(lottie): return True
    def try_ll(layers):
        for layer in layers:
            if layer.get("ty")!=4: continue
            shapes=layer.get("shapes",[]); nm=layer.get("nm","")
            n=sum(1 for s in shapes if s.get("ty")=="sh")
            fl=any(s.get("ty")=="fl" for s in shapes)
            if ("text" in nm.lower() and n>=2 and fl) or (n>=3 and fl): return _patch(shapes)
        return False
    for ll in [lottie.get("layers",[])]+[a.get("layers",[]) for a in lottie.get("assets",[])]:  
        if try_ll(ll): return True
    def _cnsh(gr):
        n=0
        for x in gr.get("it",[]):
            n+=1 if x.get("ty")=="sh" else (_cnsh(x) if x.get("ty")=="gr" else 0)
        return n
    def walk_un(obj):
        if isinstance(obj,dict):
            if obj.get("ty")=="gr":
                items=obj.get("it",[])
                if _hfl(items) and (any(_islc(x) for x in items) or not any(x.get("ty")=="sh" for x in items)):
                    if _cnsh({"it":items})>=3: return _patch(items)
            for v in obj.values():
                if walk_un(v): return True
        elif isinstance(obj,list):
            for item in obj:
                if walk_un(item): return True
        return False
    return walk_un(lottie)


def _find_username_bounds(lottie):
    def walk(obj):
        if isinstance(obj,dict):
            if obj.get("ty")=="gr" and obj.get("nm")=="USERNAME":
                b=_verts_to_bounds(_collect_path_verts(obj))
                if b: return b,obj
            for v in obj.values():
                r=walk(v)
                if r: return r
        elif isinstance(obj,list):
            for item in obj:
                r=walk(item)
                if r: return r
        return None
    return walk(lottie)


def _replace_username(lottie, new_text, font_path):
    res=_find_username_bounds(lottie)
    if not res: return False
    bounds,grp=res; x1,y1,x2,y2=bounds
    ns=_text_to_lottie_shapes(new_text,font_path,(x1+x2)/2,(y1+y2)/2,
                               max(abs(y2-y1),1.0),max_width=max(abs(x2-x1),1.0))
    if not ns: return False
    items=grp.setdefault("it",[])
    def _hfl(lst): return any(x.get("ty")=="fl" for x in lst)
    style=[x for x in items if x.get("ty") not in ("sh","el","rc","sr")
           and not (x.get("ty")=="gr" and not _hfl(x.get("it",[])))]
    items[:]=ns+style; return True


OLD_USERNAME = "@emojicreationbot"
NEW_USERNAME = "@freecreateemoji"


def replace_text_in_tgs(tgs_bytes: bytes, old_text: str, new_text: str) -> bytes:
    raw=gzip.decompress(tgs_bytes); lottie=json.loads(raw.decode("utf-8"))
    font_path=_ensure_font()
    if not font_path: return tgs_bytes
    changed=False
    bounds=_get_textgroup_bounds(lottie)
    if bounds:
        x1,y1,x2,y2=bounds; cx=(x1+x2)/2; cy=(y1+y2)/2
        ns=_text_to_lottie_shapes(new_text,font_path,cx,cy,max(abs(y2-y1),5.),max_width=max(abs(x2-x1),5.))
        if ns and _replace_textgroup(lottie,ns): changed=True
    if _find_username_bounds(lottie):
        if _replace_username(lottie,NEW_USERNAME,font_path): changed=True
    if not changed: return tgs_bytes
    return compress_tgs(lottie)


# ─── Recolor helpers ──────────────────────────────────────────────────────────

async def recolor_document(client, doc, hex_color: str) -> io.BytesIO:
    data=await download_cached(client,doc)
    mime=getattr(doc,"mime_type","")
    if mime=="application/x-tgsticker":
        lottie=json.loads(gzip.decompress(data))
        buf=io.BytesIO(compress_tgs(tint_lottie(lottie,hex_color))); buf.name="sticker.tgs"
    else:
        img=Image.open(io.BytesIO(data)).convert("RGBA").resize((512,512),Image.LANCZOS)
        buf=io.BytesIO(); tint_image(img,hex_color).save(buf,format="WEBP",lossless=True)
        buf.seek(0); buf.name="sticker.webp"
    buf.seek(0); return buf


async def recolor_document_gradient(client, doc, gradient: dict) -> io.BytesIO:
    data=await download_cached(client,doc)
    mime=getattr(doc,"mime_type","")
    if mime=="application/x-tgsticker":
        lottie=json.loads(gzip.decompress(data))
        apply_gradient_lottie(lottie,gradient)
        buf=io.BytesIO(compress_tgs(lottie)); buf.name="sticker.tgs"
    else:
        mid = gradient["colors"][len(gradient["colors"])//2]
        img=Image.open(io.BytesIO(data)).convert("RGBA").resize((512,512),Image.LANCZOS)
        buf=io.BytesIO(); tint_image(img,mid).save(buf,format="WEBP",lossless=True)
        buf.seek(0); buf.name="sticker.webp"
    buf.seek(0); return buf


async def recolor_document_svg(client, doc, svg_bytes: bytes, hex_color: Optional[str]) -> Optional[io.BytesIO]:
    data=await download_cached(client,doc)
    if getattr(doc,"mime_type","")!="application/x-tgsticker": return None
    result=replace_textgroup_with_svg(data,svg_bytes,hex_color)
    if result==data: return None
    buf=io.BytesIO(result); buf.name="sticker.tgs"; buf.seek(0)
    return buf


def validate_short_name(name: str) -> bool:
    return bool(re.fullmatch(r"[a-z0-9_]{1,64}",name))


async def _upload_item(client, me_entity, uploaded, mime: str, emoji_str: str, is_emoji: bool):
    if is_emoji:
        attr=types.DocumentAttributeCustomEmoji(alt=emoji_str,stickerset=types.InputStickerSetEmpty(),free=False,text_color=False)
    else:
        attr=types.DocumentAttributeSticker(alt=emoji_str,stickerset=types.InputStickerSetEmpty())
    mt="application/x-tgsticker" if mime=="application/x-tgsticker" else "image/webp"
    fn="sticker.tgs" if mime=="application/x-tgsticker" else "sticker.webp"
    media=types.InputMediaUploadedDocument(
        file=uploaded,mime_type=mt,
        attributes=[types.DocumentAttributeFilename(file_name=fn),attr],
    )
    r=await client(functions.messages.UploadMediaRequest(peer=me_entity,media=media))
    d=r.document
    return types.InputStickerSetItem(
        document=types.InputDocument(id=d.id,access_hash=d.access_hash,file_reference=d.file_reference),
        emoji=emoji_str,
    )


async def _safe_create_set(client, uid, title, short_name, stickers, is_emoji, retries=3):
    for i in range(retries):
        sn=short_name if i==0 else f"{short_name}_v{i+1}"
        try:
            await client(functions.stickers.CreateStickerSetRequest(
                user_id=uid,title=title,short_name=sn,stickers=stickers,emojis=is_emoji,
            ))
            return sn,None
        except Exception as e:
            if "SHORT_NAME_OCCUPIED" in str(e) or "STICKERSET_INVALID" in str(e): continue
            return None,str(e)
    return None,"SHORT_NAME_OCCUPIED"


# ─── Module ───────────────────────────────────────────────────────────────────

@loader.tds
class JellyColorMod(loader.Module):
    """Перекраска + SVG-вставка + текстовые шаблоны
    .j .jc .jpreview .jt .jsv .tstats .jdel .jexport .jdump"""

    strings = {"name": "JellyColor"}

    def __init__(self):
        self._sessions:     Dict[int,Dict[str,Any]] = {}
        self._tsessions:    Dict[int,Dict[str,Any]] = {}
        self._svg_sessions: Dict[int,Dict[str,Any]] = {}
        self._svg_pending:  Dict[int,Dict[str,Any]] = {}
        self._semaphore = None

    def _sem(self):
        if self._semaphore is None:
            self._semaphore=asyncio.Semaphore(RECOLOR_CONCURRENCY)
        return self._semaphore

    def _expire(self):
        now=time.time()
        for store in (self._sessions,self._tsessions,self._svg_sessions,self._svg_pending):
            for k in [k for k,v in store.items() if now-v.get("ts",now)>SESSION_TTL]:
                store.pop(k,None)

    def _color_history(self) -> List[str]:
        seen=[]; out=[]
        for e in reversed(self.db.get("JellyColor","stats",[])):
            c=e.get("color","")
            if c and c!="text" and not c.startswith("svg:") and c not in seen:
                seen.append(c); out.append(c)
            if len(out)>=5: break
        return out

    # ─── Градиентное меню (универсальные хелперы) ───────────────────────────

    def _gradient_menu_text(self):
        lines=[pe("🎨",PE["palette"])+" <b>Меню градиентов</b>\n"]
        for g in GRADIENT_PRESETS:
            c1,c2=g["colors"][0],g["colors"][-1]
            lines.append(f"• {g['name']}  <code>{c1}</code>→<code>{c2}</code>")
        return "\n".join(lines)

    def _gradient_menu_markup(self,cb,uid,back_cb):
        rows=[]; row=[]
        for g in GRADIENT_PRESETS:
            row.append({"text":g["name"],"callback":cb,"args":(uid,g["id"])})
            if len(row)==2: rows.append(row); row=[]
        if row: rows.append(row)
        rows.append([{"text":"◁ Назад","icon_custom_emoji_id":PE["brush"],"callback":back_cb,"args":(uid,)}])
        return rows

    # ─── .j ───────────────────────────────────────────────────────────────────

    @loader.command()
    async def j(self, message: Message):
        """Ответьте на стикер/эмодзи — перекраска с выбором цвета"""
        self._expire()
        reply=await message.get_reply_message()
        if not reply: await utils.answer(message,pe("❌",PE["err"])+" Ответьте на стикер или эмодзи."); return
        td,tt,ts=await self._resolve_target(reply)
        if not td: await utils.answer(message,pe("❌",PE["err"])+" Стикер/эмодзи не найден."); return
        try: full_set=await self._client(functions.messages.GetStickerSetRequest(stickerset=ts,hash=0))
        except Exception as e: await utils.answer(message,pe("❌",PE["err"])+" "+str(e)); return
        uid=message.sender_id; pc=len(full_set.documents)
        self._sessions[uid]={"ts":time.time(),"type":tt,"doc":td,"set_id":ts,
            "set_short":getattr(full_set.set,"short_name",""),"full_set":full_set,"pack_count":pc,
            "scope":None,"color":None,"gradient":None,"pack_name":None,
            "step":"scope" if pc>1 else "color"}
        await message.delete()
        await self.inline.form(text=self._j_text(uid),reply_markup=self._j_markup(uid),message=message)

    def _j_text(self,uid):
        s=self._sessions[uid]; step=s["step"]
        if step=="scope": return pe("🖤",PE["brush"])+f" <b>Что перекрасить?</b>\n\nПак <code>{s['set_short']}</code> — <b>{s['pack_count']}</b> шт."
        if step=="color":
            hist=self._color_history()
            hs=("\n"+pe("⏰",PE["clock"])+" Последние: "+"  ".join(f"<code>{c}</code>" for c in hist)) if hist else ""
            sc="один" if s["scope"]=="one" else f"весь пак ({s['pack_count']})"
            return pe("🖋",PE["palette"])+f" <b>Цвет</b> — {sc}{hs}"
        if step=="gradient_menu": return self._gradient_menu_text()
        if step=="name":
            g=s.get("gradient")
            label=g["name"] if g else f"<code>{s['color']}</code>"
            return pe("🏷",PE["sticker"])+f" <b>Название пака</b>\n\nЦвет: {label}"
        return pe("⏰",PE["clock"])+" <b>Перекрашиваю...</b>"

    def _j_markup(self,uid):
        s=self._sessions[uid]; step=s["step"]
        if step=="scope": return [[
            {"text":"Один","icon_custom_emoji_id":PE["sticker"],"callback":self._j_s1,"args":(uid,)},
            {"text":"Весь пак","icon_custom_emoji_id":PE["pack"],"callback":self._j_sa,"args":(uid,)},
        ]]
        if step in ("color","gradient_menu"):
            if step=="gradient_menu":
                return self._gradient_menu_markup(self._j_grad,uid,self._j_back_col)
            # ── Инлайн цветовых рядов (ИСПРАВЛЕНИЕ бага 1) ──
            hist=self._color_history()
            rows=[]; row=[]
            for label,hv in PRESET_COLORS.items():
                row.append({"text":label,"callback":self._j_col,"args":(uid,hv)})
                if len(row)==2: rows.append(row); row=[]
            if row: rows.append(row)
            if hist: rows.append([{"text":c,"callback":self._j_col,"args":(uid,c)} for c in hist[:3]])
            rows.append([{"text":"🌈 Пикер","icon_custom_emoji_id":PE["link"],"url":"https://get-color.ru/"},
                         {"text":"✏️ HEX","icon_custom_emoji_id":PE["palette"],
                          "input":"Введите HEX","handler":self._j_hex,"args":(uid,)}])
            rows.append([{"text":"🎨 Меню градиента","icon_custom_emoji_id":PE["stats"],
                          "callback":self._j_open_grad,"args":(uid,)}])
            return rows
        if step=="name": return [[{"text":"Ввести название","icon_custom_emoji_id":PE["palette"],
                                   "input":"short_name (a-z,0-9,_)","handler":self._j_name,"args":(uid,)}]]
        return []

    async def _j_s1(self,call,uid):
        s=self._sessions.get(uid)
        if not s: await call.answer("Сессия устарела.",show_alert=True); return
        s["scope"]="one"; s["step"]="color"
        await call.edit(text=self._j_text(uid),reply_markup=self._j_markup(uid))

    async def _j_sa(self,call,uid):
        s=self._sessions.get(uid)
        if not s: await call.answer("Сессия устарела.",show_alert=True); return
        s["scope"]="all"; s["step"]="color"
        await call.edit(text=self._j_text(uid),reply_markup=self._j_markup(uid))

    async def _j_col(self,call,uid,hex_color):
        s=self._sessions.get(uid)
        if not s: await call.answer("Сессия устарела.",show_alert=True); return
        s["color"]=hex_color; s["gradient"]=None; s["step"]="name"
        await call.edit(text=self._j_text(uid),reply_markup=self._j_markup(uid))

    async def _j_hex(self,call,value,uid):
        s=self._sessions.get(uid)
        if not s: await call.answer("Сессия устарела.",show_alert=True); return
        c=value.strip()
        if not c.startswith("#"): c="#"+c
        if not re.fullmatch(r"#[0-9a-fA-F]{6}",c): await call.answer("Неверный HEX.",show_alert=True); return
        s["color"]=c.upper(); s["gradient"]=None; s["step"]="name"
        await call.edit(text=self._j_text(uid),reply_markup=self._j_markup(uid))

    async def _j_open_grad(self,call,uid):
        s=self._sessions.get(uid)
        if not s: await call.answer("Сессия устарела.",show_alert=True); return
        s["step"]="gradient_menu"
        await call.edit(text=self._j_text(uid),reply_markup=self._j_markup(uid))

    async def _j_grad(self,call,uid,grad_id):
        s=self._sessions.get(uid)
        if not s: await call.answer("Сессия устарела.",show_alert=True); return
        g=next((x for x in GRADIENT_PRESETS if x["id"]==grad_id),None)
        if not g: return
        s["gradient"]=g; s["color"]="grad:"+g["name"]; s["step"]="name"
        await call.edit(text=self._j_text(uid),reply_markup=self._j_markup(uid))

    async def _j_back_col(self,call,uid):
        s=self._sessions.get(uid)
        if not s: await call.answer("Сессия устарела.",show_alert=True); return
        s["step"]="color"
        await call.edit(text=self._j_text(uid),reply_markup=self._j_markup(uid))

    async def _j_name(self,call,value,uid):
        s=self._sessions.get(uid)
        if not s: await call.answer("Сессия устарела.",show_alert=True); return
        if s.get("step")=="processing": await call.answer("Уже идёт.",show_alert=True); return
        c=value.strip().lower()
        if not validate_short_name(c): await call.answer("Только a-z,0-9,_",show_alert=True); return
        me=await self._client.get_me()
        s["pack_name"]=c+"_by_"+(me.username or "userbot")
        s["step"]="processing"
        await call.edit(text=self._j_text(uid))
        asyncio.ensure_future(self._j_run(call,uid))

    async def _j_run(self,call,uid):
        s=self._sessions[uid]
        color=s["color"]; pname=s["pack_name"]; ptype=s["type"]
        gradient=s.get("gradient")
        docs=[s["doc"]] if (s["scope"]=="one" or s["pack_count"]==1) else list(s["full_set"].documents)
        me=await self._client.get_me(); mee=await self._client.get_input_entity("me")
        async def _fn(i,doc):
            if gradient:
                buf=await recolor_document_gradient(self._client,doc,gradient)
            else:
                buf=await recolor_document(self._client,doc,color)
            mime=getattr(doc,"mime_type","image/webp")
            es="🎨"
            for a in doc.attributes:
                if isinstance(a,(DocumentAttributeCustomEmoji,DocumentAttributeSticker)):
                    es=getattr(a,"alt",None) or "🎨"; break
            up=await self._client.upload_file(buf,file_name=buf.name)
            return await _upload_item(self._client,mee,up,mime,es,ptype=="emoji")
        ordered=await self._parallel(docs,_fn,"Перекраска",call)
        try:
            if not ordered: raise ValueError("Нет стикеров")
            title="JellyColor "+(gradient["name"] if gradient else color)
            fn,err=await _safe_create_set(self._client,me.id,title,pname,ordered,ptype=="emoji")
            if err: raise ValueError(err)
            link="https://t.me/"+("addemoji/" if ptype=="emoji" else "addstickers/")+fn
        except Exception as e:
            await call.edit(text=pe("❌",PE["err"])+" <code>"+str(e)+"</code>")
            self._sessions.pop(uid,None); return
        stats=self.db.get("JellyColor","stats",[])
        clabel=gradient["name"] if gradient else color
        stats.append({"name":fn,"link":link,"color":clabel,"count":len(ordered),"type":ptype,"ts":int(time.time())})
        self.db.set("JellyColor","stats",stats)
        tl="Стикерпак" if ptype=="sticker" else "Эмодзи-пак"
        tag=gradient["name"] if gradient else f"<code>{color}</code>"
        await call.edit(
            text=(pe("✅",PE["ok"])+" <b>Готово!</b>\n\n"
                  +pe("🖤",PE["brush"])+f" {tl} → <code>{color}</code>\n"
                  +pe("📦",PE["pack"])+f" <b>{len(ordered)}</b> шт.\n\n"
                  +pe("🔗",PE["link"])+f" <a href=\"{link}\">{link}</a>"),
            reply_markup=[[{"text":"Открыть","icon_custom_emoji_id":PE["link"],"url":link}]],
        )
        self._sessions.pop(uid,None)

    # ─── .jc ────────────────────────────────────────────────────────────

    @loader.command()
    async def jc(self, message: Message):
        """Быстрая перекраска с созданием пака из 1 эмодзи: .jc #HEX (ответьте на эмодзи/стикер)"""
        reply=await message.get_reply_message()
        args=utils.get_args_raw(message).strip()
        if not reply or not args:
            await utils.answer(message,pe("ℹ️",PE["info"])+" Ответьте на эмодзи и напишите <code>.jc #FF3B30</code>"); return
        hc=args if args.startswith("#") else "#"+args
        if not re.fullmatch(r"#[0-9a-fA-F]{6}",hc): await utils.answer(message,pe("❌",PE["err"])+" Неверный HEX"); return
        td,tt,_=await self._resolve_target(reply)
        if not td: await utils.answer(message,pe("❌",PE["err"])+" Эмодзи/стикер не найден."); return
        msg=await utils.answer(message,pe("⏰",PE["clock"])+" Создаю...")
        try:
            buf=await recolor_document(self._client,td,hc)
            me=await self._client.get_me(); mee=await self._client.get_input_entity("me")
            mime=getattr(td,"mime_type","image/webp")
            es="🎨"
            for a in td.attributes:
                if isinstance(a,(DocumentAttributeCustomEmoji,DocumentAttributeSticker)):
                    es=getattr(a,"alt",None) or "🎨"; break
            uploaded=await self._client.upload_file(buf,file_name=buf.name)
            is_emoji=(tt=="emoji")
            item=await _upload_item(self._client,mee,uploaded,mime,es,is_emoji)
            sn="jc"+hc[1:].lower()+"_by_"+(me.username or "userbot")
            final_name,err=await _safe_create_set(self._client,me.id,"JellyColor "+hc,sn,[item],is_emoji)
            if err: raise ValueError(err)
            link="https://t.me/"+("addemoji/" if is_emoji else "addstickers/")+final_name
            await msg.edit(pe("✅",PE["ok"])+f" Готово!\n\n"+pe("🔗",PE["link"])+f" <a href=\"{link}\">{link}</a>")
        except Exception as e: await msg.edit(pe("❌",PE["err"])+" <code>"+str(e)+"</code>")

    # ─── .jsv — SVG-вставка ──────────────────────────────────────────────────

    @loader.command()
    async def jsv(self, message: Message):
        """Ответьте на эмодзи — заменить текст вашим SVG (адаптируется под цвет эмодзи)"""
        self._expire()
        reply=await message.get_reply_message()
        if not reply: await utils.answer(message,pe("❌",PE["err"])+" Ответьте на стикер или эмодзи."); return
        td,tt,ts=await self._resolve_target(reply)
        if not td: await utils.answer(message,pe("❌",PE["err"])+" Стикер/эмодзи не найден."); return
        try: full_set=await self._client(functions.messages.GetStickerSetRequest(stickerset=ts,hash=0))
        except Exception as e: await utils.answer(message,pe("❌",PE["err"])+" "+str(e)); return
        uid=message.sender_id; pc=len(full_set.documents)
        self._svg_sessions[uid]={"ts":time.time(),"type":tt,"doc":td,"set_id":ts,
            "set_short":getattr(full_set.set,"short_name",""),"full_set":full_set,"pack_count":pc,
            "scope":None,"hex_color":None,"pack_name":None,"svg_bytes":None,
            "step":"scope" if pc>1 else "svg_wait"}
        await message.delete()
        await self.inline.form(text=self._sv_text(uid),reply_markup=self._sv_markup(uid),message=message)

    def _sv_text(self,uid):
        s=self._svg_sessions[uid]; step=s["step"]
        if step=="scope": return pe("🖤",PE["brush"])+f" <b>Куда вставить SVG?</b>\n\nПак <code>{s['set_short']}</code> — <b>{s['pack_count']}</b> шт."
        if step=="svg_wait": return (
            pe("🖼",PE["media"])+" <b>Отправьте SVG-файл</b>\n\n"
            "Следующим сообщением отправьте <code>.svg</code> документ.\n"
            "SVG встанет вместо текста и адаптируется под цвет эмодзи.\n"
            +pe("ℹ️",PE["info"])+" Можно добавить HEX в подписи к файлу: <code>#FF3B30</code>")
        if step=="color": return (pe("🎨",PE["palette"])+" <b>Цвет SVG</b>\n\nВыберите цвет или оставьте авто.")
        if step=="gradient_menu": return self._gradient_menu_text()
        if step=="svg_grad_q":
            g=self._svg_sessions[uid].get("gradient",{})
            return (pe("🎨",PE["palette"])+f" <b>Красить SVG цветом градиента?</b>\n\n"
                    f"Градиент: {g.get('name','')}\n\n"
                    "✅ <b>Да</b> — SVG получит первый цвет градиента\n"
                    "❌ <b>Нет</b> — SVG адаптируется под цвет эмодзи")
        if step=="name": return pe("🏷",PE["sticker"])+" <b>Название пака</b>\n\na-z, 0-9, _"
        return pe("⏰",PE["clock"])+" <b>Создаём SVG-пак...</b>"

    def _sv_markup(self,uid):
        s=self._svg_sessions[uid]; step=s["step"]
        if step=="scope": return [[
            {"text":"Один","icon_custom_emoji_id":PE["sticker"],"callback":self._sv_s1,"args":(uid,)},
            {"text":"Весь пак","icon_custom_emoji_id":PE["pack"],"callback":self._sv_sa,"args":(uid,)},
        ]]
        if step=="svg_wait": return [[{"text":"❌ Отмена","icon_custom_emoji_id":PE["err"],"callback":self._sv_cancel,"args":(uid,)}]]
        if step=="color":
            rows=[]; row=[]
            for label,hv in list(PRESET_COLORS.items())[:8]:
                row.append({"text":label,"callback":self._sv_col,"args":(uid,hv)})
                if len(row)==2: rows.append(row); row=[]
            if row: rows.append(row)
            rows.append([{"text":"🤖 Авто","icon_custom_emoji_id":PE["eye"],"callback":self._sv_col,"args":(uid,None)}])
            rows.append([{"text":"✏️ HEX","icon_custom_emoji_id":PE["palette"],
                          "input":"Введите HEX","handler":self._sv_hex,"args":(uid,)}])
            rows.append([{"text":"🎨 Меню градиента","icon_custom_emoji_id":PE["stats"],
                          "callback":self._sv_open_grad,"args":(uid,)}])
            return rows
        if step=="gradient_menu":
            return self._gradient_menu_markup(self._sv_grad,uid,self._sv_back_col)
        if step=="svg_grad_q": return [[
            {"text":"✅ Да, красить SVG","icon_custom_emoji_id":PE["ok"],"callback":self._sv_grad_yes,"args":(uid,)},
            {"text":"❌ Нет","icon_custom_emoji_id":PE["err"],"callback":self._sv_grad_no,"args":(uid,)},
        ]]
        if step=="name": return [[{"text":"Ввести название","icon_custom_emoji_id":PE["palette"],
                                   "input":"short_name (a-z,0-9,_)","handler":self._sv_name,"args":(uid,)}]]
        return []

    async def _sv_s1(self,call,uid):
        s=self._svg_sessions.get(uid)
        if not s: await call.answer("Сессия устарела.",show_alert=True); return
        s["scope"]="one"; s["step"]="svg_wait"
        self._svg_pending[uid]={"call":call,"ts":time.time()}
        await call.edit(text=self._sv_text(uid),reply_markup=self._sv_markup(uid))

    async def _sv_sa(self,call,uid):
        s=self._svg_sessions.get(uid)
        if not s: await call.answer("Сессия устарела.",show_alert=True); return
        s["scope"]="all"; s["step"]="svg_wait"
        self._svg_pending[uid]={"call":call,"ts":time.time()}
        await call.edit(text=self._sv_text(uid),reply_markup=self._sv_markup(uid))

    async def _sv_cancel(self,call,uid):
        self._svg_sessions.pop(uid,None); self._svg_pending.pop(uid,None)
        await call.edit(text=pe("✅",PE["ok"])+" Отменено.")

    async def _sv_col(self,call,uid,hc):
        s=self._svg_sessions.get(uid)
        if not s: await call.answer("Сессия устарела.",show_alert=True); return
        s["hex_color"]=hc; s["gradient"]=None; s["step"]="name"
        await call.edit(text=self._sv_text(uid),reply_markup=self._sv_markup(uid))

    async def _sv_hex(self,call,value,uid):
        s=self._svg_sessions.get(uid)
        if not s: await call.answer("Сессия устарела.",show_alert=True); return
        c=value.strip()
        if not c.startswith("#"): c="#"+c
        if not re.fullmatch(r"#[0-9a-fA-F]{6}",c): await call.answer("Неверный HEX.",show_alert=True); return
        s["hex_color"]=c.upper(); s["gradient"]=None; s["step"]="name"
        await call.edit(text=self._sv_text(uid),reply_markup=self._sv_markup(uid))

    async def _sv_open_grad(self,call,uid):
        s=self._svg_sessions.get(uid)
        if not s: await call.answer("Сессия устарела.",show_alert=True); return
        s["step"]="gradient_menu"
        await call.edit(text=self._sv_text(uid),reply_markup=self._sv_markup(uid))

    async def _sv_grad(self,call,uid,grad_id):
        s=self._svg_sessions.get(uid)
        if not s: await call.answer("Сессия устарела.",show_alert=True); return
        g=next((x for x in GRADIENT_PRESETS if x["id"]==grad_id),None)
        if not g: return
        s["gradient"]=g; s["step"]="svg_grad_q"
        await call.edit(text=self._sv_text(uid),reply_markup=self._sv_markup(uid))

    async def _sv_back_col(self,call,uid):
        s=self._svg_sessions.get(uid)
        if not s: await call.answer("Сессия устарела.",show_alert=True); return
        s["step"]="color"
        await call.edit(text=self._sv_text(uid),reply_markup=self._sv_markup(uid))

    async def _sv_grad_yes(self,call,uid):
        s=self._svg_sessions.get(uid)
        if not s: await call.answer("Сессия устарела.",show_alert=True); return
        g=s.get("gradient",{})
        s["hex_color"]=g.get("colors",["#FFFFFF"])[0]
        s["step"]="name"
        await call.edit(text=self._sv_text(uid),reply_markup=self._sv_markup(uid))

    async def _sv_grad_no(self,call,uid):
        s=self._svg_sessions.get(uid)
        if not s: await call.answer("Сессия устарела.",show_alert=True); return
        s["hex_color"]=None
        s["step"]="name"
        await call.edit(text=self._sv_text(uid),reply_markup=self._sv_markup(uid))

    async def _sv_name(self,call,value,uid):
        s=self._svg_sessions.get(uid)
        if not s: await call.answer("Сессия устарела.",show_alert=True); return
        c=value.strip().lower()
        if not validate_short_name(c): await call.answer("Только a-z,0-9,_",show_alert=True); return
        me=await self._client.get_me()
        s["pack_name"]=c+"_by_"+(me.username or "userbot")
        s["step"]="processing"
        await call.edit(text=self._sv_text(uid))
        asyncio.ensure_future(self._sv_run(call,uid))

    @loader.watcher()
    async def _svg_file_watcher(self, message: Message):
        uid=message.sender_id
        if uid not in self._svg_pending: return
        me=await self._client.get_me()
        if message.sender_id!=me.id: return
        doc=message.document
        if not doc: return
        fname=""
        for a in doc.attributes:
            if hasattr(a,"file_name"): fname=a.file_name or ""; break
        mime_ok=getattr(doc,"mime_type","") in ("image/svg+xml","text/xml","application/xml")
        if not fname.lower().endswith(".svg") and not mime_ok: return
        pending=self._svg_pending.pop(uid,None)
        s=self._svg_sessions.get(uid)
        if not s or not pending: return
        s["svg_bytes"]=await self._client.download_media(doc,bytes)
        caption=(message.text or message.message or "").strip()
        m=re.search(r"#[0-9a-fA-F]{6}",caption)
        if m: s["hex_color"]=m.group(0).upper(); s["step"]="name"
        else: s["step"]="color"
        await pending["call"].edit(text=self._sv_text(uid),reply_markup=self._sv_markup(uid))
        await message.delete()

    async def _sv_run(self,call,uid):
        s=self._svg_sessions[uid]
        svg_bytes,hc,pname,ptype=s["svg_bytes"],s["hex_color"],s["pack_name"],s["type"]
        gradient=s.get("gradient")
        docs=[s["doc"]] if (s["scope"]=="one" or s["pack_count"]==1) else list(s["full_set"].documents)
        me=await self._client.get_me(); mee=await self._client.get_input_entity("me")
        skipped=[0]
        async def _fn(i,doc):
            orig_data=await download_cached(self._client,doc)
            mime=getattr(doc,"mime_type","")
            if mime!="application/x-tgsticker": skipped[0]+=1; return None
            svg_color = hc
            if svg_color is None:
                try:
                    _orig=json.loads(gzip.decompress(orig_data))
                    svg_color=get_dominant_lottie_color(_orig) or "#FFFFFF"
                except Exception: svg_color="#FFFFFF"
            data = orig_data
            if gradient:
                try:
                    _lt=json.loads(gzip.decompress(data))
                    apply_gradient_lottie(_lt,gradient)
                    data=compress_tgs(_lt)
                except Exception: pass
            result=replace_textgroup_with_svg(data,svg_bytes,svg_color)
            if result==data: skipped[0]+=1; return None
            buf=io.BytesIO(result); buf.name="sticker.tgs"
            mime=getattr(doc,"mime_type","application/x-tgsticker")
            es="✨"
            for a in doc.attributes:
                if isinstance(a,(DocumentAttributeCustomEmoji,DocumentAttributeSticker)):
                    es=getattr(a,"alt",None) or "✨"; break
            up=await self._client.upload_file(buf,file_name=buf.name)
            return await _upload_item(self._client,mee,up,mime,es,ptype=="emoji")
        ordered=await self._parallel(docs,_fn,"Создаём SVG-пак",call)
        ordered=[x for x in ordered if x is not None]
        if not ordered:
            await call.edit(text=pe("❌",PE["err"])+" Ни один стикер не удалось. SVG работает только с TGS.")
            self._svg_sessions.pop(uid,None); return
        try:
            fn,err=await _safe_create_set(self._client,me.id,"JellySVG Pack",pname,ordered,ptype=="emoji")
            if err: raise ValueError(err)
            link="https://t.me/"+("addemoji/" if ptype=="emoji" else "addstickers/")+fn
        except Exception as e:
            await call.edit(text=pe("❌",PE["err"])+" <code>"+str(e)+"</code>")
            self._svg_sessions.pop(uid,None); return
        stats=self.db.get("JellyColor","stats",[])
        stats.append({"name":fn,"link":link,"color":"svg:"+(hc or "auto"),
                      "count":len(ordered),"type":ptype,"ts":int(time.time())})
        self.db.set("JellyColor","stats",stats)
        sk=f"\n{pe('ℹ️',PE['info'])} Пропущено (not TGS): <b>{skipped[0]}</b>" if skipped[0] else ""
        await call.edit(
            text=(pe("✅",PE["ok"])+" <b>SVG-пак готов!</b>\n\n"
                  +pe("🖼",PE["media"])+f" SVG в <b>{len(ordered)}</b> эмодзи\n"
                  +pe("🎨",PE["palette"])+f" Цвет: <code>{hc or 'авто'}</code>"+sk+"\n\n"
                  +pe("🔗",PE["link"])+f" <a href=\"{link}\">{link}</a>"),
            reply_markup=[[{"text":"Открыть","icon_custom_emoji_id":PE["link"],"url":link}]],
        )
        self._svg_sessions.pop(uid,None)

    # ─── .jt — текстовые шаблоны ────────────────────────────────────────────────

    @loader.command()
    async def jt(self, message: Message):
        """Создать эмодзи-пак из шаблона с вашим текстом + выбор цвета"""
        self._expire()
        uid=message.sender_id
        self._tsessions[uid]={"ts":time.time(),"step":"template","template":None,"text":None,
                               "color":None,"pack_name":None,"preview_msg":None}
        await message.delete()
        await self.inline.form(text=self._jt_text(uid),reply_markup=self._jt_markup(uid),message=message)

    def _jt_text(self,uid):
        s=self._tsessions[uid]; step=s["step"]
        if step=="template": return pe("🖤",PE["brush"])+" <b>Выберите шаблон</b>\n\nТекст <code>"+TEMPLATE_PLACEHOLDER+"</code> будет заменён на ваш."
        if step=="text": return pe("✍️",PE["write"])+f" <b>Введите текст</b>\n\nШаблон: <b>{s['template']['title']}</b>\n2-4 символа — оптимально."
        if step=="preview": return pe("👁",PE["eye"])+f" <b>Предпросмотр</b>\n\nТекст: <code>{s['text']}</code>\nСмотрите на тестовый эмодзи выше."
        if step=="color":
            hist=self._color_history()
            hs=("\n"+pe("⏰",PE["clock"])+" Последние: "+"  ".join(f"<code>{c}</code>" for c in hist)) if hist else ""
            return pe("🎨",PE["palette"])+f" <b>Цвет эмодзи</b>\n\nТекст: <code>{s['text']}</code>{hs}"
        if step=="gradient_menu": return self._gradient_menu_text()
        if step=="name":
            g=s.get("gradient")
            clabel=g["name"] if g else (s.get('color') or "без перекраски")
            return pe("🏷",PE["sticker"])+f" <b>Название пака</b>\n\nТекст: <code>{s['text']}</code>  Цвет: <code>{clabel}</code>"
        return pe("⏰",PE["clock"])+" <b>Создаём...</b>"

    def _jt_markup(self,uid):
        s=self._tsessions[uid]; step=s["step"]
        if step=="template": return [[{"text":t["title"],"icon_custom_emoji_id":PE["sticker"],
            "callback":self._jt_tmpl,"args":(uid,i)}] for i,t in enumerate(TEMPLATE_SETS)]
        if step=="text": return [[{"text":"Ввести текст","icon_custom_emoji_id":PE["palette"],
            "input":"Текст (вместо "+TEMPLATE_PLACEHOLDER+")","handler":self._jt_text_in,"args":(uid,)}]]
        if step=="preview": return [[
            {"text":"✅ Хорошо","icon_custom_emoji_id":PE["ok"],"callback":self._jt_confirm,"args":(uid,)},
            {"text":"✏️ Изменить","icon_custom_emoji_id":PE["palette"],"callback":self._jt_retry,"args":(uid,)},
        ]]
        if step=="color":
            # ── Инлайн цветовых рядов (ИСПРАВЛЕНИЕ бага 1) ──
            hist=self._color_history()
            rows=[]; row=[]
            for label,hv in PRESET_COLORS.items():
                row.append({"text":label,"callback":self._jt_col,"args":(uid,hv)})
                if len(row)==2: rows.append(row); row=[]
            if row: rows.append(row)
            if hist: rows.append([{"text":c,"callback":self._jt_col,"args":(uid,c)} for c in hist[:3]])
            rows.append([{"text":"🌈 Пикер","icon_custom_emoji_id":PE["link"],"url":"https://get-color.ru/"},
                         {"text":"✏️ HEX","icon_custom_emoji_id":PE["palette"],
                          "input":"Введите HEX","handler":self._jt_hex,"args":(uid,)}])
            rows.append([{"text":"🎨 Меню градиента","icon_custom_emoji_id":PE["stats"],
                          "callback":self._jt_open_grad,"args":(uid,)}])
            rows.append([{"text":"❌ Без перекраски","icon_custom_emoji_id":PE["err"],
                          "callback":self._jt_col,"args":(uid,None)}])
            return rows
        if step=="gradient_menu":
            return self._gradient_menu_markup(self._jt_grad,uid,self._jt_back_col)
        if step=="name": return [[{"text":"Ввести название","icon_custom_emoji_id":PE["palette"],
            "input":"short_name (a-z,0-9,_)","handler":self._jt_name,"args":(uid,)}]]
        return []

    async def _jt_tmpl(self,call,uid,idx):
        s=self._tsessions.get(uid)
        if not s: await call.answer("Сессия устарела.",show_alert=True); return
        s["template"]=TEMPLATE_SETS[idx]; s["step"]="text"
        await call.edit(text=self._jt_text(uid),reply_markup=self._jt_markup(uid))

    async def _jt_text_in(self,call,value,uid):
        s=self._tsessions.get(uid)
        if not s: await call.answer("Сессия устарела.",show_alert=True); return
        c=value.strip()
        if not c: await call.answer("Пустой текст.",show_alert=True); return
        if len(c)>12: await call.answer("Макс 12 символов.",show_alert=True); return
        s["text"]=c; s["step"]="preview"
        await call.edit(text=self._jt_text(uid),reply_markup=self._jt_markup(uid))
        asyncio.ensure_future(self._jt_preview(call,uid))

    async def _jt_preview(self,call,uid):
        s=self._tsessions.get(uid)
        if not s: return
        try:
            fs=await self._client(functions.messages.GetStickerSetRequest(
                stickerset=types.InputStickerSetShortName(short_name=s["template"]["short_name"]),hash=0))
            doc=fs.documents[0]
            raw=await download_cached(self._client,doc)
            mime=getattr(doc,"mime_type","")
            if mime=="application/x-tgsticker":
                pat=replace_text_in_tgs(raw,TEMPLATE_PLACEHOLDER,s["text"])
                buf=io.BytesIO(pat); buf.name="preview.tgs"
            else:
                buf=io.BytesIO(raw); buf.name="preview.webp"
            buf.seek(0)
            s["preview_msg"]=await self._client.send_file(
                "me",buf,caption=pe("👁",PE["eye"])+" <b>Preview: "+s["text"]+"</b>",parse_mode="HTML")
        except Exception: pass

    async def _jt_confirm(self,call,uid):
        s=self._tsessions.get(uid)
        if not s: await call.answer("Сессия устарела.",show_alert=True); return
        if s.get("preview_msg"):
            try: await s["preview_msg"].delete()
            except Exception: pass
        s["step"]="color"
        await call.edit(text=self._jt_text(uid),reply_markup=self._jt_markup(uid))

    async def _jt_retry(self,call,uid):
        s=self._tsessions.get(uid)
        if not s: await call.answer("Сессия устарела.",show_alert=True); return
        if s.get("preview_msg"):
            try: await s["preview_msg"].delete()
            except Exception: pass
        s["step"]="text"; s["text"]=None
        await call.edit(text=self._jt_text(uid),reply_markup=self._jt_markup(uid))

    async def _jt_col(self,call,uid,hc):
        s=self._tsessions.get(uid)
        if not s: await call.answer("Сессия устарела.",show_alert=True); return
        s["color"]=hc; s["gradient"]=None; s["step"]="name"
        await call.edit(text=self._jt_text(uid),reply_markup=self._jt_markup(uid))

    async def _jt_hex(self,call,value,uid):
        s=self._tsessions.get(uid)
        if not s: await call.answer("Сессия устарела.",show_alert=True); return
        c=value.strip()
        if not c.startswith("#"): c="#"+c
        if not re.fullmatch(r"#[0-9a-fA-F]{6}",c): await call.answer("Неверный HEX.",show_alert=True); return
        s["color"]=c.upper(); s["gradient"]=None; s["step"]="name"
        await call.edit(text=self._jt_text(uid),reply_markup=self._jt_markup(uid))

    async def _jt_open_grad(self,call,uid):
        s=self._tsessions.get(uid)
        if not s: await call.answer("Сессия устарела.",show_alert=True); return
        s["step"]="gradient_menu"
        await call.edit(text=self._jt_text(uid),reply_markup=self._jt_markup(uid))

    async def _jt_grad(self,call,uid,grad_id):
        s=self._tsessions.get(uid)
        if not s: await call.answer("Сессия устарела.",show_alert=True); return
        g=next((x for x in GRADIENT_PRESETS if x["id"]==grad_id),None)
        if not g: return
        s["gradient"]=g; s["color"]="grad:"+g["name"]; s["step"]="name"
        await call.edit(text=self._jt_text(uid),reply_markup=self._jt_markup(uid))

    async def _jt_back_col(self,call,uid):
        s=self._tsessions.get(uid)
        if not s: await call.answer("Сессия устарела.",show_alert=True); return
        s["step"]="color"
        await call.edit(text=self._jt_text(uid),reply_markup=self._jt_markup(uid))

    async def _jt_name(self,call,value,uid):
        s=self._tsessions.get(uid)
        if not s: await call.answer("Сессия устарела.",show_alert=True); return
        c=value.strip().lower()
        if not validate_short_name(c): await call.answer("Только a-z,0-9,_",show_alert=True); return
        me=await self._client.get_me()
        s["pack_name"]=c+"_by_"+(me.username or "userbot"); s["step"]="processing"
        await call.edit(text=self._jt_text(uid))
        asyncio.ensure_future(self._jt_run(call,uid))

    async def _jt_run(self,call,uid):
        s=self._tsessions[uid]
        tmpl,txt,pname,color=s["template"],s["text"],s["pack_name"],s.get("color")
        gradient=s.get("gradient")
        try:
            fs=await self._client(functions.messages.GetStickerSetRequest(
                stickerset=types.InputStickerSetShortName(short_name=tmpl["short_name"]),hash=0))
        except Exception as e:
            await call.edit(text=pe("❌",PE["err"])+" Шаблон: <code>"+str(e)+"</code>")
            self._tsessions.pop(uid,None); return
        docs=list(fs.documents)
        me=await self._client.get_me(); mee=await self._client.get_input_entity("me")
        async def _fn(i,doc):
            raw=await download_cached(self._client,doc)
            mime=getattr(doc,"mime_type","")
            if mime=="application/x-tgsticker":
                patched=replace_text_in_tgs(raw,TEMPLATE_PLACEHOLDER,txt)
                lottie_obj=json.loads(gzip.decompress(patched))
                if gradient:
                    apply_gradient_lottie(lottie_obj,gradient)
                elif color:
                    tint_lottie(lottie_obj,color)
                patched=compress_tgs(lottie_obj)
                buf=io.BytesIO(patched); buf.name="sticker.tgs"
            else:
                img=Image.open(io.BytesIO(raw)).convert("RGBA").resize((512,512),Image.LANCZOS)
                if color: img=tint_image(img,color)
                buf=io.BytesIO(); img.save(buf,format="WEBP",lossless=True); buf.seek(0); buf.name="sticker.webp"
            es="✨"
            for a in doc.attributes:
                if isinstance(a,(DocumentAttributeCustomEmoji,DocumentAttributeSticker)):
                    es=getattr(a,"alt",None) or "✨"; break
            up=await self._client.upload_file(buf,file_name=buf.name)
            return await _upload_item(self._client,mee,up,mime,es,True)
        ordered=await self._parallel(docs,_fn,"Создаём",call)
        if not ordered:
            await call.edit(text=pe("❌",PE["err"])+" Ни один эмодзи не обработан.")
            self._tsessions.pop(uid,None); return
        color_label=gradient["name"] if gradient else (color or "без перекраски")
        try:
            fn,err=await _safe_create_set(self._client,me.id,txt+" Emoji Pack",pname,ordered,True)
            if err: raise ValueError(err)
            link="https://t.me/addemoji/"+fn
        except Exception as e:
            await call.edit(text=pe("❌",PE["err"])+" <code>"+str(e)+"</code>")
            self._tsessions.pop(uid,None); return
        stats=self.db.get("JellyColor","stats",[])
        stats.append({"name":fn,"link":link,"color":color or "text","count":len(ordered),"type":"emoji","ts":int(time.time())})
        self.db.set("JellyColor","stats",stats)
        await call.edit(
            text=(pe("✅",PE["ok"])+" <b>Готово!</b>\n\n"
                  +pe("✍️",PE["write"])+f" Текст: <code>{txt}</code>\n"
                  +pe("🎨",PE["palette"])+f" Цвет: <code>{color_label}</code>\n"
                  +pe("📦",PE["pack"])+f" <b>{len(ordered)}</b> шт.\n\n"
                  +pe("🔗",PE["link"])+f" <a href=\"{link}\">{link}</a>"),
            reply_markup=[[{"text":"Открыть","icon_custom_emoji_id":PE["link"],"url":link}]],
        )
        self._tsessions.pop(uid,None)

    # ─── .tstats ──────────────────────────────────────────────────────────────

    @loader.command()
    async def tstats(self, message: Message):
        """Статистика операций"""
        stats=self.db.get("JellyColor","stats",[])
        if not stats: await utils.answer(message,pe("📊",PE["stats"])+" Пусто."); return
        total_s=sum(e.get("count",0) for e in stats)
        chist={}
        for e in stats:
            c=e.get("color","")
            if c and c!="text" and not c.startswith("svg:"): chist[c]=chist.get(c,0)+1
        top=[f"<code>{c}</code>×{n}" for c,n in sorted(chist.items(),key=lambda x:-x[1])[:3]]
        lines=[
            pe("📊",PE["stats"])+" <b>JellyColor</b>\n",
            pe("📦",PE["pack"])+f" Операций: <b>{len(stats)}</b> | Стикеров: <b>{total_s}</b>",
            pe("🎨",PE["palette"])+" Топ цвета: "+("  ".join(top) or "—"),
            "\n<b>Последние 15:</b>",
        ]
        for i,e in enumerate(reversed(stats[-15:]),1):
            c=e.get("color","?"); t=e.get("type","emoji")
            cs="текст" if c=="text" else ("SVG "+c[4:] if c.startswith("svg:") else f"<code>{c}</code>")
            ti=pe("🏷",PE["sticker"]) if t=="sticker" else pe("✅",PE["ok"])
            lines.append(f"\n<b>{i}.</b> {ti} <code>{e['name']}</code>\n   {pe(chr(0x1f58c),PE['brush'])} {cs} | {pe(chr(0x1f4e6),PE['pack'])} <b>{e['count']}</b>\n   <a href=\"{e['link']}\">{e['link']}</a>")
        await utils.answer(message,"\n".join(lines),parse_mode="HTML")

    # ─── .jdel ────────────────────────────────────────────────────────────────

    @loader.command()
    async def jdel(self, message: Message):
        """Удалить запись из статистики: .jdel short_name"""
        args=utils.get_args_raw(message).strip()
        if not args: await utils.answer(message,pe("ℹ️",PE["info"])+" <code>.jdel short_name</code>"); return
        stats=self.db.get("JellyColor","stats",[])
        new=[e for e in stats if e.get("name")!=args]
        if len(new)==len(stats): await utils.answer(message,pe("❌",PE["err"])+f" <code>{args}</code> не найден."); return
        self.db.set("JellyColor","stats",new)
        await utils.answer(message,pe("✅",PE["ok"])+f" Удалено: <code>{args}</code>")

    # ─── .jexport ─────────────────────────────────────────────────────────────

    @loader.command()
    async def jexport(self, message: Message):
        """Экспорт статистики в JSON"""
        stats=self.db.get("JellyColor","stats",[])
        if not stats: await utils.answer(message,pe("ℹ️",PE["info"])+" Пустая статистика."); return
        buf=io.BytesIO(json.dumps(stats,ensure_ascii=False,indent=2).encode()); buf.name="jelly_stats.json"; buf.seek(0)
        await self._client.send_file(message.chat_id,buf,
            caption=pe("📤",PE["export"])+f" Экспорт — <b>{len(stats)}</b> записей",parse_mode="HTML")
        await message.delete()

    # ─── .jdump ───────────────────────────────────────────────────────────────

    @loader.command()
    async def jdump(self, message: Message):
        """Ответьте на эмодзи — дамп TGS + JSON"""
        reply=await message.get_reply_message()
        if not reply: await utils.answer(message,pe("❌",PE["err"])+" Ответьте на эмодзи."); return
        eid=None
        for ent in (reply.entities or []):
            if isinstance(ent,MessageEntityCustomEmoji): eid=ent.document_id; break
        if eid is None: await utils.answer(message,pe("❌",PE["err"])+" Премиум эмодзи не найдено."); return
        msg=await utils.answer(message,pe("⏰",PE["clock"])+" Дамплю...")
        docs=await self._client(functions.messages.GetCustomEmojiDocumentsRequest(document_id=[eid]))
        if not docs: await msg.edit(pe("❌",PE["err"])+" Нет документа."); return
        doc=docs[0]; raw=await download_cached(self._client,doc)
        mime=getattr(doc,"mime_type","")
        lines=[f"id: {eid}",f"mime: {mime}",f"size: {len(raw)} bytes"]
        if mime=="application/x-tgsticker":
            try:
                lottie=json.loads(gzip.decompress(raw))
                lines+=[f"w={lottie.get('w')} h={lottie.get('h')} fr={lottie.get('fr')} v={lottie.get('v')}",
                        f"layers: {len(lottie.get('layers',[]))}",
                        f"assets: {len(lottie.get('assets',[]))}",
                        f"text_bounds: {_get_textgroup_bounds(lottie)}",
                        f"dominant_color: {get_dominant_lottie_color(lottie)}",
                        "\n--- FULL JSON ---",
                        json.dumps(lottie,indent=2,ensure_ascii=False)]
            except Exception as e: lines.append(f"ERROR: {e}")
        bd=io.BytesIO("\n".join(lines).encode()); bd.name=f"dump_{eid}.txt"; bd.seek(0)
        br=io.BytesIO(raw); br.name=f"raw_{eid}.tgs"; br.seek(0)
        await self._client.send_file(message.chat_id,bd,caption=f"📄 Dump <code>{eid}</code>",parse_mode="HTML")
        await self._client.send_file(message.chat_id,br)
        await msg.delete()

    async def _resolve_target(self, reply):
        td=tt=ts=None
        if reply.sticker:
            for a in reply.sticker.attributes:
                if isinstance(a,DocumentAttributeSticker):
                    ss=a.stickerset
                    if isinstance(ss,(InputStickerSetShortName,InputStickerSetID)):
                        td,tt,ts=reply.sticker,"sticker",ss; break
        if not td:
            for ent in (reply.entities or []):
                if isinstance(ent,MessageEntityCustomEmoji):
                    docs=await self._client(functions.messages.GetCustomEmojiDocumentsRequest(document_id=[ent.document_id]))
                    if not docs: continue
                    doc=docs[0]
                    for a in doc.attributes:
                        if isinstance(a,(DocumentAttributeCustomEmoji,DocumentAttributeSticker)):
                            ss=getattr(a,"stickerset",None)
                            if ss and not isinstance(ss,InputStickerSetEmpty):
                                td,tt,ts=doc,"emoji",ss; break
                    if td: break
        return td,tt,ts

    async def _parallel(self, docs, fn, label, call):
        results=[]; lock=asyncio.Lock(); progress=[0]; sem=self._sem()
        async def _run(i,doc):
            async with sem:
                try: item=await fn(i,doc)
                except Exception: item=None
            async with lock:
                if item is not None: results.append((i,item))
                progress[0]+=1
            p=progress[0]; n=len(docs)
            if n>1:
                bar="█"*p+"░"*(n-p)
                try:
                    await call.edit(text=(
                        pe("⏰",PE["clock"])+f" <b>{label}...</b>\n\n"
                        f"<code>[{bar}]</code> {int(p/n*100)}%\n"
                        f"<b>{p}/{n}</b>"
                    ))
                except Exception: pass
        await asyncio.gather(*[_run(i,d) for i,d in enumerate(docs)])
        results.sort(key=lambda x:x[0])
        return [x for _,x in results]
