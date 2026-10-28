import type { EventPage, Health, ListingDetail, ListingPage, PollPage } from '../api-types.ts';

/** The API answered with an error, or couldn't be reached (status 503). */
export class ApiError extends Error {
	readonly status: number;

	constructor(status: number, message: string) {
		super(message);
		this.status = status;
	}
}

export interface EventQuery {
	event?: string;
	q?: string;
	listing_id?: number;
	before?: number;
	limit?: number;
}

export interface ListingQuery {
	status?: 'listed' | 'missing' | 'sold_out' | 'gone' | 'all';
	q?: string;
	before?: number;
	limit?: number;
}

export interface PollQuery {
	before?: number;
	limit?: number;
}

type Fetch = typeof fetch;

/** Client of the read-only API of the poller (docs/api.md); only used on the server. */
export class Api {
	readonly #base: string;
	readonly #fetch: Fetch;
	readonly #timeout: number;

	constructor(baseUrl: string, fetchImpl: Fetch = fetch, timeoutMs = 10_000) {
		this.#base = baseUrl;
		this.#fetch = fetchImpl;
		this.#timeout = timeoutMs;
	}

	async #get<T>(path: string, query: object = {}): Promise<T> {
		const params = new URLSearchParams();
		for (const [key, value] of Object.entries(query)) {
			if (value !== undefined && value !== '') {
				params.set(key, String(value));
			}
		}
		const url = `${this.#base}${path}${params.size ? `?${params}` : ''}`;
		let response: Response;
		try {
			response = await this.#fetch(url, { signal: AbortSignal.timeout(this.#timeout) });
		} catch (error) {
			throw new ApiError(
				503,
				`API not reachable at ${this.#base}: ${(error as Error).message}`
			);
		}
		const body = (await response.json().catch(() => null)) as { error?: unknown } | null;
		if (!response.ok) {
			const message = typeof body?.error === 'string' ? body.error : response.statusText;
			throw new ApiError(response.status, message);
		}
		return body as T;
	}

	/** Also answers when the database is unavailable (status "unavailable"). */
	async health(): Promise<Health> {
		try {
			return await this.#get<Health>('/api/health');
		} catch (error) {
			if (error instanceof ApiError && error.status === 503) {
				return {
					status: 'unavailable',
					version: '',
					schema_version: 0,
					last_poll: null,
					error: error.message
				};
			}
			throw error;
		}
	}

	polls(query: PollQuery = {}): Promise<PollPage> {
		return this.#get('/api/polls', query);
	}

	events(query: EventQuery = {}): Promise<EventPage> {
		return this.#get('/api/events', query);
	}

	listings(query: ListingQuery = {}): Promise<ListingPage> {
		return this.#get('/api/listings', query);
	}

	listing(id: number): Promise<ListingDetail> {
		return this.#get(`/api/listings/${id}`);
	}
}
