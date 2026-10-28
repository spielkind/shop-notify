import { createHash, randomBytes } from 'node:crypto';
import type { DatabaseSync } from 'node:sqlite';
import type { User } from './users.ts';

/** ``__Host-``: only sent over HTTPS, to this host and path ``/``, not settable by subdomains. */
export const SESSION_COOKIE = '__Host-session';

const TOKEN = /^[A-Za-z0-9_-]{43}$/;

/** Only the hash of a token is stored, so the database alone can't be used to sign in. */
function tokenHash(token: string): string {
	return createHash('sha256').update(token).digest('hex');
}

/** Starts a session for ``days`` days and returns its token for the cookie. */
export function createSession(
	db: DatabaseSync,
	userId: number,
	days: number,
	time: number
): string {
	const token = randomBytes(32).toString('base64url');
	db.prepare('DELETE FROM sessions WHERE expires <= ?').run(time);
	db.prepare(
		'INSERT INTO sessions (token_hash, user_id, created, expires) VALUES (?, ?, ?, ?)'
	).run(tokenHash(token), userId, time, time + days * 86400);
	return token;
}

/** The user of a valid session, else null. */
export function sessionUser(
	db: DatabaseSync,
	token: string | undefined,
	time: number
): User | null {
	if (!token || !TOKEN.test(token)) {
		return null;
	}
	const user = db
		.prepare(
			`SELECT u.id, u.name FROM sessions s JOIN users u ON u.id = s.user_id
			WHERE s.token_hash = ? AND s.expires > ?`
		)
		.get(tokenHash(token), time) as User | undefined;
	return user ? { id: user.id, name: user.name } : null;
}

export function deleteSession(db: DatabaseSync, token: string): void {
	db.prepare('DELETE FROM sessions WHERE token_hash = ?').run(tokenHash(token));
}
