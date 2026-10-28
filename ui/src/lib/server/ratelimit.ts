export interface Limit {
	/** Failed sign-ins allowed within ``seconds``. */
	failures: number;
	seconds: number;
}

export const DEFAULT_LIMITS = {
	user: { failures: 5, seconds: 15 * 60 },
	address: { failures: 20, seconds: 15 * 60 }
} satisfies Record<string, Limit>;

/**
 * Counts failed sign-ins per user and per client address, in memory (the UI runs as a
 * single process). At most ``maxKeys`` users and addresses are tracked, so that requests
 * with random names can't fill the memory.
 */
export class LoginLimiter {
	readonly #failures = new Map<string, number[]>();
	readonly #limits: typeof DEFAULT_LIMITS;
	readonly #maxKeys: number;

	constructor(limits = DEFAULT_LIMITS, maxKeys = 10_000) {
		this.#limits = limits;
		this.#maxKeys = maxKeys;
	}

	#keys(user: string, address: string): [string, Limit][] {
		return [
			[`user:${user.toLowerCase()}`, this.#limits.user],
			[`address:${address}`, this.#limits.address]
		];
	}

	#recent(key: string, limit: Limit, time: number): number[] {
		return (this.#failures.get(key) ?? []).filter((t) => t > time - limit.seconds);
	}

	/** Seconds until the next attempt is allowed; 0 if it is allowed now. */
	retryAfter(user: string, address: string, time: number): number {
		let wait = 0;
		for (const [key, limit] of this.#keys(user, address)) {
			const recent = this.#recent(key, limit, time);
			if (recent.length >= limit.failures) {
				wait = Math.max(
					wait,
					recent[recent.length - limit.failures]! + limit.seconds - time
				);
			}
		}
		return wait;
	}

	failed(user: string, address: string, time: number): void {
		for (const [key, limit] of this.#keys(user, address)) {
			const recent = this.#recent(key, limit, time).slice(-(limit.failures - 1));
			this.#failures.delete(key); // re-insert as the newest key
			this.#failures.set(key, [...recent, time]);
		}
		for (const key of this.#failures.keys()) {
			if (this.#failures.size <= this.#maxKeys) {
				break;
			}
			this.#failures.delete(key);
		}
	}

	/** A successful sign-in clears the user's failures, not those of the address. */
	succeeded(user: string): void {
		this.#failures.delete(`user:${user.toLowerCase()}`);
	}
}
