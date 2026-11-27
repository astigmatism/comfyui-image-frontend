# CIF artifact cleanup companion

Copy or symlink `cif_artifact_cleanup` into the `custom_nodes` directory of every ComfyUI runtime
used by the image frontend, then restart that runtime. It adds two server-only routes:

```text
POST /comfyui-image-frontend/artifacts/delete
GET  /comfyui-image-frontend/artifacts/retention
```

The delete route accepts only bounded `filename` / `subfolder` / `type` locators for ComfyUI
`output` and `temp` storage. It resolves every path beneath ComfyUI's configured storage roots,
treats an already-missing file as success, and removes empty artifact subdirectories. It does not
register nodes or expose arbitrary filesystem paths.

## Stage retention (debug switch)

Every stage of a generation is written to its own subfolder — `comfyui-image-frontend/<source>/
first_pass/`, `…/color_matched/`, `…/sd_upscaled/`, `…/final/` — and every one of those files is
deleted seconds after the run finished, so "which stage produced this defect?" cannot be answered
after the fact. Set `CIF_RETAIN_STAGES` in the environment of the ComfyUI process to keep them
instead:

| Value | Behaviour |
| --- | --- |
| unset, empty, `off`, `0`, `false`, `none` | Default: delete, exactly as before the switch existed. |
| `last` | Keep the newest generation; same count as `1`, different word in the report. |
| `1` … `32` | Keep the newest N generations. `33` and above are refused, not honoured — see "The bound". |
| anything else | Treated as `off`, and reported as `invalid_value` in both route responses. |

The value is trimmed and lower-cased before it is matched, and a leading zero is normalised, so
`CIF_RETAIN_STAGES=" 2 "` and `07` mean `2` and `7` and are not reported as typos. `0` is spelled
out alongside `off` because it is what an operator writes to mean "nothing to keep"; `33`, `-1` and
`1.0` are not, so they fall back to `off` and say so. A typo in a debug switch is reported, never
raised: cleanup keeps running and keeps reclaiming disk space, and the response says why the files
were not kept.

With retention on, the files a delete would have removed are **moved** into a bounded retention
directory instead. One HTTP request is one generation, so it becomes one directory, and each file
keeps the relative subfolder path it had under the storage root:

```text
<output root>/comfyui-image-frontend-retention/
  20261127T171245Z-3f9a1c07/
    comfyui-image-frontend/moody-krea2-advanced-v1/first_pass/0001.png
    comfyui-image-frontend/moody-krea2-advanced-v1/color_matched/0001.png
    comfyui-image-frontend/moody-krea2-advanced-v1/sd_upscaled/0001.png
    comfyui-image-frontend/moody-krea2-advanced-v1/final/0001.png
```

The move is an `os.replace` — both ends live under the same storage root, so a file can never end
up in two places — falling back to copy+remove only when the rename reports `EXDEV` (a storage
root symlinked onto another volume). `temp` locators are kept under the same generation name
inside the temp root.

> **Retained files are never deleted by the image frontend.** The application has already copied
> what it needs into its own storage and stops caring about the ComfyUI side, so only this
> companion touches the retention directory, and only to enforce the count bound below. Nothing
> ages them out, no job reclaims them, and switching the value back to `off` does not delete what
> was already kept: purging them is the operator's job
> (`rm -rf '<output root>/comfyui-image-frontend-retention'` is safe — the app never reads it).

That is what makes this a debug tool and not a storage policy: it exists to answer "which stage
produced this defect?" about a run somebody is already looking at, and it stops being useful the
moment that question is answered. A runtime left on `32` between investigations is a slow disk
incident with a debug label on it, so the whole loop is set the value, reproduce, look at the
stage images, set it back to `off`, delete the directory.

## The bound

One 5616×3152 PNG here is ~20 MB and a generation writes four, so even the ceiling is a few
gigabytes per storage root and an unbounded switch would be its own operational defect; that is why
`33` is refused as an invalid value instead of being honoured. After a request that kept anything,
the companion prunes each storage root it wrote to and keeps only the newest N generation
directories — at most N per storage root, counting only directories named
`<UTC-YYYYMMDDTHHMMSSZ>-<8 hex>` — and removes older ones completely. Age is the timestamp in the
name, never the modification time, because opening a retained image while debugging must not make
the generation the newest. The directory written by the current request is never the one removed,
and neither pruning nor the report below ever follows a symlink out of the retention root, so a
symlinked group can never be deleted along with the volume behind it.

## Reporting the switch

```json
{
  "mode": "2",
  "limit": 2,
  "retained_generations": 2,
  "retained_files": 8,
  "retained_bytes": 167772160,
  "directory": "/srv/comfyui/output/comfyui-image-frontend-retention"
}
```

`GET /comfyui-image-frontend/artifacts/retention` answers that, counting both storage roots and
without deleting anything. `mode` is `off` or the normalised value (`last`, `2`), `limit` is how
many generations are kept, and an `invalid_value` key appears when `CIF_RETAIN_STAGES` held
something the switch does not understand. The scan runs whatever the mode is, so `off` still
reports the generations an earlier setting left behind, and it is capped, so the numbers are a lower
bound on a pathologically full retention root. The image frontend reads this route during its
catalog refresh and shows a non-`off` answer as one informational row in the Workflow health tab;
`off`, an unreachable runtime, or an older companion without the route records nothing. Because an
invalid value falls back to `off`, the health tab cannot show a typo — asking this route directly
is the only way to see `invalid_value`.

The delete response gained two fields alongside the unchanged `deleted` and `missing`: `retained`,
how many files this request moved rather than deleted, and `retention`, the mode that decided it.
