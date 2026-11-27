#!/usr/bin/env python3
"""Texture regression fixture for published-bundle stage images.

Measures matched-scale detail energy and block-scale patchiness of one rendered
stage image with ImageMagick only (Python standard library plus ``convert``),
and compares the numbers with a blessed baseline band.  See
``docs/published-workflows.md`` ("Texture regression baselines") for why these
two numbers exist and who runs them.

This tool never submits a generation: on the production host generations must
originate from the application's own authenticated API, so a page-context fetch
is not a secure context and no script owns submission.  Point it at a PNG.

Exit codes:
  0  metrics are inside every blessed band (or measurement succeeded)
  1  at least one metric left its band (a red build)
  2  no blessed baseline exists for that bundle/stage yet (run ``bless``)
  3  bad arguments, unreadable image, or an ImageMagick failure
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import shutil
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

CONVERT = "convert"

DEFAULT_BLOCK = 8
DEFAULT_SIGMAS: tuple[float, ...] = (0.6, 1.2, 2.5)
PATCH_SIGMA = 6.0
DEFAULT_RELATIVE_TOLERANCE = 0.15
DEFAULT_ABSOLUTE_TOLERANCE = 0.05
SCHEMA_VERSION = "comfyui-image-frontend.texture-baseline/v1"
BASELINE_ROOT = Path(__file__).resolve().parent / "texture-baselines"

EXIT_OK = 0
EXIT_BAND_FAILURE = 1
EXIT_NO_BASELINE = 2
EXIT_ERROR = 3

# The two blessed recipes, kept byte-for-byte as they were measured during the
# invisible-refine investigation.  ``<image>`` is the file (optionally preceded
# by the crop stage), ``<sigma>``/``<grid>``/``<size>`` are derived per run:
#
#   detail energy at scale sigma
#     convert <image> -colorspace gray \( +clone -blur 0x<sigma> \) \
#       -compose difference -composite -format '%[fx:mean*255]' info:
#   block-scale patchiness (the mosaic signal)
#     convert <image> -colorspace gray -resize <grid> -resize <size>! \
#       \( +clone -blur 0x6 \) -compose difference -composite \
#       -format '%[fx:mean*255]' info:
#
# ``<grid>`` is derived instead of hard-coded: ceil(measured width / block) x
# ceil(measured height / block), so an 8x8 block grid over the 1160x1160 matched
# crop is 145x145 and the recipe above reproduces the recorded numbers.  The
# downscale carries the same ``!`` as the documented upscale because a
# non-square crop's aspect never matches its own block grid; for the square
# matched crop both spellings decode to the identical 145x145 resize, so the
# recorded numbers are unchanged (asserted in scripts/tests).
DETAIL_RECIPE = (
    "convert <image> -colorspace gray ( +clone -blur 0x<sigma> ) "
    "-compose difference -composite -format '%[fx:mean*255]' info:"
)
PATCHINESS_RECIPE = (
    "convert <image> -colorspace gray -resize <grid>! -resize <size>! "
    "( +clone -blur 0x<patch sigma> ) -compose difference -composite "
    "-format '%[fx:mean*255]' info:"
)
CROP_RECIPE = "-crop <WxH+X+Y> +repage"

_TOKEN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._+-]*")


class TextureError(Exception):
    """A message the user should see, with the exit code it should produce."""

    exit_code = EXIT_ERROR


class MissingBaseline(TextureError):
    exit_code = EXIT_NO_BASELINE


class UsageError(TextureError):
    exit_code = EXIT_ERROR


# --------------------------------------------------------------------------
# ImageMagick
# --------------------------------------------------------------------------


def find_convert() -> str:
    """Resolve the ImageMagick 6 binary.  Everything else is stdlib."""
    path = shutil.which(CONVERT)
    if path is None:
        raise TextureError(
            f"ImageMagick `{CONVERT}` is not on PATH; install ImageMagick 6 "
            f"(expected /usr/bin/{CONVERT}). Every metric in this tool is one `{CONVERT}` call."
        )
    return path


def invoke(args: list[str]) -> subprocess.CompletedProcess[str]:
    """One subprocess call, no shell. The argv list is built by this module alone."""
    return subprocess.run(args, capture_output=True, text=True, check=False)  # noqa: S603


def failure_reason(completed: subprocess.CompletedProcess[str]) -> str:
    """The last ImageMagick diagnostic line, which is the useful one."""
    lines = (completed.stderr or completed.stdout or "").strip().splitlines()
    return lines[-1] if lines else "no diagnostic output"


def run_convert(args: list[str]) -> str:
    """Run one argument list. Never a shell: the arguments are the contract."""
    completed = invoke(args)
    if completed.returncode != 0:
        raise TextureError(
            f"`{CONVERT}` failed for `{' '.join(args)}`: {failure_reason(completed)}"
        )
    return completed.stdout


def imagemagick_version() -> str:
    """The version string recorded in a baseline, so a drift is explainable."""
    program = find_convert()
    for flag in ("-version", "--version"):
        try:
            output = run_convert([program, flag])
        except TextureError:
            continue
        for line in output.splitlines():
            name, separator, value = line.partition(":")
            if separator and name.strip() == "Version":
                return value.strip()
        if output.strip():
            return output.strip().splitlines()[0]
    raise TextureError(f"Could not read a version string from `{CONVERT}`.")


def image_size(program: str, image: Path) -> tuple[int, int]:
    """Width and height ImageMagick decodes, which also rejects a non-image."""
    if not image.exists():
        raise UsageError(f"No such file: {image}")
    if not image.is_file():
        raise UsageError(f"Not a file: {image}")
    geometry = [program, str(image), "-format", "%w %h", "info:"]
    completed = invoke(geometry)
    if completed.returncode != 0:
        raise TextureError(
            f"{image} could not be decoded as an image by `{CONVERT}` "
            f"(is it a PNG/JPEG render?): {failure_reason(completed)}"
        )
    text = completed.stdout
    fields = text.split()
    if len(fields) != 2:
        raise TextureError(f"`{CONVERT}` reported no geometry for {image}: {text!r}")
    try:
        width, height = (int(value) for value in fields)
    except ValueError as error:  # pragma: no cover - defensive
        raise TextureError(f"`{CONVERT}` reported bad geometry for {image}: {text!r}") from error
    if width < 1 or height < 1:
        raise TextureError(f"Image {image} has no pixels ({width}x{height}).")
    return width, height


# --------------------------------------------------------------------------
# Arguments and derived geometry
# --------------------------------------------------------------------------


def parse_crop(spec: str) -> tuple[int, int, int, int]:
    """Parse ``WxH+X+Y`` — the ImageMagick geometry an operator types."""
    match = re.fullmatch(r"\s*(\d+)x(\d+)([+-]\d+)([+-]\d+)\s*", spec)
    if not match:
        raise UsageError(f"Invalid crop {spec!r}; expected WxH+X+Y, for example 1160x1160+0+0.")
    width, height, x, y = (int(value) for value in match.groups())
    if width < 1 or height < 1:
        raise UsageError(f"Crop {spec!r} must have a positive width and height.")
    return width, height, x, y


def format_crop(crop: tuple[int, int, int, int]) -> str:
    width, height, x, y = crop
    return f"{width}x{height}{x:+d}{y:+d}"


def parse_sigmas(spec: str) -> tuple[float, ...]:
    values: list[float] = []
    for part in spec.replace(" ", "").split(","):
        if not part:
            raise UsageError(f"Invalid sigma list {spec!r}; use a comma-separated list.")
        try:
            value = float(part)
        except ValueError as error:
            raise UsageError(f"Invalid sigma {part!r} in {spec!r}.") from error
        if not 0.0 < value < 100.0:
            raise UsageError(f"Sigma {value} is outside the useful range (0, 100).")
        values.append(value)
    if not values:
        raise UsageError("At least one detail sigma is required.")
    if len(set(values)) != len(values):
        raise UsageError(f"Duplicate sigma in {spec!r}.")
    return tuple(values)


def parse_seed(text: str) -> int:
    try:
        seed = int(text)
    except ValueError as error:
        raise UsageError(f"Seed {text!r} is not an integer.") from error
    if seed < 0 or seed > 2**64 - 1:
        raise UsageError(f"Seed {text!r} is outside the 0..2**64-1 range.")
    return seed


def crop_arguments(crop: tuple[int, int, int, int] | None) -> list[str]:
    if crop is None:
        return []
    return ["-crop", format_crop(crop), "+repage"]


def block_grid(size: tuple[int, int], block: int) -> tuple[int, int]:
    """The patch grid: one sample per block edge, rounded up."""
    if block < 1:
        raise UsageError("Block size must be at least 1.")
    width, height = size
    return math.ceil(width / block), math.ceil(height / block)


def check_crop_bounds(crop: tuple[int, int, int, int], size: tuple[int, int]) -> None:
    width, height, x, y = crop
    image_width, image_height = size
    if x < 0 or y < 0:
        raise UsageError(
            f"Crop {format_crop(crop)} starts outside the image; X and Y must not be negative."
        )
    if x + width > image_width or y + height > image_height:
        raise UsageError(
            f"Crop {format_crop(crop)} falls outside the {image_width}x{image_height} image; "
            "a crop is measured in matched scale, so it must be fully inside the render."
        )


def format_number(value: float) -> str:
    return f"{value:.6g}"


def format_sigma(sigma: float) -> str:
    return f"{sigma:g}"


def detail_metric_name(sigma: float) -> str:
    return f"detail_energy_sigma_{format_sigma(sigma)}"


def patchiness_metric_name(block: int) -> str:
    return f"patchiness_block{block}"


# --------------------------------------------------------------------------
# Measurement
# --------------------------------------------------------------------------


def detail_energy_arguments(
    program: str,
    image: Path,
    sigma: float,
    crop: tuple[int, int, int, int] | None = None,
) -> list[str]:
    """The blessed detail-energy argument list: mean |gray - blur(gray, sigma)|."""
    return [
        program,
        str(image),
        *crop_arguments(crop),
        "-colorspace",
        "gray",
        "(",
        "+clone",
        "-blur",
        f"0x{format_sigma(sigma)}",
        ")",
        "-compose",
        "difference",
        "-composite",
        "-format",
        "%[fx:mean*255]",
        "info:",
    ]


def patchiness_arguments(
    program: str,
    image: Path,
    grid: tuple[int, int],
    size: tuple[int, int],
    crop: tuple[int, int, int, int] | None = None,
    patch_sigma: float = PATCH_SIGMA,
) -> list[str]:
    """The blessed block-patchiness argument list (the mosaic signal).

    Downscale to one pixel per block, upscale back to exactly the measured size,
    blur at block scale and take the difference: what survives is structure that
    lives at the block grid, which is exactly what a hard-denoise refine pass
    paints over skin and jewellery.
    """
    grid_text = f"{grid[0]}x{grid[1]}!"
    size_text = f"{size[0]}x{size[1]}!"
    return [
        program,
        str(image),
        *crop_arguments(crop),
        "-colorspace",
        "gray",
        "-resize",
        grid_text,
        "-resize",
        size_text,
        "(",
        "+clone",
        "-blur",
        f"0x{format_sigma(patch_sigma)}",
        ")",
        "-compose",
        "difference",
        "-composite",
        "-format",
        "%[fx:mean*255]",
        "info:",
    ]


def run_metric(args: list[str]) -> float:
    """Run one metric argument list and read the single number it prints."""
    text = run_convert(args).strip()
    try:
        value = float(text)
    except ValueError as error:
        raise TextureError(f"`{CONVERT}` returned {text!r}, which is not a number.") from error
    return round(value, 6)


def measured_size(
    program: str, image: Path, crop: tuple[int, int, int, int] | None
) -> tuple[int, int]:
    """Validate the crop against the render and return the measured geometry."""
    size = image_size(program, image)
    if crop is None:
        return size
    check_crop_bounds(crop, size)
    return crop[0], crop[1]


def detail_metrics(
    program: str,
    image: Path,
    sigmas: tuple[float, ...],
    crop: tuple[int, int, int, int] | None = None,
) -> dict[str, float]:
    """Detail energy at every requested blur scale: one `convert` per scale."""
    return {
        detail_metric_name(sigma): run_metric(detail_energy_arguments(program, image, sigma, crop))
        for sigma in sigmas
    }


def block_patchiness(
    program: str,
    image: Path,
    size: tuple[int, int],
    block: int = DEFAULT_BLOCK,
    crop: tuple[int, int, int, int] | None = None,
) -> float:
    """Block-scale patchiness of the measured `size` geometry."""
    return run_metric(patchiness_arguments(program, image, block_grid(size, block), size, crop))


def measure_image(
    image: Path,
    crop: tuple[int, int, int, int] | None = None,
    block: int = DEFAULT_BLOCK,
    sigmas: tuple[float, ...] = DEFAULT_SIGMAS,
) -> dict[str, float]:
    """Every metric of one image, as metric name -> number."""
    program = find_convert()
    size = measured_size(program, image, crop)
    metrics = detail_metrics(program, image, sigmas, crop)
    metrics[patchiness_metric_name(block)] = block_patchiness(program, image, size, block, crop)
    return metrics


# --------------------------------------------------------------------------
# Bands
# --------------------------------------------------------------------------


def band_for(
    value: float,
    relative: float = DEFAULT_RELATIVE_TOLERANCE,
    absolute: float = DEFAULT_ABSOLUTE_TOLERANCE,
) -> dict[str, float]:
    """Blessed value +/- max(relative * value, absolute).

    Both knobs default from the measurement that motivated the fixture: the
    hidden refine pass moved detail energy at sigma 0.6 from 0.40 to 0.75 and
    block patchiness from 3.16 to 3.94, so a band has to stay far inside a
    ~25% relative shift to be worth running, while a floor of 0.08 absolute
    (0.05 half-width) keeps a metric that is essentially zero — a blurred
    stage, a flat-lay prompt — from failing on the last decimal of
    ``%[fx:mean*255]``.  A checkpoint or VAE float change moves these numbers by
    single-digit per cent, so 15% is the widest band that still catches the
    defect and the tightest one that does not cry wolf on a driver update.
    """
    if relative < 0 or absolute < 0:
        raise UsageError("Tolerances must not be negative.")
    margin = max(abs(value) * relative, absolute)
    return {"min": round(value - margin, 6), "max": round(value + margin, 6)}


def bands_for_metrics(
    metrics: dict[str, float], relative: float, absolute: float
) -> dict[str, dict[str, float]]:
    return {name: band_for(value, relative, absolute) for name, value in sorted(metrics.items())}


# --------------------------------------------------------------------------
# Baseline files
# --------------------------------------------------------------------------


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def safe_token(value: str, what: str) -> str:
    if not _TOKEN.fullmatch(value or ""):
        raise UsageError(
            f"{what} {value!r} must be a single safe token: letters, digits, '.', '_', '+' or '-'."
        )
    return value


def baseline_path(directory: Path, bundle: str, stage: str) -> Path:
    return directory / f"{safe_token(bundle, 'Bundle')}__{safe_token(stage, 'Stage')}.json"


def load_baseline(directory: Path, bundle: str, stage: str) -> dict:
    path = baseline_path(directory, bundle, stage)
    if not path.is_file():
        raise MissingBaseline(
            f"No blessed baseline for bundle {bundle!r} stage {stage!r} at {path}. "
            "Bless one first: scripts/texture_regression.py bless "
            f"--bundle {bundle} --stage {stage} --image <render.png>"
        )
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        raise TextureError(f"Baseline {path} is not valid JSON: {error}") from error
    if not isinstance(document, dict) or "metrics" not in document or "bands" not in document:
        raise TextureError(f"Baseline {path} is missing its metrics/bands sections.")
    return document


def baseline_parameters(
    document: dict,
) -> tuple[tuple[int, int, int, int] | None, int, tuple[float, ...]]:
    """The crop/block/sigma a baseline was blessed with."""
    parameters = document.get("parameters") or {}
    crop_text = parameters.get("crop")
    crop = parse_crop(crop_text) if crop_text else None
    block = int(parameters.get("block", DEFAULT_BLOCK))
    sigmas = tuple(float(value) for value in parameters.get("sigmas", DEFAULT_SIGMAS))
    return crop, block, sigmas


# --------------------------------------------------------------------------
# Subcommands
# --------------------------------------------------------------------------


def cmd_measure(args: argparse.Namespace) -> int:
    metrics = measure_image(Path(args.image), args.crop, args.block, args.sigmas)
    print(json.dumps(metrics, indent=2, sort_keys=True))
    return EXIT_OK


def cmd_bless(args: argparse.Namespace) -> int:
    image = Path(args.image)
    # Everything the caller could have mistyped is checked before measuring: the
    # bundle/stage tokens, the prompt file, then the image and its crop.
    path = baseline_path(Path(args.out), args.bundle, args.stage)
    prompt_sha256 = None
    if args.prompt_file is not None:
        prompt_path = Path(args.prompt_file)
        if not prompt_path.is_file():
            raise UsageError(f"No such prompt file: {prompt_path}")
        prompt_sha256 = sha256_file(prompt_path)

    program = find_convert()
    size = measured_size(program, image, args.crop)
    grid = block_grid(size, args.block)
    metrics = detail_metrics(program, image, args.sigmas, args.crop)
    metrics[patchiness_metric_name(args.block)] = block_patchiness(
        program, image, size, args.block, args.crop
    )

    document = {
        "schema_version": SCHEMA_VERSION,
        "bundle": args.bundle,
        "stage": args.stage,
        "created_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "seed": args.seed,
        "prompt_sha256": prompt_sha256,
        "image_sha256": sha256_file(image),
        "source_image": image.name,
        "imagemagick_version": imagemagick_version(),
        "parameters": {
            "crop": format_crop(args.crop) if args.crop else None,
            "measured_size": list(size),
            "block": args.block,
            "patch_grid": list(grid),
            "patch_sigma": PATCH_SIGMA,
            "sigmas": list(args.sigmas),
            "recipes": {
                "crop_stage": CROP_RECIPE,
                "detail_energy": DETAIL_RECIPE,
                "patchiness": PATCHINESS_RECIPE,
                "grid_derivation": (
                    "patch grid = ceil(crop width / block) x ceil(crop height / block), "
                    "downscale with !, upscale back to exactly the crop size with !, "
                    "blur 0x6, difference, mean*255"
                ),
            },
        },
        "tolerance": {"relative": args.relative_tolerance, "absolute": args.absolute_tolerance},
        "metrics": dict(sorted(metrics.items())),
        "bands": bands_for_metrics(metrics, args.relative_tolerance, args.absolute_tolerance),
    }

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(document, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    if args.json:
        print(json.dumps({"baseline": str(path), **document}, sort_keys=True))
    else:
        print(f"Blessed {len(metrics)} metrics for {args.bundle}/{args.stage} into {path}")
        for name, value in sorted(metrics.items()):
            band = document["bands"][name]
            print(
                f"  {name} = {format_number(value)} "
                f"band [{format_number(band['min'])}, {format_number(band['max'])}]"
            )
    return EXIT_OK


def cmd_check(args: argparse.Namespace) -> int:
    document = load_baseline(Path(args.baseline_dir), args.bundle, args.stage)
    crop, block, sigmas = baseline_parameters(document)
    overrides = []
    if args.crop is not None:
        crop = args.crop
        overrides.append(f"crop={format_crop(crop)}")
    if args.block is not None:
        block = args.block
        overrides.append(f"block={block}")
    if args.sigmas is not None:
        sigmas = args.sigmas
        overrides.append("sigmas=" + ",".join(format_sigma(value) for value in sigmas))
    if overrides:
        print(
            "WARNING: measuring with a parameter this baseline was not blessed with: "
            + " ".join(overrides),
            file=sys.stderr,
        )

    image = Path(args.image)
    metrics = measure_image(image, crop, block, sigmas)
    bands: dict[str, dict[str, float]] = document["bands"]
    blessed: dict[str, float] = document["metrics"]
    parameters = document.get("parameters") or {}
    digest = sha256_file(image)

    results: list[dict[str, object]] = []
    failures = 0

    def record_mismatch(name: str, value: float | None, message: str) -> None:
        nonlocal failures
        failures += 1
        results.append(
            {
                "metric": name,
                "status": "fail",
                "value": value,
                "blessed": blessed.get(name),
                "detail": message,
            }
        )
        if not args.json:
            print(f"FAIL {name}: {message}")

    for name in sorted(set(bands) | set(metrics)):
        if name not in metrics:
            # Metric names carry the sigma and block size, so this is the loud
            # version of "you compared a different measurement with the band".
            record_mismatch(
                name,
                None,
                f"blessed but not measured — the baseline parameters "
                f"{parameters.get('crop')!r}/block {parameters.get('block')}/"
                f"sigmas {parameters.get('sigmas')} measure different metrics",
            )
            continue
        if name not in bands:
            record_mismatch(
                name,
                metrics[name],
                f"measured but has no blessed band (blessed metrics: {', '.join(sorted(bands))})",
            )
            continue
        value = metrics[name]
        band = bands[name]
        reference = float(blessed.get(name, band["min"]))
        delta = round(value - reference, 6)
        outside = value < band["min"] or value > band["max"]
        distance = value - band["max"] if value > band["max"] else band["min"] - value
        detail = f"blessed {format_number(reference)} delta {delta:+.6g}"
        if outside:
            failures += 1
            detail += f", {format_number(max(distance, 0.0))} outside the band"
        results.append(
            {
                "metric": name,
                "status": "fail" if outside else "pass",
                "value": value,
                "min": band["min"],
                "max": band["max"],
                "blessed": reference,
                "delta": delta,
                "detail": detail,
            }
        )
        if not args.json:
            print(
                f"{'FAIL' if outside else 'PASS'} {name} {format_number(value)} "
                f"band [{format_number(band['min'])}, {format_number(band['max'])}] {detail}"
            )

    if args.json:
        print(
            json.dumps(
                {
                    "bundle": args.bundle,
                    "stage": args.stage,
                    "image": str(image),
                    "image_sha256": digest,
                    "ok": failures == 0,
                    "failures": failures,
                    "results": results,
                },
                sort_keys=True,
            )
        )
    else:
        verdict = (
            f"all {len(results)} metrics are inside the blessed band"
            if failures == 0
            else f"{failures} of {len(results)} metrics left the blessed band"
        )
        print(f"{args.bundle}/{args.stage}: {verdict} ({image.name}, sha256 {digest[:12]}…)")
    return EXIT_OK if failures == 0 else EXIT_BAND_FAILURE


# --------------------------------------------------------------------------
# Command line
# --------------------------------------------------------------------------


class _Parser(argparse.ArgumentParser):
    def error(self, message: str) -> None:  # pragma: no cover - argparse hook
        raise UsageError(f"{self.format_usage().strip()}\n{message}")


def _add_measure_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--image", required=True, help="Rendered stage image to measure.")
    parser.add_argument(
        "--crop",
        type=parse_crop,
        default=None,
        metavar="WxH+X+Y",
        help="Matched-scale crop; metrics are measured inside it (default: the whole image).",
    )
    parser.add_argument(
        "--block",
        type=int,
        default=DEFAULT_BLOCK,
        metavar="N",
        help=f"Block edge in pixels for the patchiness metric (default {DEFAULT_BLOCK}).",
    )
    parser.add_argument(
        "--sigmas",
        type=parse_sigmas,
        default=DEFAULT_SIGMAS,
        metavar="LIST",
        help="Comma-separated blur scales for detail energy "
        f"(default {','.join(format_sigma(value) for value in DEFAULT_SIGMAS)}).",
    )


def build_parser() -> argparse.ArgumentParser:
    parser = _Parser(
        description=__doc__.splitlines()[0],
        epilog=(
            "Exit codes: 0 inside every band, 1 a metric left its band, "
            "2 no blessed baseline for that bundle/stage, 3 bad arguments or image."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    measure = subparsers.add_parser(
        "measure",
        help="Print the metrics of one image as sorted JSON.",
        description="Measure matched-scale detail energy and block-scale patchiness.",
    )
    _add_measure_arguments(measure)
    measure.set_defaults(func=cmd_measure)

    bless = subparsers.add_parser(
        "bless",
        help="Write the baseline band for a bundle stage.",
        description="Measure an image and bless it as the baseline for a bundle stage.",
    )
    _add_measure_arguments(bless)
    bless.add_argument("--bundle", required=True, metavar="ID", help="Publication/bundle id.")
    bless.add_argument(
        "--stage", required=True, metavar="NAME", help="Declared output/stage id, e.g. final."
    )
    bless.add_argument("--seed", type=parse_seed, default=None, help="Seed the render used.")
    bless.add_argument(
        "--prompt-file",
        default=None,
        metavar="FILE",
        help="File holding the prompt the render used; only its sha256 is stored.",
    )
    bless.add_argument(
        "--out",
        default=str(BASELINE_ROOT),
        metavar="DIR",
        help=f"Baseline directory (default scripts/{BASELINE_ROOT.name}).",
    )
    bless.add_argument(
        "--relative-tolerance",
        type=float,
        default=DEFAULT_RELATIVE_TOLERANCE,
        metavar="FRACTION",
        help=f"Band half-width as a fraction of the value (default {DEFAULT_RELATIVE_TOLERANCE}).",
    )
    bless.add_argument(
        "--absolute-tolerance",
        type=float,
        default=DEFAULT_ABSOLUTE_TOLERANCE,
        metavar="VALUE",
        help=f"Minimum band half-width (default {DEFAULT_ABSOLUTE_TOLERANCE}).",
    )
    bless.add_argument("--json", action="store_true", help="Print the whole baseline document.")
    bless.set_defaults(func=cmd_bless)

    check = subparsers.add_parser(
        "check",
        help="Compare an image against the blessed band for a bundle stage.",
        description="Re-measure an image and compare it with the blessed band.",
    )
    check.add_argument("--image", required=True, help="Rendered stage image to measure.")
    check.add_argument("--bundle", required=True, metavar="ID", help="Publication/bundle id.")
    check.add_argument(
        "--stage", required=True, metavar="NAME", help="Declared output/stage id, e.g. final."
    )
    check.add_argument(
        "--baseline-dir",
        default=str(BASELINE_ROOT),
        metavar="DIR",
        help=f"Baseline directory (default scripts/{BASELINE_ROOT.name}).",
    )
    check.add_argument(
        "--crop",
        type=parse_crop,
        default=None,
        metavar="WxH+X+Y",
        help="Override the blessed crop (measured from the baseline by default).",
    )
    check.add_argument(
        "--block", type=int, default=None, metavar="N", help="Override the blessed block size."
    )
    check.add_argument(
        "--sigmas",
        type=parse_sigmas,
        default=None,
        metavar="LIST",
        help="Override the blessed sigmas.",
    )
    check.add_argument("--json", action="store_true", help="Print a machine-readable report.")
    check.set_defaults(func=cmd_check)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    try:
        args = parser.parse_args(argv)
        return int(args.func(args))
    except TextureError as error:
        print(f"texture_regression: error: {error}", file=sys.stderr)
        return error.exit_code


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
