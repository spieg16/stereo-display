import time
import requests
import xml.etree.ElementTree as ET
from io import BytesIO

import pygame
from PIL import Image

NODE_IP = "192.168.4.40"
BASE = f"http://{NODE_IP}:11000"

SAFE_MARGIN_X_RATIO = 0.08
SAFE_MARGIN_Y_RATIO = 0.10

# Horizontal correction for composite CRT pixel aspect
PIXEL_ASPECT_X = 1.50


def parse_status_xml(xml_text: str) -> dict:
    root = ET.fromstring(xml_text)

    def t(tag: str) -> str:
        v = root.findtext(tag)
        return (v or "").strip()

    return {
        "etag": (root.attrib.get("etag") or "").strip(),
        "image": t("image"),
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

    corrected_iw = iw * PIXEL_ASPECT_X

    scale = min(target_w / corrected_iw, target_h / ih)

    nw = int(iw * scale * PIXEL_ASPECT_X)
    nh = int(ih * scale)

    resized = img.resize((nw, nh), Image.Resampling.LANCZOS)

    canvas = Image.new("RGB", (target_w, target_h), (0, 0, 0))

    x = (target_w - nw) // 2
    y = (target_h - nh) // 2

    canvas.paste(resized, (x, y))

    return canvas


def pil_to_surface(img: Image.Image) -> pygame.Surface:
    return pygame.image.fromstring(img.tobytes(), img.size, img.mode)


def main():
    pygame.display.init()

    screen = pygame.display.set_mode((0, 0), pygame.FULLSCREEN)
    pygame.mouse.set_visible(False)

    sw, sh = screen.get_size()

    safe_x = int(sw * SAFE_MARGIN_X_RATIO)
    safe_y = int(sh * SAFE_MARGIN_Y_RATIO)
    safe_w = sw - (safe_x * 2)
    safe_h = sh - (safe_y * 2)

    art_rect = pygame.Rect(safe_x, safe_y, safe_w, safe_h)

    etag = None
    last_art_key = None
    last_art_surface = None

    while True:

        for event in pygame.event.get():
            if event.type == pygame.KEYDOWN and event.key == pygame.K_ESCAPE:
                return

        try:
            st = get_status(etag, timeout_s=90)
            etag = st["etag"] or etag

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

            pygame.display.flip()

        except requests.RequestException:
            time.sleep(2)

        except ET.ParseError:
            time.sleep(1)


if __name__ == "__main__":
    main()
