import { EventEmitter } from 'node:events';
import { mkdtempSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { Readable } from 'node:stream';
import { describe, expect, it } from 'vitest';
import { run, type Input, type Io } from './cli.ts';
import { openDatabase } from './db.ts';
import { verifyLogin } from './users.ts';

const PASSWORD = 'correct horse battery';

/** A terminal that types the next chunk whenever input is read. */
class Terminal extends EventEmitter implements Input {
	isTTY = true;
	rawModes: boolean[] = [];
	readonly #chunks: string[];

	constructor(...chunks: string[]) {
		super();
		this.#chunks = chunks;
	}

	setRawMode(mode: boolean) {
		this.rawModes.push(mode);
	}

	setEncoding() {}

	/** Types chunks until a line is complete, like a user at a terminal. */
	resume() {
		setImmediate(() => {
			let chunk: string | undefined;
			while ((chunk = this.#chunks.shift()) !== undefined) {
				this.emit('data', chunk);
				if (['\r', '\n', '\u0003', '\u0004'].some((key) => chunk!.includes(key))) {
					break;
				}
			}
		});
	}

	pause() {}

	async *[Symbol.asyncIterator]() {}
}

function setup(stdin: Input = Readable.from([]), env: Record<string, string> = {}) {
	const dataDir = join(mkdtempSync(join(tmpdir(), 'ui-cli-')), 'data');
	const out: string[] = [];
	const err: string[] = [];
	const io: Io = {
		stdin,
		stdout: { write: (text: string) => out.push(text) },
		stderr: { write: (text: string) => err.push(text) },
		env: { UI_DATA_DIR: dataDir, ...env }
	};
	return {
		io,
		dataDir,
		out: () => out.join(''),
		err: () => err.join(''),
		login: async (name: string, password: string) => {
			const db = openDatabase(dataDir);
			try {
				return await verifyLogin(db, name, password);
			} finally {
				db.close();
			}
		}
	};
}

describe('set-password', () => {
	it('reads the password from stdin', async () => {
		const cli = setup(Readable.from([`${PASSWORD}\n`]));
		expect(await run(['set-password', 'admin', '--password-stdin'], cli.io)).toBe(0);
		expect(cli.out()).toBe("Account 'admin' created.\n");
		expect(await cli.login('admin', PASSWORD)).not.toBeNull();

		cli.io.stdin = Readable.from(['another good password\r\n']);
		expect(await run(['set-password', 'admin', '--password-stdin'], cli.io)).toBe(0);
		expect(cli.out()).toContain("Password of 'admin' changed; its sessions ended.\n");
		expect(await cli.login('admin', 'another good password')).not.toBeNull();
	});

	it('asks twice on a terminal without showing the password', async () => {
		const terminal = new Terminal(
			`${PASSWORD}x\u007f\r`,
			`${PASSWORD.slice(0, 5)}`,
			`${PASSWORD.slice(5)}\n`
		);
		const cli = setup(terminal);
		expect(await run(['set-password', 'admin'], cli.io)).toBe(0);
		expect(cli.err()).toBe('Password: \nRepeat password: \n');
		expect(cli.err()).not.toContain(PASSWORD);
		expect(terminal.rawModes).toEqual([true, false, true, false]);
		expect(await cli.login('admin', PASSWORD)).not.toBeNull();
	});

	it('stops if the passwords differ', async () => {
		const cli = setup(new Terminal(`${PASSWORD}\r`, 'something else entirely\r'));
		expect(await run(['set-password', 'admin'], cli.io)).toBe(1);
		expect(cli.err()).toContain("The passwords don't match\n");
		expect(await cli.login('admin', PASSWORD)).toBeNull();
	});

	it('checks the password before asking again', async () => {
		const cli = setup(new Terminal('short\r'));
		expect(await run(['set-password', 'admin'], cli.io)).toBe(1);
		expect(cli.err()).toBe('Password: \nThe password must have at least 12 characters\n');
	});

	it.each(['\u0003', '\u0004'])('can be cancelled with %j', async (key) => {
		const cli = setup(new Terminal(`abc${key}`));
		expect(await run(['set-password', 'admin'], cli.io)).toBe(1);
		expect(cli.err()).toContain('Cancelled\n');
	});

	it('needs --password-stdin without a terminal', async () => {
		const cli = setup();
		expect(await run(['set-password', 'admin'], cli.io)).toBe(2);
		expect(cli.err()).toContain('Not a terminal: pass the password with --password-stdin');
	});

	it('rejects an invalid name', async () => {
		const cli = setup(Readable.from([PASSWORD]));
		expect(await run(['set-password', 'a b', '--password-stdin'], cli.io)).toBe(1);
		expect(cli.err()).toContain('A username has 1 to 64 letters');
		expect(await run(['set-password', '-', '--password-stdin'], cli.io)).toBe(2);
	});
});

describe('list-users and logout-all', () => {
	it('show the accounts and end their sessions', async () => {
		const cli = setup(Readable.from([PASSWORD]));
		expect(await run(['list-users'], cli.io)).toBe(0);
		expect(cli.out()).toBe(
			'No accounts yet - create one with: shop-notify-ui set-password NAME\n'
		);

		await run(['set-password', 'admin', '--password-stdin'], cli.io);
		expect(await run(['list-users'], cli.io)).toBe(0);
		expect(cli.out()).toMatch(
			/admin {2}created \d{4}-\d\d-\d\d \d\d:\d\d, password changed \d{4}-\d\d-\d\d \d\d:\d\d, 0 active session\(s\)\n$/
		);

		expect(await run(['logout-all', 'admin'], cli.io)).toBe(0);
		expect(cli.out()).toContain("Ended 0 session(s) of 'admin'.\n");
		expect(await run(['logout-all', 'nobody'], cli.io)).toBe(1);
		expect(cli.err()).toBe("There is no user 'nobody'\n");
	});
});

describe('usage', () => {
	it.each([
		[[], 'No command given'],
		[['help'], "Unknown command 'help'"],
		[['list-users', '--all'], 'Unknown option --all'],
		[['logout-all', '--password-stdin', 'x'], 'Unknown option --password-stdin'],
		[['logout-all'], 'logout-all needs an account name'],
		[['set-password', 'a', 'b'], 'set-password needs an account name'],
		[['list-users', 'admin'], 'list-users needs no arguments']
	])('%j: %s', async (args, message) => {
		const cli = setup();
		expect(await run(args, cli.io)).toBe(2);
		expect(cli.err()).toContain(`${message}\n\nUsage:\n`);
	});

	it('reports an invalid configuration', async () => {
		const cli = setup(undefined, { UI_SESSION_DAYS: '0' });
		expect(await run(['list-users'], cli.io)).toBe(1);
		expect(cli.err()).toContain('UI_SESSION_DAYS must be');
	});

	it('passes unexpected errors on', async () => {
		const file = join(mkdtempSync(join(tmpdir(), 'ui-cli-')), 'file');
		writeFileSync(file, '');
		const cli = setup(undefined, { UI_DATA_DIR: file });
		await expect(run(['list-users'], cli.io)).rejects.toThrow();
	});
});
