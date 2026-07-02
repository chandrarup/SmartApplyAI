#!/usr/bin/env node
import fs from "fs";
import path from "path";
import { fileURLToPath } from "url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const happyDir = path.join(__dirname, "fixtures/happy");
const advDir = path.join(__dirname, "fixtures/adversarial");

const HAPPY_EXPECTED = {
  test_greenhouse: { must_include: ["Software Engineer", "AI Platform"], must_exclude: ["cookie policy"], min_len: 80, max_len: 50000, expected_platform: "greenhouse" },
  test_greenhouse_real: { must_include: ["Software Engineer"], must_exclude: [], min_len: 80, max_len: 50000, expected_platform: "greenhouse" },
  test_lever: { must_include: ["Senior AI Engineer", "Acme Corp"], must_exclude: [], min_len: 80, max_len: 50000, expected_platform: "lever" },
  test_workday: { must_include: ["Machine Learning Engineer", "InnovateCo"], must_exclude: [], min_len: 80, max_len: 50000, expected_platform: "workday" },
  test_linkedin: { must_include: ["Easy Apply"], must_exclude: [], min_len: 50, max_len: 50000, expected_platform: "linkedin" },
  test_icims: { must_include: ["iCIMS"], must_exclude: [], min_len: 50, max_len: 50000, expected_platform: "icims" },
  test_smartrecruiters: { must_include: ["SmartRecruiters"], must_exclude: [], min_len: 50, max_len: 50000, expected_platform: "smartrecruiters" },
  test_taleo: { must_include: ["Taleo"], must_exclude: [], min_len: 50, max_len: 50000, expected_platform: "taleo" },
  test_bamboohr: { must_include: ["BambooHR"], must_exclude: [], min_len: 50, max_len: 50000, expected_platform: "bamboohr" },
  test_generic: { must_include: ["Generic"], must_exclude: [], min_len: 50, max_len: 50000, expected_platform: "generic" },
};

for (const [base, exp] of Object.entries(HAPPY_EXPECTED)) {
  fs.writeFileSync(path.join(happyDir, `${base}.expected.json`), JSON.stringify(exp, null, 2));
}

const SKILL = "PyTorch";
const SKILL2 = "retrieval-augmented generation";
const JUNK = "Related jobs";

const adversarial = {
  "jd_in_iframe.html": `<!DOCTYPE html><html><head><title>iframe JD</title></head><body>
<nav>Apply now navigation</nav>
<iframe srcdoc="<html><body><main><p>${SKILL} and ${SKILL2} required for ML platform role. Build LangChain pipelines.</p></main></body></html>"></iframe>
</body></html>`,
  "jd_in_shadow_dom.html": `<!DOCTYPE html><html><head><title>shadow</title></head><body>
<div id="host"></div><script>
const host=document.getElementById('host');
const root=host.attachShadow({mode:'open'});
root.innerHTML='<div class="job-description"><p>${SKILL} engineer role. ${SKILL2} experience preferred.</p></div>';
</script></body></html>`,
  "jd_collapsed_readmore.html": `<!DOCTYPE html><html><body>
<div class="job-post" style="max-height:40px;overflow:hidden"><p>${SKILL} ${SKILL2} TensorFlow Kubernetes MLOps role description with detailed responsibilities.</p></div>
<button>Read more</button>
<div class="related">Related jobs sidebar filler text</div>
</body></html>`,
  "multi_posting.html": `<!DOCTYPE html><html><body>
<div class="job-post"><h2>Data Analyst</h2><p>Excel SQL only.</p></div>
<div class="job-post" id="target-posting"><h2>ML Engineering Intern TARGET_MARKER</h2><p>${SKILL} ${SKILL2} internship summer 2026.</p></div>
<div class="related-jobs">${JUNK} Data Analyst copy</div>
</body></html>`,
  "lazy_loaded.html": `<!DOCTYPE html><html><body><div id="jd-root">Loading...</div>
<script>setTimeout(()=>{document.getElementById('jd-root').innerHTML='<article class="job-description"><p>${SKILL} ${SKILL2} lazy injected JD.</p></article>';},800);</script>
</body></html>`,
  "no_semantic_main.html": `<!DOCTYPE html><html><body>
<div><div><div><p>${SKILL} ${SKILL2} role without semantic landmarks.</p></div></div></div>
</body></html>`,
  "junk_heavy.html": `<!DOCTYPE html><html><body>
<div class="nav-footer">${JUNK} Apply now cookie consent footer navigation</div>
<div style="font-size:10px">Short nav link home careers blog</div>
<div><p>${SKILL} ${SKILL2} actual job description buried in plain div with enough text to be meaningful for extraction heuristics and skill matching.</p></div>
<div class="related-jobs">${JUNK} Staff Engineer Principal role</div>
</body></html>`,
  "entities_unicode.html": `<!DOCTYPE html><html><body><div id="content" class="job-post">
<p>Skills: ${SKILL} &amp; NLP — ${SKILL2} \u2014 \u00a0TensorFlow \ud83d\ude80</p>
</div></body></html>`,
  "login_wall.html": `<!DOCTYPE html><html><body>
<div class="login"><h1>Sign in to view this job</h1><p>Please log in to LinkedIn to continue.</p></div>
</body></html>`,
  "pdf_embedded.html": `<!DOCTYPE html><html><body>
<embed type="application/pdf" src="data:application/pdf;base64,JVBERi0xLjQK" width="600" height="400"/>
<object data="job.pdf" type="application/pdf">PDF only JD</object>
</body></html>`,
};

