/**
 * shop-notify-ui: manage the accounts of the web UI on the server.
 *
 *   shop-notify-ui set-password NAME [--password-stdin]   create the account or change its password
 *   shop-notify-ui list-users                             show the accounts
 *   shop-notify-ui logout-all NAME                        end all sessions of an account
 *
 * Runs directly with Node (the types are stripped), like ``node src/lib/server/cli.ts``.
 */
import { resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { ConfigError, loadConfig } from './config.ts';
import { now, openDatabase } from './db.ts';
import { checkPassword, listUsers, logoutAll, setPassword, UserError } from './users.ts';

export interface Input extends AsyncIterable<string | Buffer> {
	isTTY?: boolean;
	setRawMode?(mode: boolean): unknown;
	setEncoding(encoding: BufferEncoding): unknown;
	on(event: 'data', listener: (chunk: string) => void): unknown;
	removeListener(event: 'data', listener: (chunk: string) => void): unknown;
	resume(): unknown;
	pause(): unknown;
}

export interface Io {
	stdin: Input;
	stdout: { write(text: string): unknown };
	stderr: { write(text: string): unknown };
	env: Record<string, string | undefined>;
}

const USAGE = `Usage:
  shop-notify-ui set-password NAME [--password-stdin]   create the account or change its password
  shop-notify-ui list-users                             show the accounts
  shop-notify-ui logout-all NAME                        end all sessions of an account
`;

class UsageError extends Error {}

/** Reads a line from the terminal without showing it. */
function readHidden(prompt: string, io: Io): Promise<string> {
	const input = io.stdin;
	io.stderr.write(prompt);
	return new Promise((resolve, reject) => {
		let value = '';
		const finish = () => {
			input.removeListener('data', onData);
			input.setRawMode?.(false);
			input.pause();
			io.stderr.write('\n');
		};
		const onData = (chunk: string) => {
			for (const char of chunk) {
				if (char === '\r' || char === '\n') {
					finish();
					resolve(value);
					return;
				}
				if (char === '\u0003' || char === '\u0004') {
					finish();
					reject(new UserError('Cancelled'));
					return;
				}
				value = char === '\u007f' || char === '\b' ? value.slice(0, -1) : value + char;
			}
		};
		input.setRawMode?.(true);
		input.setEncoding('utf8');
		input.on('data', onData);
		input.resume();
	});
}

async function readAll(io: Io): Promise<string> {
	let text = '';
	for await (const chunk of io.stdin) {
		text += chunk.toString();
	}
	return text.replace(/\r?\n$/, '');
}

async function readNewPassword(io: Io, fromStdin: boolean): Promise<string> {
	if (fromStdin) {
		return readAll(io);
	}
	if (!io.stdin.isTTY) {
		throw new UsageError('Not a terminal: pass the password with --password-stdin');
	}
	const password = await readHidden('Password: ', io);
	checkPassword(password);
	if ((await readHidden('Repeat password: ', io)) !== password) {
		throw new UserError("The passwords don't match");
	}
	return password;
}

function formatTime(seconds: number): string {
	return new Date(seconds * 1000).toISOString().slice(0, 16).replace('T', ' ');
}

/** Runs a command; returns the exit code. */
export async function run(args: string[], io: Io): Promise<number> {
	try {
		const [command, ...rest] = args;
		const options = rest.filter((arg) => arg.startsWith('-'));
		const names = rest.filter((arg) => !arg.startsWith('-'));
		const allowed = command === 'set-password' ? ['--password-stdin'] : [];
		const unknown = options.filter((option) => !allowed.includes(option));
		if (unknown.length) {
			throw new UsageError(`Unknown option ${unknown.join(', ')}`);
		}
		const expected = command === 'list-users' ? 0 : 1;
		if (!['set-password', 'list-users', 'logout-all'].includes(command ?? '')) {
			throw new UsageError(command ? `Unknown command '${command}'` : 'No command given');
		}
		if (names.length !== expected) {
			throw new UsageError(
				`${command} needs ${expected ? 'an account name' : 'no arguments'}`
			);
		}
		const config = loadConfig(io.env);
		const db = openDatabase(config.dataDir);
		try {
			const name = names[0]!;
			if (command === 'set-password') {
				const password = await readNewPassword(io, options.includes('--password-stdin'));
				const created = await setPassword(db, name, password, now());
				io.stdout.write(
					created
						? `Account '${name}' created.\n`
						: `Password of '${name}' changed; its sessions ended.\n`
				);
			} else if (command === 'list-users') {
				const users = listUsers(db, now());
				if (!users.length) {
					io.stdout.write(
						'No accounts yet - create one with: shop-notify-ui set-password NAME\n'
					);
				}
				for (const user of users) {
					io.stdout.write(
						`${user.name}  created ${formatTime(user.created)}, password changed ${formatTime(user.passwordChanged)}, ${user.sessions} active session(s)\n`
					);
				}
			} else {
				io.stdout.write(`Ended ${logoutAll(db, name)} session(s) of '${name}'.\n`);
			}
		} finally {
			db.close();
		}
		return 0;
	} catch (error) {
		if (error instanceof UsageError) {
			io.stderr.write(`${error.message}\n\n${USAGE}`);
			return 2;
		}
		if (error instanceof UserError || error instanceof ConfigError) {
			io.stderr.write(`${error.message}\n`);
			return 1;
		}
		throw error;
	}
}

/* v8 ignore next 4 - the entry point when run as a command */
if (process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
	const io: Io = {
		stdin: process.stdin,
		stdout: process.stdout,
		stderr: process.stderr,
		env: process.env
	};
	process.exitCode = await run(process.argv.slice(2), io);
}
