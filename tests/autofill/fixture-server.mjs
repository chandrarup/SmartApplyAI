#!/usr/bin/env node
import http from "http";
import fs from "fs";
import path from "path";
import { fileURLToPath } from "url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const FIXTURES = path.join(__dirname, "fixtures");
const PORT = Number(process.env.AUTOFILL_FIXTURE_PORT || 8766);

const PLATFORM_ROUTES = {
  "/test/greenhouse.html": "platforms/test_greenhouse.html",
  "/test/greenhouse_real.html": "platforms/test_greenhouse_real.html",
  "/test/greenhouse-real.html": "platforms/test_greenhouse_real.html",
  "/test/lever.html": "platforms/test_lever.html",
  "/test/workday.html": "platforms/test_workday.html",
  "/test/linkedin.html": "platforms/test_linkedin.html",
  "/test/icims.html": "platforms/test_icims.html",
  "/test/smartrecruiters.html": "platforms/test_smartrecruiters.html",
  "/test/taleo.html": "platforms/test_taleo.html",
  "/test/bamboohr.html": "platforms/test_bamboohr.html",
  "/test/generic.html": "platforms/test_generic.html",
};

function resolve(urlPath) {
  const clean = (urlPath || "/").split("?")[0];
  if (PLATFORM_ROUTES[clean]) return path.join(FIXTURES, PLATFORM_ROUTES[clean]);
  if (clean.startsWith("/variants/")) {
    return path.join(FIXTURES, "variants", path.basename(clean));
  }
  return null;
}

const server = http.createServer((req, res) => {
  const file = resolve(req.url);
  if (!file || !fs.existsSync(file)) {
    res.writeHead(404);
    res.end("not found");
    return;
  }
  res.writeHead(200, { "Content-Type": "text/html; charset=utf-8" });
  fs.createReadStream(file).pipe(res);
});

server.listen(PORT, "127.0.0.1", () => {
  console.log(`Autofill fixture server http://127.0.0.1:${PORT}`);
});
