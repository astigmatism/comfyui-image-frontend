"""Metric, band and CLI tests for the texture regression fixture.

Only ImageMagick and a temporary directory are used; the classes that measure
skip on a machine without ``convert`` so
``python3 -m unittest discover -s scripts/tests -q`` stays green there.
"""

# ruff: noqa: S603

import contextlib
import importlib.util
import io
import json
import math
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

spec = importlib.util.spec_from_file_location(
    "texture_regression", Path(__file__).resolve().parents[1] / "texture_regression.py"
)
texture = importlib.util.module_from_spec(spec)
spec.loader.exec_module(texture)

CONVERT = shutil.which("convert")
HAS_IMAGEMAGICK = CONVERT is not None
NO_IMAGEMAGICK = "ImageMagick `convert` is not installed"
BUNDLE = "moody-krea2-advanced-v1"

# A smooth gradient, and the same gradient carrying 8px alternating blocks: the
# synthetic stand-in for the faceted skin a hard-denoise refine pass paints.
GRADIENT = ("-size", "64x64", "gradient:white-black")
MOSAIC = (
    "(",
    "-size",
    "64x64",
    "gradient:white-black",
    ")",
    "(",
    "-size",
    "8x8",
    "xc:gray50",
    "+noise",
    "Random",
    "-resize",
    "64x64!",
    ")",
    "-compose",
    "plus",
    "-composite",
)


def magick(*args: str) -> None:
    completed = subprocess.run([CONVERT, *args], capture_output=True, text=True, check=False)
    if completed.returncode != 0:
        raise RuntimeError(f"convert {' '.join(args)} failed: {completed.stderr.strip()}")


def run_cli(argv: list[str]) -> tuple[int, str, str]:
    """Invoke the CLI in-process and capture what it printed."""
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = texture.main(argv)
    return code, out.getvalue(), err.getvalue()


def bless_baseline(baselines: Path, image: Path, stage: str, *extra: str) -> Path:
    argv = [
        "bless",
        "--bundle",
        BUNDLE,
        "--stage",
        stage,
        "--image",
        str(image),
        "--out",
        str(baselines),
        "--sigmas",
        "0.6",
        *extra,
    ]
    code, output, error = run_cli(argv)
    assert code == 0, output + error
    return baselines / f"{BUNDLE}__{stage}.json"


def check_baseline(baselines: Path, stage: str, image: Path, *extra: str) -> tuple[int, str, str]:
    return run_cli(
        [
            "check",
            "--bundle",
            BUNDLE,
            "--stage",
            stage,
            "--image",
            str(image),
            "--baseline-dir",
            str(baselines),
            *extra,
        ]
    )


