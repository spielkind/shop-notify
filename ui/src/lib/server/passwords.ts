import { randomBytes, scrypt, timingSafeEqual, type ScryptOptions } from 'node:crypto';
import { promisify } from 'node:util';

const scryptAsync = promisify(scrypt) as (
	password: string,
	salt: Buffer,
	keyLength: number,
	options: ScryptOptions
) => Promise<Buffer>;

/**
 * scrypt parameters, at least as strong as OWASP's recommendation (N=2^15, r=8, p=3:
 * 32 MiB of memory per hash). Stored with every hash, so they can be raised later.
 */
const LOG_N = 15;
const R = 8;
const P = 3;
const KEY_LENGTH = 32;
const MAX_MEMORY = 64 * 1024 * 1024;

export const MIN_PASSWORD_LENGTH = 12;
/** Longer passwords would only cost time to hash. */
export const MAX_PASSWORD_LENGTH = 1024;

function derive(password: string, salt: Buffer, logN: number, r: number, p: number) {
	return scryptAsync(password, salt, KEY_LENGTH, { N: 2 ** logN, r, p, maxmem: MAX_MEMORY });
}

/** A hash of ``password`` like ``scrypt$15$8$3$<salt>$<key>`` (base64). */
export async function hashPassword(password: string): Promise<string> {
	const salt = randomBytes(16);
	const key = await derive(password, salt, LOG_N, R, P);
	return ['scrypt', LOG_N, R, P, salt.toString('base64'), key.toString('base64')].join('$');
}

/** Whether ``password`` matches ``hash``; false for a malformed hash. */
export async function verifyPassword(password: string, hash: string): Promise<boolean> {
	const parts = hash.split('$');
	const [scheme, logN, r, p, salt, key] = parts;
	if (parts.length !== 6 || scheme !== 'scrypt' || !salt || !key) {
		return false;
	}
	const params = [logN, r, p].map(Number);
	if (!params.every((n) => Number.isInteger(n) && n > 0 && n <= 32)) {
		return false;
	}
	const expected = Buffer.from(key, 'base64');
	let actual: Buffer;
	try {
		actual = await derive(
			password,
			Buffer.from(salt, 'base64'),
			...(params as [number, number, number])
		);
	} catch {
		return false; // e.g. parameters needing more memory than allowed
	}
	return actual.length === expected.length && timingSafeEqual(actual, expected);
}
