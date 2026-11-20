"""Brand import: point at the customer's website, get their colors, logo, font and words.

The words feed the brand-voice step; the colors, logo and font style every funnel page.
The fetch is hardened because it runs on our server with a URL a customer typed in:
only http(s), only public IP addresses (checked on every redirect), size and time capped.
"""
from __future__ import annotations

import colorsys
import hashlib
import ipaddress
import re
import socket
from collections import Counter
from html.parser import HTMLParser
from urllib.parse import urljoin, urlparse

from .config import settings

MAX_HTML = 2_000_000
MAX_CSS = 1_000_000
MAX_IMAGE = 2_000_000
UA = "Mozilla/5.0 (compatible; BrandImport/1.0)"


class FetchError(ValueError):
    pass


# ------------------------------------------------------------------ safe fetching
def _public_host(host: str) -> None:
    if not host:
        raise FetchError("That web address is missing a domain.")
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror:
        raise FetchError(f"Couldn't find {host}. Check the spelling.")
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if not ip.is_global or ip.is_multicast:
            raise FetchError("That address isn't a public website.")


def normalize_url(url: str) -> str:
    url = (url or "").strip()
    if not url:
        raise FetchError("Enter your website address.")
    if "://" not in url:
        url = "https://" + url
    p = urlparse(url)
    if p.scheme not in ("http", "https") or not p.hostname:
        raise FetchError("Enter a normal website address like yourbusiness.com")
    return url


def fetch(url: str, limit: int, kinds: tuple[str, ...] = ()) -> tuple[bytes, str, str]:
    """GET a public URL. Returns (body, content_type, final_url)."""
    for _ in range(6):  # follow up to 5 redirects, re-checking the host each time
        p = urlparse(url)
        if p.scheme not in ("http", "https"):
            raise FetchError("Unsupported link.")
        _public_host(p.hostname or "")
        import httpx

        try:
            with httpx.Client(timeout=12, follow_redirects=False, headers={"User-Agent": UA}) as c:
                with c.stream("GET", url) as r:
                    if r.is_redirect:
                        url = urljoin(url, r.headers.get("location", ""))
                        continue
                    if r.status_code >= 400:
                        raise FetchError(f"The site answered with an error ({r.status_code}).")
                    ctype = r.headers.get("content-type", "").split(";")[0].strip().lower()
                    if kinds and not any(ctype.startswith(k) for k in kinds):
                        raise FetchError(f"Unexpected content type {ctype or 'unknown'}.")
                    data = bytearray()
                    for chunk in r.iter_bytes():
                        data += chunk
                        if len(data) > limit:
                            raise FetchError("That file is too large.")
                    return bytes(data), ctype, url
        except httpx.HTTPError as e:
            raise FetchError(f"Couldn't load the site ({e.__class__.__name__}).")
    raise FetchError("Too many redirects.")


