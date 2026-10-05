"""Paint the LaNorme mark and build every file made from it.

The painting happens in `painter/plume.html`: a pointed pen and ink on laid paper, and a
wax seal, simulated on a 4096 px canvas. This script opens that page in headless Chrome
for each variant, then uses ImageMagick for the sized PNGs and potrace for the vector.

Usage:
    python brand/build.py               # red wax (the chosen mark)
    python brand/build.py --wax green   # or the chancery's green

Needs Chrome or Chromium, ImageMagick 6 or 7, and potrace on the PATH. Standard library
only.
"""

from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent
PAINTER = ROOT / "painter" / "plume.html"
WORK = ROOT / "build"
ASSETS = ROOT / "assets"
DOCS_ASSETS = ROOT.parent / "docs" / "assets"
CHROME_ON_MACOS = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
WAX_TONES = {
    "red": {"body": "#a3241c", "shadow": "#64110d", "light": "#d9614f"},
    "green": {"body": "#2f6a45", "shadow": "#173b25", "light": "#6fa883"},
}

# The design is drawn on a 1024-unit square and painted at 4x. The sheet, its seal and
# the laces span x 322 to 702 and y 128 to 890; the GitHub square is cropped around
# them so they fill about 85% of its height, with the ink carried out to the edges.
GITHUB_CROP = "3600x3600+248+236"
DISC_MASK = "circle 2048,2048 2048,200"
# Headless Chrome on Linux shows a viewport shorter than its window, so the page is
# painted in a taller window and cropped back to the canvas.
WINDOW = "--window-size=1024,1200"


def find_chrome() -> str:
    """Return the Chrome binary: $CHROME, the macOS app, or one on the PATH."""
    names = ("google-chrome", "chromium", "chromium-browser")
    candidates = [
        os.environ.get("CHROME"),
        CHROME_ON_MACOS,
        *(shutil.which(name) for name in names),
    ]
    for candidate in candidates:
        if candidate and Path(candidate).exists():
            return candidate
    raise SystemExit("Chrome not found: set CHROME to its binary.")


def find_magick() -> list[str]:
    """Return the ImageMagick command: `magick` (version 7) or `convert` (version 6)."""
    for name in ("magick", "convert"):
        if shutil.which(name):
            return [name]
    raise SystemExit("ImageMagick not found on the PATH.")


def run_command(*command: str) -> None:
    """Run one command and stop the build if it fails."""
    subprocess.run(command, check=True)


def build_chrome_command(chrome: str, *, scale: int, screenshot: Path) -> list[str]:
    """Return the headless Chrome command line that screenshots a page at a scale."""
    command = [
        chrome,
        "--headless=new",
        "--disable-gpu",
        "--hide-scrollbars",
        f"--force-device-scale-factor={scale}",
        WINDOW,
        "--virtual-time-budget=120000",
        f"--screenshot={screenshot}",
    ]
    if hasattr(os, "geteuid") and os.geteuid() == 0:
        command.append("--no-sandbox")
    return command


def paint_sheet(*, chrome: str, magick: list[str], query: str, output: Path) -> None:
    """Open the painter with the query string and save the 4096 px sheet it paints."""
    screenshot = output.with_suffix(".window.png")
    command = build_chrome_command(chrome, scale=4, screenshot=screenshot)
    if "clear=1" in query:
        command.append("--default-background-color=00000000")
    command.append(f"{PAINTER.as_uri()}{query}")
    subprocess.run(command, check=True, stderr=subprocess.DEVNULL)
    run_command(*magick, str(screenshot), "-crop", "4096x4096+0+0", "+repage", str(output))


def resize_image(*, magick: list[str], source: Path, output: Path, size: int) -> None:
    """Write a square copy of a sheet at the given size."""
    run_command(
        *magick,
        str(source),
        "-filter",
        "Lanczos",
        "-resize",
        f"{size}x{size}",
        "-strip",
        str(output),
    )


