import time
import textwrap
import requests
import xml.etree.ElementTree as ET
from io import BytesIO

import pygame
from PIL import Image

NODE_IP = "192.168.4.40"
BASE = f"http://{NODE_IP}:11000"

PADDING = 18
TEXT_PANEL_HEIGHT_RATIO = 0.40

BASE_FONT_SIZE = 28
SMALL_FONT_SIZE = 22

SAFE_MARGIN_X_RATIO = 0.08
SAFE_MARGIN_Y_RATIO = 0.10


def parse_status_xml(xml_text: str) -> dict:
    root = ET.fromstring(xml_text)

    def t(tag: str) -> str:
        v = root.findtext(tag)
        return (v or "").strip()

    return {
        "etag": (root.attrib.get("etag") or "").strip(),
        "title": t("name"),
        "artist": t("artist"),
        "album": t("album"),
        "service": t("service"),
        "image": t("image"),
        "state": t("state"),
        "mid": t("mid"),
        "title1": t("title1"),
        "title2": t("title2"),
        "title3": t("title3"),
        "twoline_title1": t("twoline_title1"),
        "twoline_title2": t("twoline_title2"),
    }


def choose_display_text(st: dict) -> dict:
    # Best case: BluOS provides 3 dedicated display lines
    if st["title1"] or st["title2"] or st["title3"]:
        return {
            "title": st["title1"] or "—",
            "artist": st["title2"] or "—",
            "album": st["title3"] or "—",
        }

    # Next fallback: 2-line display fields
    if st["twoline_title1"] or st["twoline_title2"]:
        return {
            "title": st["twoline_title1"] or st["title"] or "—",
            "artist": st["twoline_title2"] or st["artist"] or "—",
            "album": st["album"] or "—",
        }

    # Final fallback: classic fields
    return {
        "title": st["title"] or "—",
        "artist": st["artist"] or "—",
        "album": st["album"] or "—",
    }


def get_status(etag: str | None, timeout_s: int = 90) -> dict:
    params = {}
    if etag:
        params["etag"] = etag
        params["timeout"] = str(timeout_s)

    r = requests.get(f"{BASE}/Status", params=params, timeout=timeout_s + 10)
    r.raise_for_status()
    return parse_status_xml(r.text)


def fetch_art(image_path: str) -> Image.Image | None:
    if not image_path:
        return None

    if image_path.startswith("/"):
        url = BASE + image_path
    else:
        url = image_path

    url += ("&" if "?" in url else "?") + "followRedirects=1"

    r = requests.get(url, timeout=10)
    r.raise_for_status()
    return Image.open(BytesIO(r.content)).convert("RGB")


def fit_image_letterbox(img: Image.Image, target_w: int, target_h: int) -> Image.Image:
    iw, ih = img.size
    scale = min(target_w / iw, target_h / ih)
    nw, nh = int(iw * scale), int(ih * scale)
    resized = img.resize((nw, nh), Image.Resampling.LANCZOS)

    canvas = Image.new("RGB", (target_w, target_h), (0, 0, 0))
    x = (target_w - nw) // 2
    y = (target_h - nh) // 2
    canvas.paste(resized, (x, y))
    return canvas


def wrap_to_width(font: pygame.font.Font, text: str, max_width_px: int, max_lines: int) -> list[str]:
    if not text:
        return [""]

    guess = max(8, int(max_width_px / max(font.size("M")[0], 1)))
    lines = []

    for chunk in textwrap.wrap(text, width=guess):
        s = chunk
        while font.size(s)[0] > max_width_px and len(s) > 1:
            s = s[:-1]
        lines.append(s)

    if not lines:
        lines = [""]

    if len(lines) > max_lines:
        lines = lines[:max_lines]
        last = lines[-1]
        while font.size(last + "…")[0] > max_width_px and len(last) > 1:
            last = last[:-1]
        lines[-1] = last + "…"

    return lines


