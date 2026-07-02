#!/usr/bin/env node
/** Copy backend/test_*.html into tests/autofill/fixtures/platforms/ */
import fs from "fs";
import path from "path";
import { fileURLToPath } from "url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const ROOT = path.resolve(__dirname, "../..");
const SRC = path.join(ROOT, "backend");
const DEST = path.join(__dirname, "fixtures/platforms");

fs.mkdirSync(DEST, { recursive: true });
for (const f of fs.readdirSync(SRC).filter((x) => x.startsWith("test_") && x.endsWith(".html"))) {
  fs.copyFileSync(path.join(SRC, f), path.join(DEST, f));
  console.log("copied", f);
}