const hugeBody = `${SKILL} ${SKILL2} `.repeat(2500) + "end marker HUGE_JD_TAIL";
adversarial["huge_jd.html"] = `<!DOCTYPE html><html><body><div id="content" class="job-post"><p>${hugeBody}</p></div></body></html>`;

const adversarialExpected = {
  "jd_in_iframe": { must_include: [SKILL, SKILL2], must_exclude: [JUNK], min_len: 40, max_len: 50000, expected_platform: "generic" },
  "jd_in_shadow_dom": { must_include: [SKILL, SKILL2], must_exclude: [], min_len: 40, max_len: 50000, expected_platform: "generic" },
  "jd_collapsed_readmore": { must_include: [SKILL, SKILL2], must_exclude: [JUNK], min_len: 40, max_len: 50000, expected_platform: "generic" },
  "multi_posting": { must_include: ["TARGET_MARKER", SKILL], must_exclude: ["Data Analyst"], min_len: 30, max_len: 50000, expected_platform: "generic" },
  "lazy_loaded": { must_include: [SKILL, SKILL2], must_exclude: [], min_len: 40, max_len: 50000, expected_platform: "generic" },
  "no_semantic_main": { must_include: [SKILL, SKILL2], must_exclude: [], min_len: 30, max_len: 50000, expected_platform: "generic" },
  "junk_heavy": { must_include: [SKILL, SKILL2], must_exclude: [JUNK], min_len: 40, max_len: 50000, expected_platform: "generic" },
  "entities_unicode": { must_include: [SKILL, "TensorFlow"], must_exclude: [], min_len: 20, max_len: 50000, expected_platform: "generic" },
  "huge_jd": { must_include: ["HUGE_JD_TAIL", SKILL], must_exclude: [], min_len: 20000, max_len: 40000, expected_platform: "generic" },
  "login_wall": { must_include: ["Sign in to view this job"], must_exclude: [SKILL], min_len: 20, max_len: 500, expected_platform: "generic" },
  "pdf_embedded": { must_include: [SKILL], must_exclude: [], min_len: 5, max_len: 50000, expected_platform: "generic" },
};

for (const [file, html] of Object.entries(adversarial)) {
  fs.writeFileSync(path.join(advDir, file), html);
  const base = file.replace(".html", "");
  fs.writeFileSync(path.join(advDir, `${base}.expected.json`), JSON.stringify(adversarialExpected[base], null, 2));
}

console.log("Built happy expected + adversarial fixtures");