def draw_text_panel(
    screen: pygame.Surface,
    rect: pygame.Rect,
    fonts: dict,
    title: str,
    artist: str,
    album: str,
    source: str,
):
    pygame.draw.rect(screen, (12, 12, 12), rect)

    x = rect.x + PADDING
    y = rect.y + PADDING
    w = rect.w - PADDING * 2

    title_font = fonts["title"]
    body_font = fonts["body"]
    small_font = fonts["small"]

    for line in wrap_to_width(title_font, title or "—", w, max_lines=2):
        surf = title_font.render(line, True, (240, 240, 240))
        screen.blit(surf, (x, y))
        y += surf.get_height() + 6

    for value in [artist, album]:
        txt = value or "—"
        line = wrap_to_width(body_font, txt, w, max_lines=1)[0]
        surf = body_font.render(line, True, (200, 200, 200))
        screen.blit(surf, (x, y))
        y += surf.get_height() + 8

    src = source or "Unknown source"
    src_line = wrap_to_width(small_font, src, w, max_lines=1)[0]
    surf = small_font.render(src_line, True, (170, 170, 170))
    screen.blit(surf, (x, rect.bottom - PADDING - surf.get_height()))


def pil_to_surface(img: Image.Image) -> pygame.Surface:
    return pygame.image.fromstring(img.tobytes(), img.size, img.mode)


def main():
    pygame.display.init()
    pygame.font.init()

    screen = pygame.display.set_mode((0, 0), pygame.FULLSCREEN)
    pygame.mouse.set_visible(False)

    sw, sh = screen.get_size()

    scale = max(0.75, min(1.6, sw / 800))
    title_font = pygame.font.SysFont(None, int(BASE_FONT_SIZE * 1.25 * scale), bold=True)
    body_font = pygame.font.SysFont(None, int(BASE_FONT_SIZE * scale))
    small_font = pygame.font.SysFont(None, int(SMALL_FONT_SIZE * scale))

    fonts = {"title": title_font, "body": body_font, "small": small_font}

    safe_x = int(sw * SAFE_MARGIN_X_RATIO)
    safe_y = int(sh * SAFE_MARGIN_Y_RATIO)
    safe_w = sw - (safe_x * 2)
    safe_h = sh - (safe_y * 2)

    text_panel_h = int(safe_h * TEXT_PANEL_HEIGHT_RATIO)
    art_h = safe_h - text_panel_h

    art_rect = pygame.Rect(safe_x, safe_y, safe_w, art_h)
    text_rect = pygame.Rect(safe_x, safe_y + art_h, safe_w, text_panel_h)

    etag = None
    last_art_key = None
    last_art_surface = None
    last_display = {
        "title": "Connecting…",
        "artist": "",
        "album": "",
        "source": NODE_IP,
    }

    while True:
        for event in pygame.event.get():
            if event.type == pygame.KEYDOWN and event.key == pygame.K_ESCAPE:
                return

        try:
            st = get_status(etag, timeout_s=90)
            etag = st["etag"] or etag

            display = choose_display_text(st)
            display["source"] = st["service"] or "Unknown source"

            # Update displayed text every successful status refresh
            last_display = display

            # Refresh artwork whenever the image URL changes
            art_key = st["image"] or ""
            if art_key != last_art_key:
                img = fetch_art(st["image"])
                if img:
                    fitted = fit_image_letterbox(img, art_rect.w, art_rect.h)
                    last_art_surface = pil_to_surface(fitted)
                else:
                    last_art_surface = None
                last_art_key = art_key

            screen.fill((0, 0, 0))
            if last_art_surface:
                screen.blit(last_art_surface, (art_rect.x, art_rect.y))

            draw_text_panel(
                screen,
                text_rect,
                fonts,
                title=last_display["title"],
                artist=last_display["artist"],
                album=last_display["album"],
                source=last_display["source"],
            )

            pygame.display.flip()

        except requests.RequestException:
            screen.fill((0, 0, 0))
            draw_text_panel(
                screen,
                text_rect,
                fonts,
                title="Connecting…",
                artist="",
                album="",
                source=NODE_IP,
            )
            pygame.display.flip()
            time.sleep(2)

        except ET.ParseError:
            time.sleep(1)


if __name__ == "__main__":
    main()