# ------------------------------------------------------------------ parsing
class _Page(HTMLParser):
    TEXT_TAGS = {"h1", "h2", "h3", "p", "li", "blockquote"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.meta: dict[str, str] = {}
        self.title = ""
        self.icons: list[str] = []
        self.logo_imgs: list[str] = []
        self.stylesheets: list[str] = []
        self.css: list[str] = []
        self.text: list[str] = []
        self._stack: list[str] = []
        self._in_title = self._in_style = False
        self._skip = 0

    def handle_starttag(self, tag, attrs):
        a = {k.lower(): (v or "") for k, v in attrs}
        if tag in ("script", "noscript", "svg"):
            self._skip += 1
        if tag == "title":
            self._in_title = True
        elif tag == "style":
            self._in_style = True
        elif tag == "meta":
            key = (a.get("name") or a.get("property") or "").lower()
            if key and a.get("content"):
                self.meta[key] = a["content"]
        elif tag == "link":
            rel = a.get("rel", "").lower()
            if "stylesheet" in rel and a.get("href"):
                self.stylesheets.append(a["href"])
            elif "icon" in rel and a.get("href"):
                (self.icons.insert(0, a["href"]) if "apple" in rel else self.icons.append(a["href"]))
        elif tag == "img":
            src = a.get("src") or a.get("data-src") or ""
            hay = " ".join([src, a.get("alt", ""), a.get("class", ""), a.get("id", "")]).lower()
            if src and "logo" in hay:
                self.logo_imgs.append(src)
        if a.get("style"):
            self.css.append(a["style"])
        self._stack.append(tag)

    def handle_endtag(self, tag):
        if tag in ("script", "noscript", "svg") and self._skip:
            self._skip -= 1
        if tag == "title":
            self._in_title = False
        elif tag == "style":
            self._in_style = False
        if self._stack and self._stack[-1] == tag:
            self._stack.pop()

    def handle_data(self, data):
        if self._in_title:
            self.title += data
        elif self._in_style:
            self.css.append(data)
        elif not self._skip and self._stack and self._stack[-1] in self.TEXT_TAGS:
            t = " ".join(data.split())
            if len(t) > 2:
                self.text.append(t)


HEX = re.compile(r"#([0-9a-fA-F]{6}|[0-9a-fA-F]{3})\b")
RGB = re.compile(r"rgba?\(\s*(\d{1,3})[\s,]+(\d{1,3})[\s,]+(\d{1,3})")
FONT = re.compile(r"font-family\s*:\s*([^;}{]+)", re.I)
GENERIC_FONTS = {"sans-serif", "serif", "monospace", "cursive", "fantasy", "system-ui", "inherit", "initial",
                 "-apple-system", "blinkmacsystemfont", "segoe ui", "roboto", "helvetica", "helvetica neue",
                 "arial", "ui-sans-serif", "apple color emoji", "segoe ui emoji", "noto color emoji", "unset"}


def _hex(r: int, g: int, b: int) -> str:
    return f"#{r:02x}{g:02x}{b:02x}"


def _usable(h: str) -> bool:
    r, g, b = (int(h[i:i + 2], 16) / 255 for i in (1, 3, 5))
    hue, light, sat = colorsys.rgb_to_hls(r, g, b)
    return sat > 0.25 and 0.08 < light < 0.85  # skip greys, near-white, near-black (deep navy stays)


def _hue(h: str) -> float:
    r, g, b = (int(h[i:i + 2], 16) / 255 for i in (1, 3, 5))
    return colorsys.rgb_to_hls(r, g, b)[0] * 360


def palette(css: str, theme_color: str = "") -> list[str]:
    counts: Counter[str] = Counter()
    for m in HEX.finditer(css):
        v = m.group(1).lower()
        if len(v) == 3:
            v = "".join(ch * 2 for ch in v)
        counts["#" + v] += 1
    for m in RGB.finditer(css):
        r, g, b = (min(255, int(x)) for x in m.groups())
        counts[_hex(r, g, b)] += 1
    tc = theme_color.strip().lower()
    if HEX.fullmatch(tc) and len(tc) == 7:
        counts[tc] += 50
    picked: list[str] = []
    for color, _ in counts.most_common():
        if not _usable(color):
            continue
        if all(min(abs(_hue(color) - _hue(p)), 360 - abs(_hue(color) - _hue(p))) > 25 for p in picked):
            picked.append(color)
        if len(picked) == 3:
            break
    return picked


def main_font(css: str) -> str:
    counts: Counter[str] = Counter()
    for m in FONT.finditer(css):
        for fam in m.group(1).split(","):
            name = fam.strip().strip("'\"").strip()
            low = name.lower()
            if not name or low in GENERIC_FONTS or low.startswith("var(") or "awesome" in low or "icon" in low:
                continue
            counts[name] += 1
            break
    return counts.most_common(1)[0][0][:60] if counts else ""


def import_site(url: str) -> dict:
    """Fetch a website and return {colors, font, logo_url, name, description, text, url}."""
    url = normalize_url(url)
    body, _, final = fetch(url, MAX_HTML, ("text/html", "application/xhtml"))
    page = _Page()
    page.feed(body.decode("utf-8", errors="replace"))
    css = "\n".join(page.css)
    for href in page.stylesheets[:4]:
        try:
            data, _, _ = fetch(urljoin(final, href), MAX_CSS, ("text/css", "text/plain"))
            css += "\n" + data.decode("utf-8", errors="replace")
        except FetchError:
            continue
    logo = next(iter(page.logo_imgs), "") or next(iter(page.icons), "") or page.meta.get("og:image", "")
    text, seen, total = [], set(), 0
    for t in page.text:
        if t in seen:
            continue
        seen.add(t)
        text.append(t)
        total += len(t)
        if total > 6000:
            break
    return {
        "url": final,
        "colors": palette(css, page.meta.get("theme-color", "")),
        "font": main_font(css),
        "logo_url": urljoin(final, logo) if logo else "",
        "name": (page.meta.get("og:site_name") or page.title.split("|")[0].split(" - ")[0]).strip()[:80],
        "description": (page.meta.get("description") or page.meta.get("og:description") or "").strip()[:400],
        "text": "\n".join(text),
    }


# ------------------------------------------------------------------ logos
IMAGE_TYPES = {b"\x89PNG": "png", b"\xff\xd8\xff": "jpg", b"GIF8": "gif", b"RIFF": "webp"}


def image_ext(data: bytes) -> str:
    for magic, ext in IMAGE_TYPES.items():
        if data.startswith(magic):
            if ext == "webp" and data[8:12] != b"WEBP":
                continue
            return ext
    head = data[:600].lstrip().lower()
    if head.startswith(b"<svg") or (head.startswith(b"<?xml") and b"<svg" in head):
        return "svg"
    raise FetchError("Logo must be a PNG, JPG, WEBP, GIF or SVG image.")


def save_logo(account_id: int, data: bytes) -> str:
    """Store a logo and return its public path."""
    if not data or len(data) > MAX_IMAGE:
        raise FetchError("Logo must be under 2 MB.")
    ext = image_ext(data)
    folder = settings.data_dir / "uploads" / str(account_id)
    folder.mkdir(parents=True, exist_ok=True)
    for old in folder.glob("logo-*"):
        old.unlink(missing_ok=True)
    name = f"logo-{hashlib.sha256(data).hexdigest()[:12]}.{ext}"
    (folder / name).write_bytes(data)
    return f"/media/{account_id}/{name}"


def download_logo(account_id: int, url: str) -> str:
    data, _, _ = fetch(url, MAX_IMAGE, ("image/",))
    return save_logo(account_id, data)


# ------------------------------------------------------------------ domains
DOMAIN_RE = re.compile(r"^(?=.{4,253}$)([a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$")


def clean_domain(value: str) -> str:
    d = (value or "").strip().lower()
    if "://" in d:
        d = urlparse(d).hostname or ""
    d = d.split("/")[0].split(":")[0].rstrip(".")
    if d and not DOMAIN_RE.match(d):
        raise FetchError("Enter a domain like go.yourbusiness.com")
    return d
