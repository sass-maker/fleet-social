# Clip selection qualification — October 4, 2026

The owner requested the production release and stronger automatic excerpt selection in issue #10. This preserves the selected Editorial Planner and canonical local Mashup runtime.

Captioned sources now require complete sentence boundaries inside a 24-second window, no caption gap over 1.5 seconds, and at least one substantive brief term. Ranking uses caption-frequency specificity, term density and coverage, with overlap penalties; a verbatim previous passage cannot be reused inside a longer window. Unrelated, unpunctuated or excessively long passages produce a source-specific error. Captionless sources retain the disclosed first-ten-second fallback. Fifteen meaningful Python regressions cover these rules and the existing typography, pacing, captions and soundtrack controls; CI runs them without new dependencies.

The actual Create form acquired the same two public Creative Commons YouTube originals used in the earlier test, with no prepared video substitution:

| Source                                                    | Selected time   | Complete passage                                                                                                                   |
| --------------------------------------------------------- | --------------- | ---------------------------------------------------------------------------------------------------------------------------------- |
| [Joel Mokyr](https://www.youtube.com/watch?v=y4Orjmn21VI) | 2330.48–2352.16 | The passage explains the need for an English engineer to build and run sophisticated equipment, ending with “That’s what they do.” |
| [Chase Koch](https://www.youtube.com/watch?v=jMR08XbWOgw) | 2744.00–2749.84 | A complete statement about building long-term music assets in Wichita.                                                             |

The brief was “Ideas become progress when people build, maintain, and improve useful things.” Job `fe7a2afd-2ccb-403c-afb2-24d3c170e055`, recipe `f04673a52b6e3d7673399beea8a6fbd195e3a1d993021a097a8aa43279fda1ad`, produced a genuine 27.56-second, 1080×1920 H.264/AAC MP4 of 4,142,557 bytes. Video SHA-256 is `d2345117b8a92fb71376b691d59adf0ba824b3954079a8a5c338f615e2b11ff1`; captions SHA-256 is `da7e6514fe90f7958bc145f468abfe949aa0b122a8646f3fbde79f81d1db8956`. Both files independently match the canonical Mashup receipt; artifact, render-plan approval and provenance validation passed.

Chrome played to `ended=true`, `currentTime=duration=27.56`, `readyState=4`. The new Fleet Social draft `b3fcbdaa-ff6d-4fe3-aacc-ddfc76f18041` remains awaiting video approval. Render-plan approval does not authorize social publishing. See [the saved review](review.jpg).

The shared workspace runner interrupted an earlier attempt during registry/disk measurement. Its unfinished job was explicitly marked interrupted and retried; completed artifacts and the previously approved draft were preserved. No provider publication, migration, credential change or production configuration edit was needed for this qualification.

Selection remains lexical. Complete boundaries improve the cut, but cannot establish semantic narrative coherence; the music excerpt is only broadly related to building useful assets. Source titles can still truncate inside Mashup’s existing header treatment. Review the finished video before approval. Production accepts finished MP4 exports; source acquisition and FFmpeg rendering run locally.
