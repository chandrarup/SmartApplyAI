/**
 * Static VERIFY for Branch C (fix/tailor-ux).
 * Run: node backend/_verify_tailor_ux.mjs
 */
import fs from "fs";
import path from "path";
import { fileURLToPath } from "url";

const html = fs.readFileSync(
  path.join(path.dirname(fileURLToPath(import.meta.url)), "dashboard.html"),
  "utf8",
);

const fails = [];
function ok(cond, msg) { if (!cond) fails.push(msg); }

ok(html.includes('id="tlr-editsModal"'), "C1: edits modal missing");
ok(html.includes('id="tlr-openEditsBtn"'), "C1: Review Edits button missing");
ok(html.includes("tlrOpenEditsModal"), "C1: open helper missing");
ok(html.includes("tlrCloseEditsModal"), "C1: close helper missing");
ok(html.includes("tlr-edits-sec"), "C1: section grouping class missing");
ok(/tlr-stickyBar[\s\S]*tlr-aiRewrite[\s\S]*tlr-pdfBtn/.test(html), "C1: sticky bar must keep AI + PDF");
ok(!/Approve Edits<\/h3>/.test(html), "C1: right-rail Approve Edits card should be gone");

ok(html.includes('id="tlr-jobCtx"'), "C2: tailor job ctx missing");
ok(html.includes('id="rev-jobCtx"'), "C2: review job ctx missing");
ok(html.includes("tlrUpdateJobCtx"), "C2: tailor ctx updater missing");
ok(html.includes("revUpdateJobCtx"), "C2: review ctx updater missing");

ok(html.includes("tlr-sk-add"), "C3: green skill class missing");
ok(html.includes("tlr-sk-del"), "C3: red skill class missing");
ok(html.includes("AI rewrite:"), "C3: AI rewrite toast with diff summary missing");
ok(html.includes("tlrOpenEditsModal()"), "C3: AI rewrite should open edits modal");

if (fails.length) {
  console.error("FAIL:\n - " + fails.join("\n - "));
  process.exit(1);
}
console.log("OK — Branch C static VERIFY passed");
