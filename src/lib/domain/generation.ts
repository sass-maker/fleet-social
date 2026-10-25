/**
 * Scheduled generation: a calendar slot asks a generator for media ahead of
 * its planned release. Cloud and API generators are called by the scheduler
 * with a signed webhook; local generators cannot be reached, so they claim
 * their due slots instead. Either way the generator answers through draft
 * intake with the slot's sourceRef, and the slot is ready once media lands.
 */
import { GENERATOR_KINDS, type GeneratorKind } from './media-receipt';

export const GENERATION_REQUEST_SCHEMA = 'fleet.generation-request.v1';
export const SLOT_STATUSES = ['planned', 'requested', 'ready', 'failed', 'cancelled'] as const;
export type SlotStatus = (typeof SLOT_STATUSES)[number];

/** Webhook attempts before a slot parks in failed; the owner can retry it. */
export const MAX_DISPATCH_ATTEMPTS = 5;
export const DEFAULT_LEAD_MINUTES = 360;
const MAX_LEAD_MINUTES = 14 * 24 * 60;
const NAME_RE = /^[a-zA-Z0-9][a-zA-Z0-9._-]{0,63}$/;
const SLOT_REF_PREFIX = 'slot:';

export function slotSourceRef(slotId: string): string {
	return `${SLOT_REF_PREFIX}${slotId}`;
}

export function slotIdFromSourceRef(sourceRef: string | null | undefined): string | null {
	return sourceRef?.startsWith(SLOT_REF_PREFIX) ? sourceRef.slice(SLOT_REF_PREFIX.length) : null;
}

/** Minutes to wait before the next webhook attempt: 2, 4, 8, 16. */
export function retryDelayMinutes(attempts: number): number {
	return 2 ** Math.max(1, Math.min(attempts, 4));
}

/**
 * What the calendar shows. A requested slot whose release time passed with no
 * media is late rather than still generating: the generator never answered.
 */
export function slotDisplayState(
	slot: { status: string; plannedFor: Date | string },
	now = new Date()
): SlotStatus | 'late' {
	const status = slot.status as SlotStatus;
	if ((status === 'planned' || status === 'requested') && new Date(slot.plannedFor) < now) {
		return 'late';
	}
	return status;
}

type Ok<T> = { ok: true; value: T };
type Err = { ok: false; error: string };

export type GeneratorInput = {
	name: string;
	kind: GeneratorKind;
	endpointUrl: string | null;
	leadMinutes: number;
};

function endpointProblem(url: URL): string | null {
	if (url.protocol === 'https:') return null;
	// A generator running beside `npm run dev` is the only plain-http case.
	if (url.protocol === 'http:' && ['localhost', '127.0.0.1'].includes(url.hostname)) return null;
	return 'endpointUrl must be https';
}

export function parseGeneratorInput(raw: unknown): Ok<GeneratorInput> | Err {
	if (!raw || typeof raw !== 'object') return { ok: false, error: 'Invalid JSON body' };
	const body = raw as Record<string, unknown>;
	if (typeof body.name !== 'string' || !NAME_RE.test(body.name)) {
		return { ok: false, error: 'name must be a short identifier (letters, digits, . _ -)' };
	}
	if (!GENERATOR_KINDS.includes(body.kind as GeneratorKind)) {
		return { ok: false, error: 'kind must be local, cloud or api' };
	}
	const kind = body.kind as GeneratorKind;
	let endpointUrl: string | null = null;
	if (kind !== 'local') {
		if (typeof body.endpointUrl !== 'string' || !body.endpointUrl.trim()) {
			return { ok: false, error: 'Cloud and API generators need an endpointUrl' };
		}
		let url: URL;
		try {
			url = new URL(body.endpointUrl.trim());
		} catch {
			return { ok: false, error: 'endpointUrl is not a URL' };
		}
		const problem = endpointProblem(url);
		if (problem) return { ok: false, error: problem };
		endpointUrl = url.toString();
	}
	const lead = body.leadMinutes ?? DEFAULT_LEAD_MINUTES;
	if (typeof lead !== 'number' || !Number.isInteger(lead) || lead < 0 || lead > MAX_LEAD_MINUTES) {
		return { ok: false, error: 'leadMinutes must be a whole number of minutes up to 14 days' };
	}
	return { ok: true, value: { name: body.name, kind, endpointUrl, leadMinutes: lead } };
}

export type SlotInput = {
	generatorId: string;
	projectId: string;
	brief: string;
	title: string | null;
	plannedFor: Date;
};

export function parseSlotInput(
	raw: unknown,
	isProject: (id: unknown) => id is string,
	now = new Date()
): Ok<SlotInput> | Err {
	if (!raw || typeof raw !== 'object') return { ok: false, error: 'Invalid JSON body' };
	const body = raw as Record<string, unknown>;
	if (typeof body.generatorId !== 'string' || !body.generatorId) {
		return { ok: false, error: 'Choose a generator' };
	}
	if (!isProject(body.projectId)) return { ok: false, error: 'Choose an active Fleet project' };
	const brief = typeof body.brief === 'string' ? body.brief.trim() : '';
	if (!brief) return { ok: false, error: 'Describe what to generate' };
	if (brief.length > 4000) return { ok: false, error: 'Brief must be 4,000 characters or fewer' };
	const title = typeof body.title === 'string' && body.title.trim() ? body.title.trim() : null;
	if (title && title.length > 200)
		return { ok: false, error: 'Title must be 200 characters or fewer' };
	const plannedFor = new Date(String(body.plannedFor ?? ''));
	if (Number.isNaN(plannedFor.getTime())) return { ok: false, error: 'plannedFor must be a date' };
	if (plannedFor <= now) return { ok: false, error: 'Plan the release in the future' };
	return {
		ok: true,
		value: { generatorId: body.generatorId, projectId: body.projectId, brief, title, plannedFor }
	};
}
