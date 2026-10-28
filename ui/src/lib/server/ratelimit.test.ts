import { describe, expect, it } from 'vitest';
import { DEFAULT_LIMITS, LoginLimiter } from './ratelimit.ts';

describe('LoginLimiter', () => {
	it('blocks a user after 5 failures for 15 minutes', () => {
		const limiter = new LoginLimiter();
		for (let i = 0; i < 5; i++) {
			expect(limiter.retryAfter('admin', `10.0.0.${i}`, 1000 + i)).toBe(0);
			limiter.failed('Admin', `10.0.0.${i}`, 1000 + i);
		}
		// counted per user, whatever the case and address
		expect(limiter.retryAfter('ADMIN', '10.0.0.9', 1005)).toBe(900 - 5);
		expect(limiter.retryAfter('admin', '10.0.0.9', 1000 + 900)).toBe(0);
		expect(limiter.retryAfter('other', '10.0.0.9', 1005)).toBe(0);
	});

	it('blocks an address after 20 failures', () => {
		const limiter = new LoginLimiter();
		for (let i = 0; i < 20; i++) {
			limiter.failed(`user${i}`, '10.0.0.1', 1000);
		}
		expect(limiter.retryAfter('new', '10.0.0.1', 1000)).toBe(900);
		expect(limiter.retryAfter('new', '10.0.0.2', 1000)).toBe(0);
	});

	it('waits for the oldest failure that still counts', () => {
		const limiter = new LoginLimiter({
			user: { failures: 2, seconds: 100 },
			address: DEFAULT_LIMITS.address
		});
		limiter.failed('a', 'x', 0);
		limiter.failed('a', 'x', 50);
		limiter.failed('a', 'x', 90);
		expect(limiter.retryAfter('a', 'x', 95)).toBe(55);
	});

	it('forgets the failures of a user after a successful sign-in', () => {
		const limiter = new LoginLimiter();
		for (let i = 0; i < 5; i++) {
			limiter.failed('admin', '10.0.0.1', 1000);
		}
		limiter.succeeded('ADMIN');
		expect(limiter.retryAfter('admin', '10.0.0.2', 1000)).toBe(0);
	});

	it('tracks a limited number of users and addresses', () => {
		const limiter = new LoginLimiter(DEFAULT_LIMITS, 4);
		for (let i = 0; i < 5; i++) {
			limiter.failed('admin', '10.0.0.1', 1000);
		}
		// the oldest keys are dropped first
		limiter.failed('u1', 'a1', 1000);
		limiter.failed('u2', 'a2', 1000);
		expect(limiter.retryAfter('admin', 'other', 1000)).toBe(0);
	});
});