class ArgumentTests(unittest.TestCase):
    """Geometry derivation, band arithmetic and argv lists need no ImageMagick."""

    def test_detail_energy_recipe_is_the_blessed_command(self):
        self.assertEqual(
            texture.detail_energy_arguments("convert", Path("stage.png"), 0.6),
            [
                "convert",
                "stage.png",
                "-colorspace",
                "gray",
                "(",
                "+clone",
                "-blur",
                "0x0.6",
                ")",
                "-compose",
                "difference",
                "-composite",
                "-format",
                "%[fx:mean*255]",
                "info:",
            ],
        )

    def test_patchiness_recipe_is_the_blessed_command_for_the_matched_crop(self):
        # The recorded mosaic investigation used an 1160x1160 matched crop with 8x8
        # blocks, i.e. a 145x145 patch grid; the derived argv must be that command.
        self.assertEqual(texture.block_grid((1160, 1160), 8), (145, 145))
        self.assertEqual(
            texture.patchiness_arguments("convert", Path("stage.png"), (145, 145), (1160, 1160)),
            [
                "convert",
                "stage.png",
                "-colorspace",
                "gray",
                "-resize",
                "145x145!",
                "-resize",
                "1160x1160!",
                "(",
                "+clone",
                "-blur",
                "0x6",
                ")",
                "-compose",
                "difference",
                "-composite",
                "-format",
                "%[fx:mean*255]",
                "info:",
            ],
        )

    def test_block_grid_rounds_up_from_the_measured_geometry(self):
        self.assertEqual(texture.block_grid((100, 60), 8), (13, 8))
        self.assertEqual(texture.block_grid((100, 60), 16), (7, 4))
        # A crop is what gets measured, so the grid follows the crop, not the file.
        self.assertEqual(texture.block_grid(texture.parse_crop("40x30+10+20")[:2], 8), (5, 4))

    def test_crop_stage_precedes_the_grayscale_conversion(self):
        args = texture.detail_energy_arguments(
            "convert", Path("stage.png"), 1.2, texture.parse_crop("40x30+10+20")
        )
        crop_at = args.index("-crop")
        self.assertEqual(args[crop_at : crop_at + 3], ["-crop", "40x30+10+20", "+repage"])
        self.assertLess(crop_at, args.index("-colorspace"))
        self.assertEqual(args[0], "convert")
        self.assertNotIn("", args)

    def test_band_is_relative_with_an_absolute_floor(self):
        wide = texture.band_for(3.16)
        self.assertAlmostEqual(wide["min"], 3.16 - 0.474, places=6)
        self.assertAlmostEqual(wide["max"], 3.16 + 0.474, places=6)
        tiny = texture.band_for(0.02)
        self.assertAlmostEqual(tiny["min"], 0.02 - 0.05, places=6)
        self.assertAlmostEqual(tiny["max"], 0.02 + 0.05, places=6)

    def test_band_catches_the_recorded_mosaic_shift_and_survives_a_small_drift(self):
        # The defect moved block patchiness 3.16 -> 3.94 and detail energy at
        # sigma 0.6 from 0.40 to 0.75, so both must fall outside a band blessed at
        # the healthy value, while a few per cent of checkpoint or VAE drift may not.
        self.assertGreater(3.94, texture.band_for(3.16)["max"])
        self.assertGreater(0.75, texture.band_for(0.40)["max"])
        self.assertLess(3.16 * 1.05, texture.band_for(3.16)["max"])
        self.assertGreater(0.40 * 0.95, texture.band_for(0.40)["min"])

    def test_band_knobs_are_overridable(self):
        tight = texture.band_for(3.16, 0.02, 0.001)
        self.assertAlmostEqual(tight["max"], 3.16 + 0.0632, places=6)
        self.assertLess(tight["max"], texture.band_for(3.16)["max"])
        with self.assertRaises(texture.UsageError):
            texture.band_for(1.0, -0.1, 0.05)

    def test_crop_and_sigma_parsing(self):
        self.assertEqual(texture.parse_crop("1160x1160+0+0"), (1160, 1160, 0, 0))
        self.assertEqual(texture.format_crop((1160, 1160, 0, 0)), "1160x1160+0+0")
        self.assertEqual(texture.parse_sigmas("0.6,1.2,2.5"), (0.6, 1.2, 2.5))
        for bad in ["", "8x", "0x64+0", "64x64+0+0+0"]:
            with self.subTest(bad=bad), self.assertRaises(texture.UsageError):
                texture.parse_crop(bad)
        for bad in ["", "0", "nope", "1.2,1.2"]:
            with self.subTest(bad=bad), self.assertRaises(texture.UsageError):
                texture.parse_sigmas(bad)

    def test_unsafe_bundle_and_stage_names_are_refused(self):
        for bad in ["../evil", "a/b", "", "with space"]:
            with self.subTest(bad=bad), self.assertRaises(texture.UsageError):
                texture.baseline_path(Path("/baselines"), bad, "final")
        self.assertEqual(
            texture.baseline_path(Path("/b"), BUNDLE, "final"),
            Path(f"/b/{BUNDLE}__final.json"),
        )

    def test_convert_lookup_names_the_missing_package(self):
        with (
            patch.object(texture.shutil, "which", return_value=None),
            self.assertRaises(texture.TextureError) as caught,
        ):
            texture.find_convert()
        self.assertIn("not on PATH", str(caught.exception))
        self.assertIn("convert", str(caught.exception))


