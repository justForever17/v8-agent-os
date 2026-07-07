import { copyFileSync } from "node:fs";

copyFileSync(new URL("../src/styles.css", import.meta.url), new URL("../styles.css", import.meta.url));
