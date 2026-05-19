# Filesystem archive format

The Filesystem archive provider (`ARCHIVE_PROVIDER=fs`) treats a directory
tree as a MAM. This document is the authoritative reference for the on-disk
shape; the spec it implements is
`docs/specs/2026-05-19-archive-abstraction-and-offline-mode-design.md` §5.3.

## Directory layout

```
<FS_ROOT>/
  .archive/
    fields.json                # optional; provider field definitions
  <catalog>/                   # one directory per catalog
    <clip>.mov                 # the media file (any of FS_MEDIA_EXTS)
    <clip>.annot.json          # optional sidecar: markers / fields / notes
    nested/
      <clip2>.mp4              # nesting is allowed; counts as same catalog
      <clip2>.annot.json
```

- **Catalogs** are the top-level subdirectories of `FS_ROOT`. Subdirectories
  below them are walked recursively by `list_clips` but are NOT catalogs.
- **Hidden directories** (any whose name starts with `.`) are excluded from
  the catalog list. `.archive/` is also excluded by name.
- **Media files** are the files matching any extension in `FS_MEDIA_EXTS`
  (default: `.mov,.mp4,.mkv,.mxf,.m4v,.avi`). Match is case-insensitive.
- **Sidecars** live next to their media: `<clipname>.annot.json` for
  `<clipname>.<ext>`. A missing sidecar is fine — it represents a clip
  with no annotations yet.

## Clip identity

The provider-clip-id used everywhere downstream (write queue, audit log,
cache mirror) is the **catalog-prefixed path with the media extension
stripped, using forward slashes**:

```
<FS_ROOT>/archive_30s/clip001.mov  →  provider_clip_id = "archive_30s/clip001"
<FS_ROOT>/archive_30s/scene_a/c.mp4 → provider_clip_id = "archive_30s/scene_a/c"
```

This is stable across host OS (Windows `\` is normalised to `/`),
human-readable in the audit log, and unambiguous within a single `FS_ROOT`.
Renaming the media file changes the id (it is a path, not a hash). If you
need rename-stable ids, use the catalog tooling to issue a copy + delete.

## Sidecar JSON schema

```json
{
  "markers": [
    {
      "name": "intro",
      "in":  {"secs": 0.0, "frm": 0,  "fps": 25.0},
      "out": {"secs": 2.0, "frm": 50, "fps": 25.0},
      "description": null,
      "category":    null,
      "color":       null
    }
  ],
  "fields": {
    "pragafilm.barva": {"value": true,        "is_multi": false},
    "tags":            {"value": ["a", "b"], "is_multi": true}
  },
  "notes": {
    "notes":    "Free-form note text.",
    "bigNotes": "Longer note text."
  },
  "provider_data": {
    "vendor_x": {"any": "blob"}
  }
}
```

Notes on the shape:

- `markers[*].out` may be `null` for a point marker.
- `markers[*].in.frm` is the frame number derived from `secs * fps`
  (rounded). It is persisted so a future re-detection of fps does not
  silently shift the marker against the clip.
- `fields[id].value` is opaque (scalar | list | string).
  `is_multi` is advisory: a `list` value implies multi.
- `notes` keys are display-target names (`notes`, `bigNotes`, ...).
- `provider_data` is round-tripped untouched. Unknown top-level keys on
  the sidecar are folded into `provider_data` on read so they survive
  the next write.

## Field definitions (`.archive/fields.json`)

Optional. Lives at `<FS_ROOT>/.archive/fields.json`. Shape:

```json
[
  {
    "identifier":      "pragafilm.dekáda.natočení",
    "name":            "Decade filmed",
    "type":            "picklist",
    "is_multi":        false,
    "is_editable":     true,
    "picklist_values": ["20.léta", "30.léta", "40.léta"]
  },
  {
    "identifier":  "pragafilm.barva",
    "name":        "Colour",
    "type":        "bool",
    "is_multi":    false,
    "is_editable": true
  }
]
```

Valid `type` values are `text | integer | decimal | date | picklist | multi-picklist | bool`.
A missing or malformed file results in an empty field-defs list and a
warning in the application log; the adapter does not refuse to start.
Unknown JSON keys per entry are preserved on the `FieldDef.provider_data`
blob.

## Etag semantics

The FS adapter declares `supports_etag=True`. The etag for a clip is the
SHA-256 of the sidecar bytes on disk (`hashlib.sha256(bytes).hexdigest()`).
A missing sidecar has etag `None`.

Reads compute the etag from the on-disk bytes. Writes:

1. Read the current sidecar bytes (if any).
2. If `change_set.expected_etag` is set and differs from the computed
   etag, return `WriteResult(status="conflict", ...)`. No change is
   persisted.
3. Otherwise apply the ops, render the new JSON, and write atomically
   (tempfile + `fsync` + `os.replace`).
4. Return `WriteResult(status="ok", new_etag=<sha256 of new bytes>)`.

This gives strict optimistic concurrency — a concurrent edit since the
last read will refuse the write rather than overwriting.

## Atomicity

Sidecar writes are POSIX-atomic via tempfile + fsync + rename on the same
filesystem. Failure modes:

- Crash before `os.replace`: the existing sidecar is untouched. The
  tempfile is left behind in the catalog directory; the application
  cleans it up on the next write attempt that follows the same code
  path (the tempfile naming `<sidecar>.<random>.tmp` does not collide).
- Crash after `os.replace`: the new sidecar is committed.
- Exception during serialisation: the tempfile is unlinked, the
  existing sidecar is untouched.

## ffprobe

Duration and fps come from `ffprobe` (an `ffmpeg` companion binary). If
`ffprobe` is not on `PATH` the adapter logs a single warning per process
and returns `duration_secs=0.0, fps=25.0` for every clip. The user can
still annotate and apply; only timeline UI display will be inaccurate.
Installing `ffmpeg` enables accurate probing without restarting the app
(the probe runs per-call).

## Capabilities

The FS provider declares:

| Capability               | Value                |
|--------------------------|----------------------|
| `supports_markers`       | `True`               |
| `supports_notes`         | `{"notes","bigNotes"}` |
| `supports_field_create`  | `False`              |
| `supports_etag`          | `True`               |
| `media_is_local`         | `True`               |
| `write_atomicity`        | `"per-clip"`         |

`media_is_local=True` means `WorkspaceManager.prepare()` does not copy
media into the local cache (there is nothing to copy — the bytes are
already on disk). `proxy_cache` rows are not used for this provider.
