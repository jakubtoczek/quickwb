"""Generate the QuickWB app icon: the classic split black/white white-balance
disc on a dark rounded tile. Run: python misc/make_icon.py"""

from pathlib import Path

from PIL import Image, ImageDraw

OUT = Path(__file__).resolve().parent.parent / "src" / "quickwb" / "assets"
S = 256  # master size, supersampled x4 then downscaled for smooth edges


def render(size: int) -> Image.Image:
    ss = size * 4
    im = Image.new("RGBA", (ss, ss), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    pad = ss // 12
    d.rounded_rectangle([0, 0, ss - 1, ss - 1], radius=ss // 6, fill=(43, 43, 43, 255))
    box = [pad * 2, pad * 2, ss - pad * 2, ss - pad * 2]
    d.ellipse(box, fill=(240, 240, 240, 255), outline=(120, 120, 120, 255), width=ss // 64)
    # right half black -> the two-tone WB disc
    d.pieslice(box, -90, 90, fill=(24, 24, 24, 255))
    return im.resize((size, size), Image.LANCZOS)


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    sizes = [16, 32, 48, 64, 128, 256]
    imgs = [render(s) for s in sizes]
    imgs[-1].save(OUT / "quickwb.png")
    imgs[-1].save(OUT / "quickwb.ico", sizes=[(s, s) for s in sizes])
    print("wrote", OUT / "quickwb.ico", "and quickwb.png")


if __name__ == "__main__":
    main()