def build_github_square(*, magick: list[str], source: Path, output: Path, size: int) -> None:
    """Carry the ink out to the square's edges and crop around the sheet and its seal."""
    run_command(
        *magick,
        str(source),
        "(",
        "-size",
        "4096x4096",
        "xc:black",
        "-fill",
        "white",
        "-draw",
        DISC_MASK,
        ")",
        "-compose",
        "multiply",
        "-composite",
        "-crop",
        GITHUB_CROP,
        "+repage",
        "-filter",
        "Lanczos",
        "-resize",
        f"{size}x{size}",
        "-strip",
        str(output),
    )


def trace_colour(*, magick: list[str], source: Path, output: Path, select: str) -> None:
    """Trace the pixels a selection marks (0 is traced) into an SVG with potrace."""
    bitmap = output.with_suffix(".pbm")
    run_command(*magick, str(source), "-fx", select, "-threshold", "50%", str(bitmap))
    options = ["--turdsize", "4", "--alphamax", "0.8", "--opttolerance", "0.15"]
    run_command("potrace", str(bitmap), "-b", "svg", *options, "-o", str(output))


def build_tone_selection(colour: str) -> str:
    """Return an ImageMagick -fx selection of the pixels close to one colour."""
    red, green, blue = (int(colour[i : i + 2], 16) / 255 for i in (1, 3, 5))
    return f"(abs(u.r-{red:.3f})+abs(u.g-{green:.3f})+abs(u.b-{blue:.3f})) < 0.16 ? 0 : 1"


def read_group(*, svg: Path, colour: str) -> str:
    """Return the path group potrace wrote, filled with the given colour."""
    group = re.search(r"<g transform=.*?</g>", svg.read_text(), re.S)
    if group is None:
        raise SystemExit(f"potrace wrote no paths to {svg}")
    return group.group(0).replace('fill="#000000"', f'fill="{colour}"')


def compose_svg(*, layers: list[tuple[Path, str]], output: Path) -> None:
    """Lay the traced layers, in order, over a white disc, transparent outside it."""
    groups = "\n".join(read_group(svg=svg, colour=colour) for svg, colour in layers)
    output.write_text(
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 4096 4096" width="1024" height="1024">\n'
        "<title>LaNorme</title>\n"
        "<desc>A sheet of laid paper cut from an ink disc, bearing a Didone N written with a "
        "pointed pen, and a wax seal hanging from its fold on silk laces, pressed with a "
        "carpenter's square.</desc>\n"
        '<circle cx="2048" cy="2048" r="1858" fill="#ffffff"/>\n'
        f"{groups}\n</svg>\n",
    )


def measure_difference(*, chrome: str, magick: list[str], painted: Path, vector: Path) -> str:
    """Render the SVG and return its RMSE against the painting, as ImageMagick reports it."""
    window = WORK / "vector-window.png"
    rendered = WORK / "vector-check.png"
    command = build_chrome_command(chrome, scale=1, screenshot=window)
    subprocess.run([*command, vector.as_uri()], check=True, stderr=subprocess.DEVNULL)
    run_command(*magick, str(window), "-crop", "1024x1024+0+0", "+repage", str(rendered))
    compare = ["magick", "compare"] if magick == ["magick"] else ["compare"]
    result = subprocess.run(
        [*compare, "-metric", "RMSE", str(painted), str(rendered), "null:"],
        capture_output=True,
        text=True,
        check=False,
    )
    return result.stderr.strip()


def paint_variants(*, chrome: str, magick: list[str], wax: str) -> None:
    """Paint every sheet the files are made from into the work directory."""
    # A sheet painted with clear=1 is transparent outside the disc.
    sheets = {
        "seal": f"?wax={wax}",
        "black-and-white": "",
        "seal-clear": f"?wax={wax}&clear=1",
        "small-seal": f"?wax={wax}&optical=small",
        "small-black-and-white-clear": "?optical=small&clear=1",
    }
    for name, query in sheets.items():
        print(f"painting {name}", flush=True)
        paint_sheet(chrome=chrome, magick=magick, query=query, output=WORK / f"{name}-4096.png")


