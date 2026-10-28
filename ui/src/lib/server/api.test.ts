import { isHttpError } from '@sveltejs/kit';
import { mkdtempSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { Api, ApiError } from './api.ts';
import { getContext, resetContext } from './context.ts';
import { fromApi, positiveInt } from './load.ts';

function json(status: number, body: unknown): Response {
	return new Response(JSON.stringify(body), {
		status,
		headers: { 'Content-Type': 'application/json' }
	});
}

describe('Api', () => {
	it('requests the endpoints with their parameters', async () => {
		const fetch = vi.fn(async () => json(200, { events: [], next: null }));
		const api = new Api('http://api:8080', fetch);
		await api.events({ event: 'new,back', q: '', listing_id: 7, before: undefined, limit: 50 });
		await api.polls();
		await api.listing(42);
		await api.events();
		expect(fetch.mock.calls.map((call) => (call as unknown[])[0])).toEqual([
			'http://api:8080/api/events?event=new%2Cback&listing_id=7&limit=50',
			'http://api:8080/api/polls',
			'http://api:8080/api/listings/42',
			'http://api:8080/api/events'
		]);
		expect((fetch.mock.calls[0] as unknown[])[1]).toEqual({ signal: expect.any(AbortSignal) });
	});

	it('reports the error of the API', async () => {
		const api = new Api('http://api:8080', async () =>
			json(404, { error: 'Listing 9 is unknown' })
		);
		await expect(api.listing(9)).rejects.toEqual(new ApiError(404, 'Listing 9 is unknown'));
		await expect(api.listing(9)).rejects.toMatchObject({ status: 404 });
	});

	it('reports an answer that is no JSON', async () => {
		const api = new Api(
			'http://api:8080',
			async () =>
				new Response('<html>Bad gateway</html>', { status: 502, statusText: 'Bad Gateway' })
		);
		await expect(api.polls()).rejects.toMatchObject({ status: 502, message: 'Bad Gateway' });
	});

	it('reports an API that is not reachable', async () => {
		const api = new Api('http://api:8080', async () => {
			throw new TypeError('fetch failed');
		});
		await expect(api.polls()).rejects.toMatchObject({
			status: 503,
			message: 'API not reachable at http://api:8080: fetch failed'
		});
	});

	it('gives up after the timeout', async () => {
		const api = new Api(
			'http://api:8080',
			(_url, init) =>
				new Promise((_resolve, reject) => {
					init?.signal?.addEventListener('abort', () => reject(init.signal?.reason));
				}),
			10
		);
		await expect(api.polls()).rejects.toMatchObject({ status: 503 });
	});

	it('answers the health even if the database is unavailable', async () => {
		const ok = { status: 'ok', version: '1.5.0', schema_version: 3, last_poll: null };
		expect(await new Api('http://api', async () => json(200, ok)).health()).toEqual(ok);
		const unavailable = new Api('http://api', async () =>
			json(503, { status: 'unavailable', error: 'No database' })
		);
		expect(await unavailable.health()).toEqual({
			status: 'unavailable',
			version: '',
			schema_version: 0,
			last_poll: null,
			error: 'No database'
		});
		const broken = new Api('http://api', async () => json(500, { error: 'Internal error' }));
		await expect(broken.health()).rejects.toMatchObject({ status: 500 });
	});

	it('uses the global fetch by default', () => {
		expect(new Api('http://api')).toBeInstanceOf(Api);
	});
});

describe('fromApi', () => {
	it('passes results through', async () => {
		expect(await fromApi(Promise.resolve(1))).toBe(1);
	});

	it.each([
		[404, 404],
		[400, 503],
		[500, 503],
		[503, 503]
	])('turns API error %i into page error %i', async (apiStatus, pageStatus) => {
		const spy = vi.spyOn(console, 'error').mockImplementation(() => {});
		const error = await fromApi(Promise.reject(new ApiError(apiStatus, 'x'))).catch((e) => e);
		expect(isHttpError(error, pageStatus)).toBe(true);
		spy.mockRestore();
	});

	it('passes other errors on', async () => {
		await expect(fromApi(Promise.reject(new Error('bug')))).rejects.toThrow('bug');
	});
});

describe('positiveInt', () => {
	it.each([
		['1', 1],
		['123456789012345', 123456789012345],
		['1234567890123456', undefined],
		['0', undefined],
		['01', undefined],
		['-1', undefined],
		['1.5', undefined],
		['', undefined],
		[null, undefined],
		[undefined, undefined]
	])('%j -> %j', (value, expected) => {
		expect(positiveInt(value)).toBe(expected);
	});
});

describe('getContext', () => {
	afterEach(() => resetContext());

	it('is created once from the environment', () => {
		const dataDir = join(mkdtempSync(join(tmpdir(), 'ui-')), 'data');
		const context = getContext({ UI_DATA_DIR: dataDir, API_URL: 'http://api:8080' });
		expect(context.config.dataDir).toBe(dataDir);
		expect(context.api).toBeInstanceOf(Api);
		// compared by identity: comparing the contents would read the closed database
		expect(getContext({}) === context).toBe(true);
		resetContext();
		expect(getContext({ UI_DATA_DIR: dataDir }) === context).toBe(false);
	});

	it('can be reset before it is created', () => {
		expect(() => resetContext()).not.toThrow();
	});
});
