import { mkdtempSync, statSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { DatabaseSync } from 'node:sqlite';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { now, openDatabase, openDatabaseFile, SCHEMA_VERSION } from './db.ts';
import { hashPassword, verifyPassword } from './passwords.ts';
import { createSession, deleteSession, SESSION_COOKIE, sessionUser } from './sessions.ts';
import {
	checkPassword,
	hasUsers,
	listUsers,
	logoutAll,
	setPassword,
	UserError,
	verifyLogin
} from './users.ts';

const PASSWORD = 'correct horse battery';
const DAY = 86400;

function memory(): DatabaseSync {
	return openDatabaseFile(':memory:');
}

afterEach(() => {
	vi.useRealTimers();
});

describe('database', () => {
	it('is created in the data directory, readable only by its owner', () => {
		const dir = join(mkdtempSync(join(tmpdir(), 'ui-')), 'nested');
		const db = openDatabase(dir);
		expect(statSync(dir).mode & 0o777).toBe(0o700);
		expect(db.prepare('PRAGMA journal_mode').get()).toEqual({ journal_mode: 'wal' });
		expect(db.prepare('PRAGMA user_version').get()).toEqual({ user_version: SCHEMA_VERSION });
		db.close();
		// opening it again keeps the data
		const again = openDatabase(dir);
		expect(hasUsers(again)).toBe(false);
		again.close();
	});

	it('refuses a newer schema', () => {
		const path = join(mkdtempSync(join(tmpdir(), 'ui-')), 'ui.db');
		const db = new DatabaseSync(path);
		db.exec(`PRAGMA user_version = ${SCHEMA_VERSION + 1}`);
		db.close();
		expect(() => openDatabaseFile(path)).toThrow('newer than this version supports');
	});

	it('counts time in seconds', () => {
		vi.useFakeTimers({ now: 1_792_000_000_500 });
		expect(now()).toBe(1_792_000_000);
	});
});

describe('passwords', () => {
	it('are hashed with a random salt and verified', async () => {
		const hash = await hashPassword(PASSWORD);
		expect(hash).toMatch(/^scrypt\$15\$8\$3\$[A-Za-z0-9+/=]+\$[A-Za-z0-9+/=]+$/);
		expect(await hashPassword(PASSWORD)).not.toBe(hash);
		expect(await verifyPassword(PASSWORD, hash)).toBe(true);
		expect(await verifyPassword('wrong', hash)).toBe(false);
	});

	it.each([
		'',
		'bcrypt$1$2$3$a$b',
		'scrypt$15$8$3$c2FsdA==',
		'scrypt$15$8$x$c2FsdA==$a2V5',
		'scrypt$0$8$3$c2FsdA==$a2V5',
		'scrypt$15$8$3$$a2V5',
		'scrypt$15$8$3$c2FsdA==$',
		'scrypt$30$8$1$c2FsdA==$a2V5'
	])('rejects a malformed hash %j', async (hash) => {
		expect(await verifyPassword(PASSWORD, hash)).toBe(false);
	});

	it('rejects a hash with a key of another length', async () => {
		const hash = await hashPassword(PASSWORD);
		expect(await verifyPassword(PASSWORD, `${hash.slice(0, hash.lastIndexOf('$'))}$a2V5`)).toBe(
			false
		);
	});

	it('need 12 to 1024 characters', () => {
		expect(() => checkPassword('x'.repeat(11))).toThrow('at least 12');
		expect(() => checkPassword('x'.repeat(1025))).toThrow('at most 1024');
		expect(() => checkPassword('x'.repeat(12))).not.toThrow();
	});
});

describe('users', () => {
	it('are created and can sign in', async () => {
		const db = memory();
		expect(hasUsers(db)).toBe(false);
		expect(await setPassword(db, 'admin', PASSWORD, 100)).toBe(true);
		expect(hasUsers(db)).toBe(true);
		const user = await verifyLogin(db, 'admin', PASSWORD);
		expect(user).toEqual({ id: expect.any(Number), name: 'admin' });
		// names are case-insensitive
		expect(await verifyLogin(db, 'ADMIN', PASSWORD)).toEqual(user);
		expect(await verifyLogin(db, 'admin', 'wrong password')).toBeNull();
		expect(await verifyLogin(db, 'nobody', PASSWORD)).toBeNull();
		expect(await verifyLogin(db, 'x'.repeat(65), PASSWORD)).toBeNull();
		expect(await verifyLogin(db, 'admin', 'x'.repeat(2000))).toBeNull();
	});

	it('changing the password ends the sessions', async () => {
		const db = memory();
		await setPassword(db, 'admin', PASSWORD, 100);
		const user = (await verifyLogin(db, 'admin', PASSWORD))!;
		const token = createSession(db, user.id, 30, 200);
		expect(await setPassword(db, 'Admin', 'another good password', 300)).toBe(false);
		expect(sessionUser(db, token, 300)).toBeNull();
		expect(await verifyLogin(db, 'admin', PASSWORD)).toBeNull();
		expect(await verifyLogin(db, 'admin', 'another good password')).toEqual(user);
		expect(listUsers(db, 300)).toEqual([
			{ name: 'admin', created: 100, passwordChanged: 300, sessions: 0 }
		]);
	});

	it.each(['', '-admin', 'a b', 'x'.repeat(65), 'admin/1', 'ädmin'])(
		'rejects the name %j',
		async (name) => {
			await expect(setPassword(memory(), name, PASSWORD, 100)).rejects.toThrow(UserError);
		}
	);

	it('rejects a short password', async () => {
		await expect(setPassword(memory(), 'admin', 'short', 100)).rejects.toThrow('at least 12');
	});

	it('lists users with their active sessions', async () => {
		const db = memory();
		await setPassword(db, 'bob', PASSWORD, 100);
		await setPassword(db, 'alice', PASSWORD, 200);
		const bob = (await verifyLogin(db, 'bob', PASSWORD))!;
		createSession(db, bob.id, 1, 300);
		createSession(db, bob.id, 30, 300);
		expect(listUsers(db, 300 + 2 * DAY)).toEqual([
			{ name: 'alice', created: 200, passwordChanged: 200, sessions: 0 },
			{ name: 'bob', created: 100, passwordChanged: 100, sessions: 1 }
		]);
	});

	it('can be signed out everywhere', async () => {
		const db = memory();
		await setPassword(db, 'admin', PASSWORD, 100);
		const user = (await verifyLogin(db, 'admin', PASSWORD))!;
		const tokens = [createSession(db, user.id, 30, 200), createSession(db, user.id, 30, 200)];
		expect(logoutAll(db, 'admin')).toBe(2);
		expect(tokens.map((token) => sessionUser(db, token, 300))).toEqual([null, null]);
		expect(() => logoutAll(db, 'nobody')).toThrow("There is no user 'nobody'");
	});
});

describe('sessions', () => {
	it('last the given number of days', async () => {
		const db = memory();
		await setPassword(db, 'admin', PASSWORD, 100);
		const user = (await verifyLogin(db, 'admin', PASSWORD))!;
		const token = createSession(db, user.id, 30, 1000);
		expect(token).toMatch(/^[A-Za-z0-9_-]{43}$/);
		expect(sessionUser(db, token, 1000 + 30 * DAY - 1)).toEqual(user);
		expect(sessionUser(db, token, 1000 + 30 * DAY)).toBeNull();
	});

	it('store only a hash of the token', async () => {
		const db = memory();
		await setPassword(db, 'admin', PASSWORD, 100);
		const token = createSession(db, 1, 30, 1000);
		const stored = db.prepare('SELECT token_hash FROM sessions').all();
		expect(stored).toEqual([{ token_hash: expect.stringMatching(/^[0-9a-f]{64}$/) }]);
		expect(JSON.stringify(stored)).not.toContain(token);
	});

	it.each([undefined, '', 'short', 'x'.repeat(43) + '!', 'A'.repeat(43)])(
		'ignore the invalid or unknown token %j',
		(token) => {
			expect(sessionUser(memory(), token, 1000)).toBeNull();
		}
	);

	it('can be ended, and expired ones are deleted', async () => {
		const db = memory();
		await setPassword(db, 'admin', PASSWORD, 100);
		const old = createSession(db, 1, 1, 1000);
		const token = createSession(db, 1, 1, 1000 + 2 * DAY);
		expect(db.prepare('SELECT COUNT(*) AS n FROM sessions').get()).toEqual({ n: 1 });
		expect(sessionUser(db, old, 1000)).toBeNull();
		deleteSession(db, token);
		expect(sessionUser(db, token, 1000 + 2 * DAY)).toBeNull();
	});

	it('use a __Host- cookie', () => {
		expect(SESSION_COOKIE).toBe('__Host-session');
	});
});
