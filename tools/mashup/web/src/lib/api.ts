import type {
  CandidateResponse,
  EDL,
  SaveResult,
  SegmentDetail,
} from './types';

async function failure(response: Response): Promise<Error> {
  let detail = `${response.status} ${response.statusText}`;
  try {
    const body = await response.json();
    if (body?.error) detail = body.error;
    if (Array.isArray(body?.detail) && body.detail.length) {
      const first = body.detail[0];
      detail += ` — ${(first.loc ?? []).join('.')}: ${first.msg}`;
    }
  } catch {
    /* non-JSON error body; the status line is all we have */
  }
  return new Error(detail);
}

export async function getEdl(): Promise<EDL> {
  const response = await fetch('/api/edl');
  if (!response.ok) throw await failure(response);
  return response.json();
}

export async function putEdl(edl: EDL): Promise<SaveResult> {
  const response = await fetch('/api/edl', {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(edl),
  });
  if (!response.ok) throw await failure(response);
  const recomputed = response.headers.get('X-Mashup-Score-Recomputed') ?? '';
  return {
    doc: await response.json(),
    mode: response.headers.get('X-Mashup-Score-Mode') ?? 'partial',
    recomputed: recomputed ? recomputed.split(',') : [],
  };
}

export async function getCandidates(
  query: string,
  limit = 20,
  signal?: AbortSignal,
): Promise<CandidateResponse> {
  const params = new URLSearchParams({ q: query, limit: String(limit) });
  const response = await fetch(`/api/candidates?${params}`, { signal });
  if (!response.ok) throw await failure(response);
  return response.json();
}

export async function getSegment(id: string): Promise<SegmentDetail> {
  const response = await fetch(`/api/segment/${encodeURIComponent(id)}`);
  if (!response.ok) throw await failure(response);
  return response.json();
}

export function mediaUrl(sourceId: string, start: number, end: number): string {
  const params = new URLSearchParams({
    start: start.toFixed(3),
    end: end.toFixed(3),
  });
  return `/api/media/${encodeURIComponent(sourceId)}?${params}`;
}
