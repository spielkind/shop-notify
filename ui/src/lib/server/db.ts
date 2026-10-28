import { mkdirSync } from 'node:fs';
import { join } from 'node:path';
import { DatabaseSync } from 'node:sqlite';

/** Version of the schema below, in ``PRAGMA user_version``. */
export const SCHEMA_VERSION = 1;

const SCHEMA = `
CREATE TABLE users (
	id               INTEGER PRIMARY KEY,
	name             TEXT NOT NULL UNIQUE COLLATE NOCASE,
	password_hash    TEXT NOT NULL,
	created          INTEGER NOT NULL,
	password_changed INTEGER NOT NULL
);
CREATE TABLE sessions (
	token_hash TEXT PRIMARY KEY,
	user_id    INTEGER NOT NULL REFERENCES users (id) ON DELETE CASCADE,
	created    INTEGER NOT NULL,
	expires    INTEGER NOT NULL
);
CREATE INDEX sessions_user ON sessions (user_id);
CREATE INDEX sessions_expires ON sessions (expires);
`;

/**
 * Opens (and creates) the UI's database in ``dataDir``: the accounts and sessions,
 * separate from the poller's database. The app and the shop-notify-ui command may use
 * it at the same time.
 */
export function openDatabase(dataDir: string): DatabaseSync {
	mkdirSync(dataDir, { recursive: true, mode: 0o700 });
	return openDatabaseFile(join(dataDir, 'ui.db'));
}

export function openDatabaseFile(path: string): DatabaseSync {
	const db = new DatabaseSync(path);
	try {
		db.exec('PRAGMA journal_mode = WAL; PRAGMA foreign_keys = ON; PRAGMA busy_timeout = 5000');
		const { user_version: version } = db.prepare('PRAGMA user_version').get() as {
			user_version: number;
		};
		if (version > SCHEMA_VERSION) {
			throw new Error(
				`${path} has schema version ${version}, newer than this version supports (${SCHEMA_VERSION})`
			);
		}
		if (version < SCHEMA_VERSION) {
			db.exec(`BEGIN; ${SCHEMA} PRAGMA user_version = ${SCHEMA_VERSION}; COMMIT`);
		}
	} catch (error) {
		db.close();
		throw error;
	}
	return db;
}

/** Seconds since the epoch. */
export function now(): number {
	return Math.floor(Date.now() / 1000);
}
