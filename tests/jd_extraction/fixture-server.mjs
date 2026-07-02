#!/usr/bin/env node
/** Static server for JD extraction fixtures — paths match detectPlatform() test routes. */
import http from "http";
import fs from "fs";
import path from "path";
import { fileURLToPath } from "url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const FIXTURES = path.join(__dirname, "fixtures");
const PORT = Number(process.env.JD_FIXTURE_PORT || 8765);

const HAPPY_MAP = {
  "/test/greenhouse.html": "happy/test_greenhouse.html",
  "/test/greenhouse": "happy/test_greenhouse.html",
  "/test/greenhouse_real.html": "happy/test_greenhouse_real.html",
  "/test/greenhouse-real.html": "happy/test_greenhouse_real.html",
  "/test/lever.html": "happy/test_lever.html",
  "/test/lever": "happy/test_lever.html",
  "/test/workday.html": "happy/test_workday.html",
  "/test/workday": "happy/test_workday.html",
  "/test/linkedin.html": "happy/test_linkedin.html",
  "/test/linkedin": "happy/test_linkedin.html",
  "/test/icims.html": "happy/test_icims.html",
  "/test/icims": "happy/test_icims.html",
  "/test/smartrecruiters.html": "happy/test_smartrecruiters.html",
  "/test/smartrecruiters": "happy/test_smartrecruiters.html",
  "/test/taleo.html": "happy/test_taleo.html",
  "/test/taleo": "happy/test_taleo.html",
  "/test/bamboohr.html": "happy/test_bamboohr.html",
  "/test/bamboohr": "happy/test_bamboohr.html",
  "/test/generic.html": "happy/test_generic.html",
  "/test/generic": "happy/test_generic.html",
};

function resolveFile(urlPath) {
  const clean = urlPath.split("?")[0];
  if (HAPPY_MAP[clean]) return path.join(FIXTURES, HAPPY_MAP[clean]);
  if (clean.startsWith("/adversarial/")) {
    const rel = clean.slice("/adversarial/".length);
    return path.join(FIXTURES, "adversarial", rel);
  }
  return null;
}

const server = http.createServer((req, res) => {
  const file = resolveFile(req.url || "/");
  if (!file || !fs.existsSync(file)) {
    res.writeHead(404);
    res.end("not found");
    return;
  }
  res.writeHead(200, { "Content-Type": "text/html; charset=utf-8" });
  fs.createReadStream(file).pipe(res);
});

server.listen(PORT, "127.0.0.1", () => {
  console.log(`JD fixture server http://127.0.0.1:${PORT}`);
});
