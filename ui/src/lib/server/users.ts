import type { DatabaseSync } from 'node:sqlite';
import {
	hashPassword,
	MAX_PASSWORD_LENGTH,
	MIN_PASSWORD_LENGTH,
	verifyPassword
} from './passwords.ts';

export const USER_NAME = /^[a-z0-9][a-z0-9._-]{0,63}$/i;

export class UserError extends Error {}

export interface User {
	id: number;
	name: string;
}

export interface UserInfo {
	name: string;
	created: number;
	passwordChanged: number;
	sessions: number;
}

/** Checked against when a user doesn't exist, so that it takes as long as a wrong password. */
let dummyHash: Promise<string> | undefined;

export function checkPassword(password: string): void {
	if (password.length < MIN_PASSWORD_LENGTH) {
		throw new UserError(`The password must have at least ${MIN_PASSWORD_LENGTH} characters`);
	}
	if (password.length > MAX_PASSWORD_LENGTH) {
		throw new UserError(`The password must have at most ${MAX_PASSWORD_LENGTH} characters`);
	}
}

/**
 * Creates the user or changes its password, which ends all its sessions.
 * Returns whether the user was created.
 */
export async function setPassword(
	db: DatabaseSync,
	name: string,
	password: string,
	time: number
): Promise<boolean> {
	if (!USER_NAME.test(name)) {
		throw new UserError(
			'A username has 1 to 64 letters, digits, dots, dashes or underscores, starting with a letter or digit'
		);
	}
	checkPassword(password);
	const hash = await hashPassword(password);
	const existing = db.prepare('SELECT id FROM users WHERE name = ?').get(name) as
		{ id: number } | undefined;
	if (existing) {
		db.prepare('UPDATE users SET password_hash = ?, password_changed = ? WHERE id = ?').run(
			hash,
			time,
			existing.id
		);
		db.prepare('DELETE FROM sessions WHERE user_id = ?').run(existing.id);
		return false;
	}
	db.prepare(
		'INSERT INTO users (name, password_hash, created, password_changed) VALUES (?, ?, ?, ?)'
	).run(name, hash, time, time);
	return true;
}

/** The user if ``name`` and ``password`` match, else null - in about the same time. */
export async function verifyLogin(
	db: DatabaseSync,
	name: string,
	password: string
): Promise<User | null> {
	const user =
		name.length <= 64 && password.length <= MAX_PASSWORD_LENGTH
			? (db.prepare('SELECT id, name, password_hash FROM users WHERE name = ?').get(name) as
					{ id: number; name: string; password_hash: string } | undefined)
			: undefined;
	if (!user) {
		dummyHash ??= hashPassword('not a password of anyone');
		await verifyPassword(password.slice(0, MAX_PASSWORD_LENGTH), await dummyHash);
		return null;
	}
	return (await verifyPassword(password, user.password_hash))
		? { id: user.id, name: user.name }
		: null;
}

export function hasUsers(db: DatabaseSync): boolean {
	return db.prepare('SELECT 1 FROM users LIMIT 1').get() !== undefined;
}

export function listUsers(db: DatabaseSync, time: number): UserInfo[] {
	return db
		.prepare(
			`SELECT name, created, password_changed AS passwordChanged,
				(SELECT COUNT(*) FROM sessions s WHERE s.user_id = u.id AND s.expires > ?) AS sessions
			FROM users u ORDER BY name`
		)
		.all(time) as unknown as UserInfo[];
}

/** Ends all sessions of the user; returns how many there were. */
export function logoutAll(db: DatabaseSync, name: string): number {
	const user = db.prepare('SELECT id FROM users WHERE name = ?').get(name) as
		{ id: number } | undefined;
	if (!user) {
		throw new UserError(`There is no user '${name}'`);
	}
	return Number(db.prepare('DELETE FROM sessions WHERE user_id = ?').run(user.id).changes);
}