@unittest.skipUnless(HAS_IMAGEMAGICK, NO_IMAGEMAGICK)
class MetricTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.workspace = tempfile.TemporaryDirectory(prefix="texture-fixture-")
        root = Path(cls.workspace.name)
        cls.gradient = root / "gradient.png"
        cls.texture_image = root / "texture.png"
        cls.blurred = root / "texture_blur.png"
        cls.mosaic = root / "mosaic.png"
        cls.wide = root / "wide.png"
        cls.square = root / "square.png"
        magick(*GRADIENT, str(cls.gradient))
        magick("-size", "64x64", "xc:gray50", "+noise", "Random", str(cls.texture_image))
        magick(str(cls.texture_image), "-blur", "0x3", str(cls.blurred))
        magick(*MOSAIC, str(cls.mosaic))
        magick("-size", "100x60", "xc:gray50", "+noise", "Random", str(cls.wide))
        magick("-size", "116x116", "xc:gray50", "+noise", "Random", str(cls.square))

    @classmethod
    def tearDownClass(cls):
        cls.workspace.cleanup()

    def test_a_blocky_image_measures_more_patchy_than_a_gradient(self):
        metric = texture.patchiness_metric_name(8)
        gradient = texture.measure_image(self.gradient, sigmas=())[metric]
        mosaic = texture.measure_image(self.mosaic, sigmas=())[metric]
        self.assertGreater(mosaic, gradient * 1.5, f"mosaic {mosaic} vs gradient {gradient}")

    def test_blurring_collapses_detail_energy(self):
        program = texture.find_convert()
        sharp = texture.detail_metrics(program, self.texture_image, texture.DEFAULT_SIGMAS)
        soft = texture.detail_metrics(program, self.blurred, texture.DEFAULT_SIGMAS)
        for sigma in texture.DEFAULT_SIGMAS:
            name = texture.detail_metric_name(sigma)
            with self.subTest(sigma=sigma):
                self.assertGreater(
                    sharp[name], soft[name] * 4, f"{name}: {sharp[name]} vs {soft[name]}"
                )

    def test_metric_names_are_stable_and_values_are_finite(self):
        metrics = texture.measure_image(self.mosaic)
        self.assertEqual(
            list(metrics),
            [
                "detail_energy_sigma_0.6",
                "detail_energy_sigma_1.2",
                "detail_energy_sigma_2.5",
                "patchiness_block8",
            ],
        )
        for value in metrics.values():
            self.assertTrue(math.isfinite(value))
            self.assertGreaterEqual(value, 0.0)

    def test_derived_command_reproduces_the_documented_recipe(self):
        # The documented recipe typed literally (a square matched crop and its own
        # block grid: 116x116 with 8x8 blocks is a 15x15 patch grid), against the
        # argv this module derives.  Adding `!` to the downscale is what makes the
        # derivation correct for a non-square crop; on a square crop it must be a
        # no-op, and the recorded 1160x1160 -> 145x145 numbers are unchanged.
        documented = [
            CONVERT,
            str(self.square),
            "-colorspace",
            "gray",
            "-resize",
            "15x15",
            "-resize",
            "116x116!",
            "(",
            "+clone",
            "-blur",
            "0x6",
            ")",
            "-compose",
            "difference",
            "-composite",
            "-format",
            "%[fx:mean*255]",
            "info:",
        ]
        derived = texture.patchiness_arguments(
            CONVERT, self.square, texture.block_grid((116, 116), 8), (116, 116)
        )
        reference = float(texture.run_convert(documented).strip())
        generated = float(texture.run_convert(derived).strip())
        measured = texture.measure_image(self.square, sigmas=())["patchiness_block8"]
        self.assertAlmostEqual(reference, generated, places=6)
        self.assertAlmostEqual(measured, generated, places=6)

    def test_crop_and_block_size_change_the_numbers(self):
        program = texture.find_convert()
        crop = texture.parse_crop("40x30+10+20")
        whole = texture.block_patchiness(program, self.wide, (100, 60))
        cropped = texture.block_patchiness(program, self.wide, (40, 30), crop=crop)
        fine = texture.block_patchiness(program, self.wide, (100, 60), 4)
        coarse = texture.block_patchiness(program, self.wide, (100, 60), 16)
        self.assertNotEqual(whole, cropped)
        self.assertNotEqual(whole, fine)
        self.assertNotEqual(fine, coarse)
        metrics = texture.measure_image(self.wide, block=4, sigmas=())
        self.assertIn("patchiness_block4", metrics)
        self.assertNotIn("patchiness_block8", metrics)

    def test_subprocess_arguments_never_go_through_a_shell(self):
        calls: list[tuple[tuple, dict]] = []
        real = subprocess.run

        def record(*args, **kwargs):
            calls.append((args, kwargs))
            return real(*args, **kwargs)

        with patch.object(texture.subprocess, "run", side_effect=record):
            texture.measure_image(self.gradient, sigmas=(0.6,))
        self.assertEqual(len(calls), 3)  # geometry, one detail scale, patchiness
        for args, kwargs in calls:
            argv = args[0]
            self.assertEqual(argv[0], CONVERT)
            self.assertFalse(kwargs.get("shell", False))
            self.assertNotIn("-c", argv)


