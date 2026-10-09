# scripts/

## `fetch_archive.py`

Downloads a public-domain archive.org item into a local folder so the mashup
pipeline has a real, legally clean corpus to develop against.

It selects files by archive.org `format` (default `MPEG4`), downloads them one
at a time with a resumable `.part` file, verifies the published md5, skips
anything already complete, and writes a `PROVENANCE.json` recording the item,
title, licence, source URL, fetch date, and every file's size and md5.

### Fetch the dev archive

```bash
# See what you'd get first — no network writes, no downloads.
python scripts/fetch_archive.py --item ybylcollection --dest ./archive --limit 20 --dry-run

# Then fetch (~1.6 GB for 20 episodes at ~80 MB each).
python scripts/fetch_archive.py --item ybylcollection --dest ./archive --limit 20
```

Re-running is safe: complete files are skipped and interrupted ones resume via
an HTTP `Range` request. Ctrl-C leaves a `.part` file, never a truncated `.mp4`.

Exit codes: `0` ok, `1` runtime error, `2` bad usage, `3` licence refused,
`4` md5 mismatch (the corrupt file is deleted), `130` interrupted.

### Licence position

`ybylcollection` — *You Bet Your Life Collection* (Groucho Marx), 42 MPEG4
episodes — is published under
[Public Domain Mark 1.0](http://creativecommons.org/publicdomain/mark/1.0/).
That mark asserts the work is free of known copyright worldwide, so it carries
no restriction on copying, editing, recombining, or redistributing it. Themed
mashups are derivative works, and public domain material permits derivatives
without permission, attribution, or licence compatibility checks. It is also a
good fit for the product: one creator, one archive, comedy with recurring
running gags.

The script enforces this rather than trusting it. It reads `licenseurl` from
the item metadata and exits `3` unless the licence is a public domain mark/CC0
or a CC licence, and always rejects anything containing `-nd` (NoDerivatives).
Missing or unrecognised licences are refused too.

### Creators fetching their own material

If you own the material, the metadata licence gate does not apply to you. Pass
`--i-have-rights` to skip it:

```bash
python scripts/fetch_archive.py --item my-own-item --dest ./archive --i-have-rights
```

The licence found (or `unspecified (--i-have-rights)`) is still recorded in
`PROVENANCE.json`, so provenance stays documented either way.
