const canonicalUrl = import.meta.env.PUBLIC_SITE_URL || 'https://mashup.highsignal.app';

export const site = {
  name: 'Mashup',
  url: canonicalUrl.replace(/\/$/, ''),
  title: 'Mashup — authored edits from approved media',
  description:
    'Mashup turns approved podcast and video archives into coherent, inspectable edits—proven with a source-faithful three-episode synthesis.',
  summary:
    'A local-first editorial tool for creators and operators who need to find, approve, sequence, and render coherent clips without losing source provenance or human control.',
  audience:
    'Creators and operators assembling comedy, motivation, podcast, or clipping compilations from creator-owned, licensed, or public-domain media.',
  status: 'Public proof; local operator pilot',
  socialImage: 'https://mashup.highsignal.app/social.png',
  github: 'https://github.com/sass-maker/mashup',
  primaryMedia: 'https://mashup.highsignal.app/media/survive-technology-final.mp4',
  primaryCaptions: 'https://mashup.highsignal.app/captions/survive-technology-final.vtt',
  primaryReceipt: '/receipts/survive-technology.receipt.json',
  primaryScoreLedger: '/receipts/survive-technology.score.json',
  compactMedia: 'https://mashup.highsignal.app/media/operators-final.mp4',
  compactCaptions: 'https://mashup.highsignal.app/captions/operators-final.vtt',
  compactReceipt: '/receipts/operators.receipt.json',
  capabilities: [
    'Resumable local archive ingestion, transcription, enrichment, embedding, and planning',
    'Editable EDLs with eight independent score terms and explicit approval boundaries',
    'Source-faithful multi-clip rendering with captions, source headings, and media receipts',
    'Static public screening for approved finished media and its provenance',
  ],
  boundaries: [
    'The public website does not accept uploads, accounts, archives, or render jobs',
    'Approval and publishing remain human decisions on the operator machine',
    'Source speech stays intact; synthetic speech, voice cloning, and deceptive footage are prohibited',
    'Filmed media must be creator-owned, appropriately licensed, or public domain',
  ],
  lastUpdated: '2026-08-28',
} as const;
