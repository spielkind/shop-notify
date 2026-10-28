import { error } from '@sveltejs/kit';
import { ApiError } from './api.ts';

/** The result of an API call; an API error becomes a 404 or 503 page. */
export async function fromApi<T>(call: Promise<T>): Promise<T> {
	try {
		return await call;
	} catch (e) {
		if (!(e instanceof ApiError)) {
			throw e;
		}
		if (e.status === 404) {
			error(404, 'Not found');
		}
		console.error(`API error ${e.status}: ${e.message}`);
		error(503, 'Unavailable');
	}
}

/**
 * A positive integer from a query or route parameter, else undefined. At most 15 digits,
 * so that it is exact as a JavaScript number (Etsy's listing IDs have 10).
 */
export function positiveInt(value: string | null | undefined): number | undefined {
	return value && /^[1-9][0-9]{0,14}$/.test(value) ? Number(value) : undefined;
}
