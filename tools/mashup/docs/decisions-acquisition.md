# Decision log — podcast feed acquisition

Three entries on the stage that turns a pasted podcast RSS URL into a cached,
provenance-backed local audio file. It runs before
[archive ingestion](../README.md#stage-by-stage) and stops at a downloaded
file; transcription is unchanged.

Entries 1–11 are in [`decisions.md`](decisions.md); 12–18, on retrieval,
scoring and the study design, are in
[`decisions-retrieval.md`](decisions-retrieval.md).

---

## 19. `feedparser` rather than a hand-rolled RSS parser

**Context.** Acquisition starts from a URL a stranger controls. The parser has
to survive RSS 0.9x, RSS 2.0 and Atom; the `itunes:` namespace; four date
formats; unescaped ampersands and unclosed tags; and a document that may be
hostile rather than merely sloppy.

**Decision.** Take the dependency. `feedparser` (pinned `>=6.0.11,<7`) does the
XML, namespace and date work. Everything opinionated — enclosure choice, GUID
fallbacks, URL resolution, duration normalisation, de-duplication — stays in
`src/mashup/feed/parse.py`, where it is tested against committed fixtures.

**Why.** Three reasons, in order of weight:

1. It strips DOCTYPE and entity declarations before parsing. A stdlib
   `xml.etree` parse of an attacker-supplied feed is billion-laughs
   vulnerable; avoiding that by hand means reimplementing the mitigation.
2. Date parsing is the part that silently produces *wrong* answers rather than
   errors, and a wrong `pubDate` misorders the episode list a human then picks
   from.
3. It is handed bytes, never a URL, so it never opens a socket of its own and
   the offline test story stays simple.

**Trade-off accepted.** One more runtime dependency in a deliberately short
list, plus its `feedparser-sgmllib` companion. It is pure Python, has no
transitive network or C dependencies, and the surface actually used here is
`parse()` and the resulting mapping — small enough to replace if the project
ever outgrows it.

**A wrinkle worth knowing.** `feedparser` returns `FeedParserDict`s whose most
useful keys — `enclosures`, `rights`, `license` — are *computed aliases*, not
stored entries. Copying one into a plain `dict` silently loses them. The parser
reads those mappings as-is and never copies them.

---

## 20. Rights fail closed on a feed that says nothing

**Context.** Mashup only edits creator-owned, appropriately licensed, or
public-domain material. Almost no podcast feed carries a machine-readable
licence, and a `<copyright>` line is prose.

**Decision.** `check_rights` accepts public-domain marks and Creative Commons
licences without an `-nd` term, from `<atom:link rel="license">` or a licence
URL embedded in the copyright text. Everything else — including silence —
refuses the download. `--i-have-rights` is the escape hatch, and the
acquisition record stores `rights.override: true` when it is used.

**Why.** This mirrors `scripts/fetch_archive.py`, which has enforced the same
position on archive.org items since the dev corpus was first fetched. A gate
that defaults to permissive is not a gate, and the failure mode it prevents —
a licensed clip reaching a rendered edit — is not one you can undo after
publication.

**Trade-off accepted.** Most real podcast feeds will refuse on the first try,
because most publish no licence at all. That is the intended answer, not
friction to be smoothed away: the override exists for the creator's own feed,
and the refusal names it. `mashup feed` deliberately still *lists* an
unlicensed feed with a warning — listing is not acquisition, and the gate
belongs on the byte transfer.

---

## 21. The cache is keyed on (feed URL, GUID) and validated by content hash

**Context.** Re-running acquisition must not re-download unchanged audio, and
the same GUID appears in more than one feed.

**Decision.** The cache directory is
`<workdir>/cache/episodes/<title-slug>-<sha256(feed_url + guid)[:16]>/`,
holding `audio.<ext>` and `acquisition.json`. A rerun is a hit only when the
record exists, names the right schema, and its recorded size *and* SHA-256
still match the file on disk. Downloads go to a `.part` file resumed with a
`Range` request and renamed into place only once complete.

**Why.** Hashing on every rerun costs a read of a file that is already local,
and it is what makes the record a claim about the bytes rather than about a
filename. A truncated or hand-edited file is re-fetched instead of quietly
becoming the episode. Keying on the feed URL as well as the GUID means two
podcasts that both number their episodes `1` do not collide.

**Trade-off accepted.** A GUID is the publisher's identity for an episode, and
plenty of feeds omit it — the parser then borrows the episode link or the
enclosure URL and marks `guidSource` accordingly, so a key that will change if
the publisher moves the file is visible in the record and warned about at the
CLI rather than presented as the feed's own answer.

Related: this is the same posture as
[decision 10](decisions.md#10-every-expensive-stage-is-separately-resumable) —
every expensive stage is separately resumable, keyed explicitly rather than by
timestamp.