def build_images(*, magick: list[str]) -> None:
    """Write the sized PNGs, the GitHub avatar, the favicons and the docs assets."""
    painted = WORK / "seal-4096.png"
    shutil.copy(painted, ASSETS / "lanorme-master-4096.png")
    resize_image(magick=magick, source=painted, output=ASSETS / "lanorme.png", size=1024)
    resize_image(magick=magick, source=painted, output=ASSETS / "lanorme-2048.png", size=2048)
    resize_image(
        magick=magick,
        source=WORK / "seal-clear-4096.png",
        output=ASSETS / "lanorme-transparent.png",
        size=1024,
    )
    resize_image(
        magick=magick,
        source=WORK / "black-and-white-4096.png",
        output=ASSETS / "lanorme-black-and-white.png",
        size=1024,
    )
    small = WORK / "small-seal-4096.png"
    build_github_square(
        magick=magick,
        source=small,
        output=ASSETS / "lanorme-github.png",
        size=1024,
    )
    build_github_square(
        magick=magick,
        source=small,
        output=ASSETS / "lanorme-github-500.png",
        size=500,
    )
    favicon = WORK / "small-black-and-white-clear-4096.png"
    for size in (16, 32, 48, 180):
        resize_image(
            magick=magick,
            source=favicon,
            output=ASSETS / "favicon" / f"favicon-{size}.png",
            size=size,
        )
    run_command(
        *magick,
        str(painted),
        "-crop",
        "720x720+1688+2720",
        "+repage",
        "-strip",
        str(ASSETS / "seal-close-up.png"),
    )
    shutil.copy(ASSETS / "favicon" / "favicon-180.png", DOCS_ASSETS / "logo.png")
    shutil.copy(ASSETS / "favicon" / "favicon-48.png", DOCS_ASSETS / "favicon.png")


def build_vector(*, magick: list[str], wax: str) -> Path:
    """Trace the ink and the three tones of the wax, and lay them into one SVG."""
    tones = WAX_TONES[wax]
    painted = WORK / "seal-4096.png"
    layers = [(WORK / "ink.svg", "#000000"), (WORK / "wax.svg", tones["body"])]
    trace_colour(
        magick=magick,
        source=WORK / "black-and-white-4096.png",
        output=layers[0][0],
        select="u.r<0.5 ? 0 : 1",
    )
    # Every coloured pixel is wax or lace; the shadow and the light are laid over it.
    chroma = "(max(u.r,max(u.g,u.b))-min(u.r,min(u.g,u.b))) > 0.12 ? 0 : 1"
    trace_colour(magick=magick, source=painted, output=layers[1][0], select=chroma)
    for tone in ("shadow", "light"):
        layer = WORK / f"wax-{tone}.svg"
        trace_colour(
            magick=magick,
            source=painted,
            output=layer,
            select=build_tone_selection(tones[tone]),
        )
        layers.append((layer, tones[tone]))
    vector = ASSETS / "lanorme.svg"
    compose_svg(layers=layers, output=vector)
    return vector


def main() -> None:
    """Paint the variants, then build the PNGs, the favicons and the SVG from them."""
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--wax", default="red", choices=sorted(WAX_TONES))
    arguments = parser.parse_args()
    if shutil.which("potrace") is None:
        raise SystemExit("potrace not found on the PATH.")
    chrome, magick = find_chrome(), find_magick()
    for directory in (WORK, ASSETS / "favicon", DOCS_ASSETS):
        directory.mkdir(parents=True, exist_ok=True)
    paint_variants(chrome=chrome, magick=magick, wax=arguments.wax)
    build_images(magick=magick)
    print("tracing the vector", flush=True)
    vector = build_vector(magick=magick, wax=arguments.wax)
    difference = measure_difference(
        chrome=chrome,
        magick=magick,
        painted=ASSETS / "lanorme.png",
        vector=vector,
    )
    print(f"vector against painting, RMSE: {difference}")
    print(f"done: {ASSETS}")


if __name__ == "__main__":
    main()
