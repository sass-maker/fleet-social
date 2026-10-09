/** Mirrors src/mashup/models.py. Keep the two in step. */

export type Role =
  | 'setup'
  | 'development'
  | 'punchline'
  | 'callback'
  | 'closer'
  | 'aside';

export const ROLES: Role[] = [
  'setup',
  'development',
  'punchline',
  'callback',
  'closer',
  'aside',
];

export interface VisualInsert {
  mode: 'still' | 'motion';
  start: number;
  end: number;
  source_path: string;
  source_time: number;
  source_title: string;
  source_url: string;
}

export interface Clip {
  index: number;
  segment_id: string;
  segment_ids: string[];
  source_id: string;
  source_title: string;
  source_path: string;
  start: number;
  end: number;
  render_start: number;
  render_end: number;
  text: string;
  summary: string;
  role: Role;
  energy: number;
  topics: string[];
  visuals: VisualInsert[];
  transition: 'cut' | 'crossfade';
  edited: boolean;
  note: string | null;
}

export interface ScoreTerms {
  relevance: number;
  context_completeness: number;
  non_repetition: number;
  progression: number;
  escalation: number;
  callback: number;
  duration_fit: number;
  source_diversity: number;
}

export const TERM_KEYS: (keyof ScoreTerms)[] = [
  'relevance',
  'context_completeness',
  'non_repetition',
  'progression',
  'escalation',
  'callback',
  'duration_fit',
  'source_diversity',
];

export interface EDL {
  version: number;
  strategy: string;
  prompt: string;
  target_duration: number;
  generated_at: string;
  clips: Clip[];
  score: number;
  terms: ScoreTerms;
  weights: Record<string, number>;
  rationale: string[];
}

/** One row from /api/candidates — enough to judge a replacement. */
export interface Candidate {
  id: string;
  source_id: string;
  source_title: string;
  source_path: string;
  start: number;
  end: number;
  duration: number;
  summary: string;
  text: string;
  role: Role;
  energy: number;
  topics: string[];
  relevance?: number | null;
}

export interface CandidateResponse {
  query: string;
  mode: 'embedding' | 'substring' | 'browse';
  count: number;
  results: Candidate[];
}

export interface SegmentDetail {
  segment: Candidate & {
    cue_start: number;
    cue_end: number;
    entities: string[];
    required_context: string[];
    can_open: boolean;
    can_end: boolean;
    has_embedding: boolean;
  };
  prev: Candidate | null;
  next: Candidate | null;
  source: {
    id: string;
    title: string;
    duration: number;
    has_video: boolean;
  };
}

/** What PUT /api/edl gives back: the saved doc plus how it was rescored. */
export interface SaveResult {
  doc: EDL;
  mode: 'full' | 'partial' | string;
  recomputed: string[];
}
