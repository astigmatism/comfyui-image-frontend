# Texture regression baselines

One committed JSON per bundle stage: the blessed detail-energy and block-patchiness
numbers a rendered stage image of that publication has to stay inside. Baselines are
produced by [`../texture_regression.py`](../texture_regression.py) and reviewed like a
screenshot — a person looks at the render, agrees it is the texture they want, and
re-blesses it. Nothing updates a baseline silently.

This directory intentionally contains **no** baselines yet. A baseline is a measurement
of a production render, so one may only be committed together with the seed, prompt,
crop and the generation that produced it. Never commit a number invented from a
synthetic image.

## What is measured

Both metrics are one `convert` call each, on the grayscale version of the measured
region, reported on the 0–255 scale. `<image>` carries the blessed crop stage
`-crop <WxH+X+Y> +repage` first when the baseline declares a crop; a baseline without a
crop measures the whole file.

Detail energy at blur scale `<sigma>` (default scales 0.6, 1.2, 2.5 — texture that a
denoise pass smooths away shows up as a drop, texture it invents as a rise):

```sh
convert "$img" -colorspace gray \( +clone -blur 0x$sigma \) \
  -compose difference -composite -format '%[fx:mean*255]' info:
```

Block-scale patchiness, the mosaic signal. The image is reduced to one sample per block
and enlarged back to exactly the measured size, so only structure that lives on the
block grid survives; the difference against its own block-scale blur is the patchiness:

```sh
convert "$img" -colorspace gray -resize ${gw}x${gh}! -resize ${w}x${h}! \
  \( +clone -blur 0x6 \) -compose difference -composite -format '%[fx:mean*255]' info:
```

The patch grid is derived, never typed: `gw = ceil(w / block)`, `gh = ceil(h / block)`,
with `w x h` the measured size (the crop, or the whole image) and `block` the blessed
block size, default 8. For the 1160x1160 matched crop used during the invisible-refine
investigation that is 145x145, which is the command the recorded numbers came from. Both
resizes carry `!` because a non-square crop's aspect never matches its own block grid;
for a square crop that is the same resize the recorded command performed. The recorded
crop, block size, sigmas, derived grid, ImageMagick version and image hash are all in the
baseline file, so a baseline can be re-measured exactly.

Bands are `blessed value ± max(15% of the value, 0.05)`, both knobs overridable at bless
time. The defect this fixture exists for moved block patchiness 3.16 → 3.94 and detail
energy at sigma 0.6 from 0.40 to 0.75, so the band has to be far tighter than a 25% move
while still absorbing a checkpoint quantisation or VAE float change, which moves these
numbers by a few per cent.

## The baseline file

`bless` writes one JSON document per bundle stage: two-space indent, sorted keys, a
trailing newline. The numbers below are illustrative — this directory has no committed
baseline yet — but the keys are exactly what the file holds:

```json
{
  "schema_version": "comfyui-image-frontend.texture-baseline/v1",
  "bundle": "moody-krea2-advanced-v1",
  "stage": "refine",
  "created_at": "2026-11-27T18:41:07Z",
  "seed": 1125899906842624,
  "prompt_sha256": "6f1c…",
  "image_sha256": "b2d4…",
  "source_image": "refine.png",
  "imagemagick_version": "ImageMagick 6.9.12-…",
  "parameters": {
    "crop": "1160x1160+0+0",
    "measured_size": [1160, 1160],
    "block": 8,
    "patch_grid": [145, 145],
    "patch_sigma": 6.0,
    "sigmas": [0.6, 1.2, 2.5],
    "recipes": { "crop_stage": "…", "detail_energy": "…", "patchiness": "…",
                 "grid_derivation": "…" }
  },
  "tolerance": { "relative": 0.15, "absolute": 0.05 },
  "metrics": { "detail_energy_sigma_0.6": 0.4, "patchiness_block8": 3.16 },
  "bands": { "detail_energy_sigma_0.6": { "min": 0.34, "max": 0.46 },
             "patchiness_block8": { "min": 2.686, "max": 3.634 } }
}
```

Three of those keys decide the verdict — `metrics` is the number the printed delta is
measured against, `bands` is the verdict, and `parameters.crop` / `block` / `sigmas` are
the geometry the measurement is repeated on. The rest is provenance for whoever reads the
diff: `created_at` is UTC to the second, `prompt_sha256` is the hash of the
`--prompt-file` and never its text, `seed` is `null` when `--seed` was not passed, and
`source_image` is a basename, so a baseline contains no path and no prompt. A baseline
whose seed and prompt are missing can still be checked; it just cannot be reviewed,
because nobody can say which render the numbers came from.

