import { build } from 'esbuild';
import { copyFile, mkdir } from 'node:fs/promises';
await mkdir('dist', { recursive: true });
await build({ entryPoints: ['src/main.tsx'], outfile: 'dist/main.js', bundle: true, platform: 'node', format: 'esm', target: 'node22', packages: 'external', sourcemap: true });
await build({ entryPoints: ['../v8-agent-os-cli/src/core_control.mjs'], outfile: 'dist/core-control.mjs', bundle: true, platform: 'node', format: 'esm', target: 'node22', packages: 'external', sourcemap: true });
await build({ entryPoints: ['../v8-agent-os-cli/src/cli.mjs'], outfile: 'dist/cli.mjs', bundle: true, platform: 'node', format: 'esm', target: 'node22', packages: 'external', sourcemap: true });
await copyFile('../../LICENSE', 'LICENSE');
