#!/usr/bin/env node
import { main } from "../src/cli.mjs";

main(process.argv.slice(2)).catch((error) => {
  const message = error instanceof Error ? error.message : String(error);
  console.error(`V8OS CLI failed: ${message}`);
  process.exitCode = 1;
});
