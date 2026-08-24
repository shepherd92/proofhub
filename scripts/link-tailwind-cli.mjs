#!/usr/bin/env node

// Hugo's Tailwind transformer launches the discovered executable as a Node.js
// script. pnpm's hoisted linker creates a shell wrapper on POSIX, so replace
// that wrapper with a direct link to Tailwind's actual Node.js entry point.

import { access, lstat, mkdir, readlink, rm, symlink } from 'node:fs/promises';
import { dirname, join, relative, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

if (process.platform === 'win32') {
  process.exit(0);
}

const repositoryRoot = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const executable = join(repositoryRoot, 'node_modules', '.bin', 'tailwindcss');
const entryPoint = join(
  repositoryRoot,
  'node_modules',
  '@tailwindcss',
  'cli',
  'dist',
  'index.mjs',
);

await access(entryPoint);
await mkdir(dirname(executable), { recursive: true });

try {
  const metadata = await lstat(executable);
  if (metadata.isSymbolicLink()) {
    const currentTarget = resolve(dirname(executable), await readlink(executable));
    if (currentTarget === entryPoint) {
      process.exit(0);
    }
  }
  await rm(executable, { force: true });
} catch (error) {
  if (error.code !== 'ENOENT') {
    throw error;
  }
}

await symlink(relative(dirname(executable), entryPoint), executable);