@unittest.skipUnless(HAS_IMAGEMAGICK, NO_IMAGEMAGICK)
class CommandLineTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.workspace = tempfile.TemporaryDirectory(prefix="texture-cli-")
        root = Path(cls.workspace.name)
        cls.baselines = root / "baselines"
        cls.prompt = root / "prompt.txt"
        cls.prompt.write_text("macro of skin and jewellery\n", encoding="utf-8")
        cls.clean = root / "base.png"
        cls.patched = root / "refine.png"
        cls.wide = root / "wide.png"
        magick(*GRADIENT, str(cls.clean))
        magick(*MOSAIC, str(cls.patched))
        magick("-size", "100x60", "xc:gray50", "+noise", "Random", str(cls.wide))
        bless_baseline(cls.baselines, cls.clean, "base")
        bless_baseline(cls.baselines, cls.wide, "crop", "--crop", "40x30+10+20")

    @classmethod
    def tearDownClass(cls):
        cls.workspace.cleanup()

    def test_measure_prints_sorted_json(self):
        code, output, error = run_cli(["measure", "--image", str(self.clean), "--sigmas", "0.6"])
        self.assertEqual(code, 0, error)
        metrics = json.loads(output)
        self.assertEqual(list(metrics), ["detail_energy_sigma_0.6", "patchiness_block8"])
        self.assertEqual(output, json.dumps(metrics, indent=2, sort_keys=True) + "\n")
        for value in metrics.values():
            self.assertIsInstance(value, float)

    def test_bless_records_the_recipe_inputs_and_check_passes(self):
        path = bless_baseline(
            self.baselines,
            self.clean,
            "recorded",
            "--seed",
            "1125899906842624",
            "--prompt-file",
            str(self.prompt),
        )
        document = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(document["bundle"], BUNDLE)
        self.assertEqual(document["stage"], "recorded")
        self.assertEqual(document["seed"], 1125899906842624)
        self.assertEqual(len(document["prompt_sha256"]), 64)
        self.assertEqual(len(document["image_sha256"]), 64)
        self.assertIn("ImageMagick 6", document["imagemagick_version"])
        self.assertRegex(document["created_at"], r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ$")
        parameters = document["parameters"]
        self.assertEqual(parameters["block"], texture.DEFAULT_BLOCK)
        self.assertEqual(parameters["patch_sigma"], texture.PATCH_SIGMA)
        self.assertEqual(parameters["crop"], None)
        self.assertEqual(parameters["sigmas"], [0.6])
        self.assertEqual(parameters["measured_size"], [64, 64])
        self.assertEqual(parameters["patch_grid"], [8, 8])
        self.assertEqual(
            set(parameters["recipes"]),
            {"crop_stage", "detail_energy", "patchiness", "grid_derivation"},
        )
        self.assertEqual(document["tolerance"], {"relative": 0.15, "absolute": 0.05})
        for name, band in document["bands"].items():
            self.assertEqual(band, texture.band_for(document["metrics"][name], 0.15, 0.05), name)

        code, output, error = check_baseline(self.baselines, "base", self.clean)
        self.assertEqual(code, 0, output + error)
        self.assertIn("PASS patchiness_block8", output)
        self.assertEqual(error.strip(), "")

    def test_blessed_crop_and_block_are_recorded(self):
        path = bless_baseline(
            self.baselines, self.wide, "crop16", "--crop", "40x30+10+20", "--block", "16"
        )
        parameters = json.loads(path.read_text(encoding="utf-8"))["parameters"]
        self.assertEqual(parameters["crop"], "40x30+10+20")
        self.assertEqual(parameters["measured_size"], [40, 30])
        self.assertEqual(parameters["block"], 16)
        self.assertEqual(parameters["patch_grid"], [3, 2])

    def test_check_fails_on_a_patched_variant_and_names_the_metric(self):
        # "base" is the clean gradient, blessed once for the class in setUpClass.
        code, output, error = check_baseline(self.baselines, "base", self.patched)
        self.assertEqual(code, texture.EXIT_BAND_FAILURE)
        self.assertIn("FAIL patchiness_block8", output)
        self.assertIn("outside the band", output)
        self.assertEqual(error.strip(), "")

    def test_check_json_reports_machine_readable_results(self):
        code, output, _ = check_baseline(self.baselines, "base", self.patched, "--json")
        self.assertEqual(code, texture.EXIT_BAND_FAILURE)
        report = json.loads(output)
        self.assertFalse(report["ok"])
        self.assertEqual(report["bundle"], BUNDLE)
        self.assertGreaterEqual(report["failures"], 1)
        patchiness = next(row for row in report["results"] if row["metric"] == "patchiness_block8")
        self.assertEqual(patchiness["status"], "fail")
        self.assertGreater(patchiness["value"], patchiness["max"])
        self.assertGreater(patchiness["delta"], 0.0)
        self.assertEqual(len(report["image_sha256"]), 64)

    def test_check_reuses_the_blessed_crop(self):
        # "crop" was blessed with --crop 40x30+10+20; the check passes no crop.
        code, output, _ = check_baseline(self.baselines, "crop", self.wide, "--json")
        self.assertEqual(code, 0, output)
        report = json.loads(output)
        row = next(item for item in report["results"] if item["metric"] == "patchiness_block8")
        program = texture.find_convert()
        blessed = texture.block_patchiness(
            program, self.wide, (40, 30), crop=texture.parse_crop("40x30+10+20")
        )
        whole = texture.block_patchiness(program, self.wide, (100, 60))
        self.assertAlmostEqual(row["value"], blessed, places=6)
        self.assertNotAlmostEqual(row["value"], whole, places=3)

    def test_check_warns_when_parameters_are_overridden(self):
        # "base" was blessed at the default block size, so asking for 16 has to say
        # so on stderr and refuse rather than compare two different metrics.
        code, output, error = check_baseline(self.baselines, "base", self.wide, "--block", "16")
        self.assertEqual(code, texture.EXIT_BAND_FAILURE)
        self.assertIn("not blessed with", error)
        self.assertIn("block=16", error)
        self.assertIn("no blessed band", output)
        self.assertIn("blessed but not measured", output)

    def test_missing_baseline_has_its_own_exit_code(self):
        code, _, error = run_cli(
            [
                "check",
                "--bundle",
                "unknown-bundle",
                "--stage",
                "final",
                "--image",
                str(self.clean),
                "--baseline-dir",
                str(self.baselines / "empty"),
            ]
        )
        self.assertEqual(code, texture.EXIT_NO_BASELINE)
        self.assertNotEqual(code, texture.EXIT_BAND_FAILURE)
        self.assertIn("No blessed baseline for bundle 'unknown-bundle' stage 'final'", error)
        self.assertIn("bless", error)

    def test_bad_input_exits_three_with_a_readable_message(self):
        cases = [
            (["measure", "--image", str(self.clean / "nope.png")], "No such file"),
            (["measure", "--image", str(self.prompt)], "could not be decoded as an image"),
            (
                ["measure", "--image", str(self.wide), "--crop", "400x300+0+0"],
                "falls outside the 100x60 image",
            ),
            (["measure", "--image", str(self.wide), "--crop", "bogus"], "expected WxH+X+Y"),
            (
                ["measure", "--image", str(self.wide), "--sigmas", "0.6", "--block", "0"],
                "Block size",
            ),
            (["measure", "--image", str(self.wide), "--sigmas", "nope"], "Invalid sigma"),
            (["bless", "--stage", "final", "--image", str(self.clean)], "required"),
            (
                ["bless", "--bundle", "../evil", "--stage", "s", "--image", str(self.clean)],
                "safe token",
            ),
            (
                [
                    "bless",
                    "--bundle",
                    "b",
                    "--stage",
                    "s",
                    "--image",
                    str(self.clean),
                    "--seed",
                    "-1",
                ],
                "Seed",
            ),
            (
                [
                    "bless",
                    "--bundle",
                    "b",
                    "--stage",
                    "s",
                    "--image",
                    str(self.clean),
                    "--prompt-file",
                    str(self.clean / "missing.txt"),
                ],
                "No such prompt file",
            ),
        ]
        for argv, expected in cases:
            with self.subTest(argv=argv):
                code, _, error = run_cli(argv)
                self.assertEqual(code, texture.EXIT_ERROR, error)
                self.assertIn(expected, error)

    def test_a_missing_imagemagick_is_reported_and_not_raised(self):
        def missing() -> str:
            raise texture.TextureError("ImageMagick `convert` is not on PATH")

        with patch.object(texture, "find_convert", side_effect=missing):
            code, _, error = run_cli(["measure", "--image", str(self.clean)])
        self.assertEqual(code, texture.EXIT_ERROR)
        self.assertIn("not on PATH", error)


if __name__ == "__main__":
    unittest.main()