Metric names carry their own parameters, `detail_energy_sigma_<sigma>` and
`patchiness_block<n>`, which is what makes an overridden block size or sigma fail loudly
instead of silently comparing two different measurements: the blessed `patchiness_block8`
band comes back "blessed but not measured", the freshly named metric comes back "measured
but has no blessed band", and the run exits 1. Overriding `--crop` alone keeps the names
and measures a different region, so it prints `WARNING: measuring with a parameter this
baseline was not blessed with` on stderr — that warning is the only guard, which is why
the crop is decided once, at bless time.

## Workflow

The tool never submits a generation. On the production host a generation must come from
the application's own authenticated API, so submission stays with the application and this
fixture only ever measures a PNG an operator points it at.

1. Generate the bundle's fixed seed and prompt through the application, on the runtime
   the bundle is published for, and download the stage image you want to guard.
2. Decide the matched-scale crop once. Different crops measure different images: the
   crop is stored in the baseline and `check` reuses it.
3. Bless it:

   ```sh
   scripts/texture_regression.py bless \
     --bundle moody-krea2-advanced-v1 --stage refine \
     --image refine.png --crop 1160x1160+0+0 --block 8 \
     --seed 1125899906842624 --prompt-file prompt.txt
   ```

4. Open the render next to the numbers. A baseline is a human decision, not a build
   artifact: commit it only with the render you looked at.
5. From then on a republish is checked:

   ```sh
   scripts/texture_regression.py check \
     --bundle moody-krea2-advanced-v1 --stage refine --image refine.png
   ```

`check` prints one line per metric with the blessed value, the band and the delta, and
exits non-zero naming every metric that left its band. A baseline that does not exist is
a different failure and a different exit code, so "never blessed" can never be read as
"passed".

`measure` runs the same measurements and prints them as sorted JSON without reading or
writing a baseline, which is what you want while deciding whether a crop is the right
one: until a person has looked at the render, a number is not a fact about the bundle.
`bless --json` prints the whole document it wrote and `check --json` prints one result
object per metric plus the sha256 of the image it measured, for a caller that would
rather parse than grep. `--out` (bless) and `--baseline-dir` (check) move the directory
both subcommands read and write; each defaults to this one.

The fixture's own regressions are `../tests/test_texture_regression.py`: they pin the two
blessed recipes and the patch grid derived from them, the band arithmetic that has to
catch the recorded shift while absorbing a few per cent of checkpoint drift, a
bless/check round-trip, and the separate exit code for a stage that was never blessed.
They run under `make test-deployment` and skip themselves when `convert` is not installed.

## Exit codes

| Code | Meaning |
|---:|---|
| 0 | every metric is inside its band |
| 1 | at least one metric left its band (a red build) |
| 2 | no blessed baseline for that bundle/stage — bless one |
| 3 | bad arguments, an unreadable image, or an ImageMagick failure |

## Re-blessing

A baseline is only worth as much as the discipline around it, and this fixture's failure
mode is specific: the numbers move because the pixels moved, and a re-bless makes the red
build green whether that is news about a new checkpoint or news about a defect.

Re-blessing is honest when someone opened the render that produced the new numbers,
agreed its texture is the texture the bundle should produce, and the commit says what
changed — a workflow edit meant to change detail, a new checkpoint revision, a crop that
moved onto the region the defect was hiding in. The commit names the render, and usually
the seed, so the next reader can repeat the measurement instead of trusting the diff.

Re-blessing hides a regression when the only thing that changed is that `check` went red:
the render was not opened, or was opened and nobody can say which of the two numbers
moved or why. It hides it twice over when the same commit widens `--relative-tolerance`
or `--absolute-tolerance`, because the band stops being a statement about a render and
becomes a statement about whatever image happened to be measured last — the default 15%
band already absorbs a checkpoint quantisation, so a tolerance that had to grow is the
thing to question, not the render. Two more tells: re-blessing a stage whose
`image_sha256` did not change, and re-blessing every baseline after a republish that
touched one stage.

Nothing here is enforced by the tool, deliberately: `check` never writes to this
directory, so every change to a baseline is a person deciding, and `bless` records the
image hash, the ImageMagick version and the parameters it measured with so that decision
can be audited afterwards.

## Layout

```text
<bundle>__<stage>.json    # one baseline: metrics, bands, parameters, provenance
README.md                 # this file
```

Bundle and stage are single safe tokens — a letter or digit, then letters, digits, `.`,
`_`, `+` or `-` — because they become the filename. Stage is the publication's declared
output id — `final`, or the id of an earlier stage whose texture matters.
