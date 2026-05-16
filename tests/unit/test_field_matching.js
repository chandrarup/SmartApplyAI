/**
 * Unit tests for FIELD_PATTERNS matching and label resolution logic.
 *
 * Run with: node tests/unit/test_field_matching.js
 *
 * Why unit-test these: They're pure functions. No browser needed. Every regex
 * change should be validated here first before touching the browser. These tests
 * cover the exact label strings seen in real ATS forms (Workday, Greenhouse, iCIMS).
 *
 * Philosophy (Chandra's teaching notes):
 *   - Each test = one specific real-world label string observed in the wild
 *   - Negative tests matter as much as positive ones (don't over-match)
 *   - Tests reveal bugs before production does
 */

// ─── Minimal browser environment mock ──────────────────────────────────────
// content.js references browser globals. We stub them here so we can import
// just the logic functions without a real browser.
global.document  = { querySelector: () => null, querySelectorAll: () => [] };
global.location  = { hostname: "test.workday.com", pathname: "/en-US/apply" };
global.window    = global;
global.chrome    = { runtime: { sendMessage: () => {} } };

// ─── FIELD_PATTERNS (copy of the subset we're testing) ────────────────────
// In production these live in content.js. We copy here to test the regex logic
// independently of the full content.js file.
const FIELD_PATTERNS = [
  { key: "first_name",            patterns: [/^first\s*name/i, /^first$/i, /given\s*name/i, /^forename/i] },
  { key: "last_name",             patterns: [/^last\s*name/i, /^last$/i, /family\s*name/i, /^surname/i] },
  { key: "middle_name",           patterns: [/^middle\s*name/i, /^middle\s*initial/i] },
  { key: "email",                 patterns: [/e[\s-]?mail(\s*address)?/i] },
  { key: "phone",                 patterns: [/^phone$|^phone\s*number$|^mobile\s*number$|^cell\s*phone$/i] },
  { key: "phone_extension",       patterns: [/phone\s*ext|extension/i] },
  { key: "address",               patterns: [/^address\s*1?$|^street\s*address/i, /^mailing\s*address/i] },
  { key: "city",                  patterns: [/^city$/i, /^city\s*\/\s*town/i] },
  { key: "state",                 patterns: [/^state$|^province$|^state\s*\/\s*province/i] },
  { key: "zip",                   patterns: [/^zip$|^zip\s*code$|^postal\s*code$/i] },
  { key: "country",               patterns: [/^country$/i, /^country\s*of\s*(residence|citizenship)/i] },
  { key: "linkedin",              patterns: [/linkedin/i] },
  { key: "github",                patterns: [/github/i] },
  { key: "portfolio",             patterns: [/portfolio|personal\s*website|personal\s*url/i] },
  { key: "work_authorization",    patterns: [/authorized\s*to\s*work|work\s*auth|legally\s*authorized/i] },
  { key: "requires_sponsorship",  patterns: [/require.*sponsor|visa\s*sponsor|need.*sponsor/i] },
  { key: "salary",                patterns: [/salary|compensation|desired\s*pay|expected\s*salary/i] },
  { key: "age_18_or_over",        patterns: [/18\s*years?\s*of\s*age|at\s*least\s*18|are\s*you\s*18/i] },
  { key: "start_date",            patterns: [/start\s*date|available\s*(to\s*start|date)/i] },
  { key: "years_of_experience",   patterns: [/years?\s*(of\s*)?(experience|exp)|how\s*many\s*years/i] },
  { key: "highest_education",     patterns: [/highest\s*(level\s*of\s*)?education|education\s*level/i] },
  { key: "university",            patterns: [/^university$|^college$|^school$|^institution/i] },
  { key: "degree",                patterns: [/^degree$|^degree\s*type$|^degree\s*level$/i] },
  { key: "field_of_study",        patterns: [/^field\s*of\s*study$|^major$|^area\s*of\s*study/i] },
  { key: "current_company",       patterns: [/current\s*(company|employer|organization)|employer\s*name/i] },
  { key: "current_title",         patterns: [/current\s*(title|position|role)|job\s*title/i] },
  { key: "cover_letter",          patterns: [/cover\s*letter/i] },
  { key: "referral_source",       patterns: [/how\s*did\s*you\s*(hear|find|learn|know)/i, /referral|referred\s*by/i] },
  { key: "gender",                patterns: [/^gender$/i] },
  { key: "race",                  patterns: [/^race$|^ethnicity$|^race\s*\/\s*ethnicity/i] },
  { key: "veteran_status",        patterns: [/veteran/i] },
  { key: "disability_status",     patterns: [/disability|disabled/i] },
  { key: "pronouns",              patterns: [/^pronouns?$/i] },
];

function matchFieldKey(labelText) {
  if (!labelText) return null;
  // Strip trailing asterisks and whitespace (as getLabelForInput does in content.js)
  const text = labelText.replace(/[\s*✱]+$/, "").trim();
  if (!text) return null;
  for (const { key, patterns } of FIELD_PATTERNS) {
    if (patterns.some(p => p.test(text))) return key;
  }
  return null;
}

// ─── STATE_ABBREVIATIONS (copy from content.js rewrite) ──────────────────
const STATE_ABBREVIATIONS = {
  "alabama": "AL", "alaska": "AK", "arizona": "AZ", "arkansas": "AR",
  "california": "CA", "colorado": "CO", "connecticut": "CT", "delaware": "DE",
  "florida": "FL", "georgia": "GA", "hawaii": "HI", "idaho": "ID",
  "illinois": "IL", "indiana": "IN", "iowa": "IA", "kansas": "KS",
  "kentucky": "KY", "louisiana": "LA", "maine": "ME", "maryland": "MD",
  "massachusetts": "MA", "michigan": "MI", "minnesota": "MN", "mississippi": "MS",
  "missouri": "MO", "montana": "MT", "nebraska": "NE", "nevada": "NV",
  "new hampshire": "NH", "new jersey": "NJ", "new mexico": "NM", "new york": "NY",
  "north carolina": "NC", "north dakota": "ND", "ohio": "OH", "oklahoma": "OK",
  "oregon": "OR", "pennsylvania": "PA", "rhode island": "RI", "south carolina": "SC",
  "south dakota": "SD", "tennessee": "TN", "texas": "TX", "utah": "UT",
  "vermont": "VT", "virginia": "VA", "washington": "WA", "west virginia": "WV",
  "wisconsin": "WI", "wyoming": "WY", "district of columbia": "DC",
};

function stateAbbrevLookup(value) {
  const lower = value.toLowerCase().trim();
  // Full name → abbreviation
  if (STATE_ABBREVIATIONS[lower]) return STATE_ABBREVIATIONS[lower];
  // Abbreviation → full name
  const fullName = Object.keys(STATE_ABBREVIATIONS).find(
    k => STATE_ABBREVIATIONS[k] === lower.toUpperCase()
  );
  return fullName || null;
}

// ─── Test runner ──────────────────────────────────────────────────────────
let passed = 0, failed = 0;
const failures = [];

function test(description, actual, expected) {
  if (actual === expected) {
    console.log(`  ✓ ${description}`);
    passed++;
  } else {
    console.log(`  ✗ ${description}`);
    console.log(`      Expected: ${JSON.stringify(expected)}`);
    console.log(`      Got:      ${JSON.stringify(actual)}`);
    failed++;
    failures.push(description);
  }
}

function section(name) {
  console.log(`\n── ${name} ──`);
}

// ─────────────────────────────────────────────────────────────────────────
// FIRST NAME
// ─────────────────────────────────────────────────────────────────────────
section("First Name");
test("basic",                   matchFieldKey("First Name"),              "first_name");
test("trailing asterisk",       matchFieldKey("First Name *"),            "first_name");  // stripped by cleaner
test("lowercase",               matchFieldKey("first name"),              "first_name");
test("no space (camelCase)",    matchFieldKey("FirstName"),               "first_name");  // \s* matches zero spaces — correct for name= attrs
test("given name (LinkedIn)",   matchFieldKey("Given Name"),              "first_name");
test("forename (UK)",           matchFieldKey("Forename"),                "first_name");
test("just First",              matchFieldKey("First"),                   "first_name");

// ─────────────────────────────────────────────────────────────────────────
// LAST NAME
// ─────────────────────────────────────────────────────────────────────────
section("Last Name");
test("basic",                   matchFieldKey("Last Name"),               "last_name");
test("family name",             matchFieldKey("Family Name"),             "last_name");
test("surname (UK)",            matchFieldKey("Surname"),                 "last_name");
test("just Last",               matchFieldKey("Last"),                    "last_name");

// ─────────────────────────────────────────────────────────────────────────
// EMAIL
// ─────────────────────────────────────────────────────────────────────────
section("Email");
test("basic",                   matchFieldKey("Email"),                   "email");
test("email address",           matchFieldKey("Email Address"),           "email");
test("hyphenated e-mail",       matchFieldKey("E-mail"),                  "email");
test("spaced e mail",           matchFieldKey("E mail"),                  "email");
test("work email",              matchFieldKey("Work Email"),              "email");

// ─────────────────────────────────────────────────────────────────────────
// PHONE — must NOT match extension
// ─────────────────────────────────────────────────────────────────────────
section("Phone (strict — must not over-match)");
test("basic phone",             matchFieldKey("Phone"),                   "phone");
test("phone number",            matchFieldKey("Phone Number"),            "phone");
test("cell phone",              matchFieldKey("Cell Phone"),              "phone");
test("mobile number",           matchFieldKey("Mobile Number"),           "phone");
// These MUST NOT match phone — they have their own keys
test("phone extension → ext key", matchFieldKey("Phone Extension"),      "phone_extension");
test("phone ext",               matchFieldKey("Phone Ext"),               "phone_extension");

// ─────────────────────────────────────────────────────────────────────────
// ADDRESS / LOCATION
// ─────────────────────────────────────────────────────────────────────────
section("Address & Location");
test("address",                 matchFieldKey("Address"),                 "address");
test("address line 1",         matchFieldKey("Address 1"),               "address");
test("street address",          matchFieldKey("Street Address"),          "address");
test("mailing address",         matchFieldKey("Mailing Address"),         "address");
test("city",                    matchFieldKey("City"),                    "city");
test("city/town",               matchFieldKey("City / Town"),             "city");
test("state basic",             matchFieldKey("State"),                   "state");
test("province (Canada)",       matchFieldKey("Province"),                "state");
test("state/province",          matchFieldKey("State / Province"),        "state");
test("zip",                     matchFieldKey("Zip"),                     "zip");
test("zip code",                matchFieldKey("Zip Code"),                "zip");
test("postal code",             matchFieldKey("Postal Code"),             "zip");
test("country",                 matchFieldKey("Country"),                 "country");

// ─────────────────────────────────────────────────────────────────────────
// WORK AUTHORIZATION
// ─────────────────────────────────────────────────────────────────────────
section("Work Authorization & Sponsorship");
test("authorized to work",      matchFieldKey("Are you authorized to work in the US?"),   "work_authorization");
test("work authorization",      matchFieldKey("Work Authorization"),                       "work_authorization");
test("legally authorized",      matchFieldKey("Are you legally authorized to work?"),      "work_authorization");
test("require sponsorship",     matchFieldKey("Do you require visa sponsorship?"),         "requires_sponsorship");
test("need sponsorship",        matchFieldKey("Will you need sponsorship?"),               "requires_sponsorship");
test("visa sponsor",            matchFieldKey("Visa Sponsorship"),                         "requires_sponsorship");

// ─────────────────────────────────────────────────────────────────────────
// SALARY / COMPENSATION
// ─────────────────────────────────────────────────────────────────────────
section("Salary");
test("salary",                  matchFieldKey("Salary"),                  "salary");
test("desired salary",          matchFieldKey("Desired Salary"),          "salary");
test("expected salary",         matchFieldKey("Expected Salary"),         "salary");
test("compensation",            matchFieldKey("Compensation"),            "salary");
test("desired pay",             matchFieldKey("Desired Pay"),             "salary");

// ─────────────────────────────────────────────────────────────────────────
// AGE VERIFICATION
// ─────────────────────────────────────────────────────────────────────────
section("Age Verification");
test("18 years of age",         matchFieldKey("Are you 18 years of age or older?"),  "age_18_or_over");
test("at least 18",             matchFieldKey("Are you at least 18?"),                "age_18_or_over");

// ─────────────────────────────────────────────────────────────────────────
// EEO / DIVERSITY FIELDS
// ─────────────────────────────────────────────────────────────────────────
section("EEO / Diversity");
test("gender",                  matchFieldKey("Gender"),                  "gender");
test("race",                    matchFieldKey("Race"),                    "race");
test("ethnicity",               matchFieldKey("Ethnicity"),               "race");
test("race/ethnicity",          matchFieldKey("Race / Ethnicity"),        "race");
test("veteran status",          matchFieldKey("Veteran Status"),          "veteran_status");
test("disability",              matchFieldKey("Disability Status"),       "disability_status");
test("pronouns",                matchFieldKey("Pronouns"),                "pronouns");

// ─────────────────────────────────────────────────────────────────────────
// EDUCATION
// ─────────────────────────────────────────────────────────────────────────
section("Education");
test("highest education",       matchFieldKey("Highest Level of Education"),  "highest_education");
test("education level",         matchFieldKey("Education Level"),              "highest_education");
test("university",              matchFieldKey("University"),                   "university");
test("college",                 matchFieldKey("College"),                      "university");
test("school",                  matchFieldKey("School"),                       "university");
test("degree",                  matchFieldKey("Degree"),                       "degree");
test("degree type",             matchFieldKey("Degree Type"),                  "degree");
test("field of study",          matchFieldKey("Field of Study"),               "field_of_study");
test("major",                   matchFieldKey("Major"),                        "field_of_study");

// ─────────────────────────────────────────────────────────────────────────
// SOCIAL / PORTFOLIO
// ─────────────────────────────────────────────────────────────────────────
section("Social / Portfolio");
test("linkedin",                matchFieldKey("LinkedIn"),                "linkedin");
test("linkedin url",            matchFieldKey("LinkedIn URL"),            "linkedin");
test("linkedin profile",        matchFieldKey("LinkedIn Profile"),        "linkedin");
test("github",                  matchFieldKey("GitHub"),                  "github");
test("portfolio",               matchFieldKey("Portfolio"),               "portfolio");
test("personal website",        matchFieldKey("Personal Website"),        "portfolio");

// ─────────────────────────────────────────────────────────────────────────
// NEGATIVE TESTS — important: these must NOT match anything
// ─────────────────────────────────────────────────────────────────────────
section("Negative Tests (must NOT match)");
test("employee id",             matchFieldKey("Employee ID"),             null);
test("department",              matchFieldKey("Department"),              null);
test("manager name",            matchFieldKey("Manager Name"),            null);
test("empty string",            matchFieldKey(""),                        null);
test("null",                    matchFieldKey(null),                      null);
test("undefined",               matchFieldKey(undefined),                 null);
test("just asterisk",           matchFieldKey("*"),                       null);
test("whitespace only",         matchFieldKey("   "),                     null);

// ─────────────────────────────────────────────────────────────────────────
// STATE ABBREVIATION MAPPING
// ─────────────────────────────────────────────────────────────────────────
section("State Abbreviation Mapping (Bug 3 fix)");
// Full name → abbreviation
test("Texas → TX",              stateAbbrevLookup("Texas"),               "TX");
test("California → CA",         stateAbbrevLookup("California"),          "CA");
test("New York → NY",           stateAbbrevLookup("New York"),            "NY");
test("West Virginia → WV",      stateAbbrevLookup("West Virginia"),       "WV");
test("District of Columbia → DC", stateAbbrevLookup("District of Columbia"), "DC");
// Abbreviation → full name
test("TX → texas",              stateAbbrevLookup("TX"),                  "texas");
test("CA → california",         stateAbbrevLookup("CA"),                  "california");
test("NY → new york",           stateAbbrevLookup("NY"),                  "new york");
// Case insensitivity
test("TEXAS (uppercase) → TX",  stateAbbrevLookup("TEXAS"),               "TX");
test("tx (lowercase) → texas",  stateAbbrevLookup("tx"),                  "texas");
// Invalid
test("ZZ (invalid) → null",     stateAbbrevLookup("ZZ"),                  null);
test("empty → null",            stateAbbrevLookup(""),                    null);

// ─────────────────────────────────────────────────────────────────────────
// LABEL CLEANING (strips trailing * and whitespace)
// ─────────────────────────────────────────────────────────────────────────
section("Label Cleaning (asterisk stripping)");
// The cleaner in getLabelForInput does: label.replace(/[\s*✱]+$/, "").trim()
// We simulate the same here by calling matchFieldKey (which applies it)
test("trailing *",              matchFieldKey("First Name *"),            "first_name");
test("trailing ✱ (star emoji)", matchFieldKey("Email ✱"),                "email");
test("multiple trailing *",     matchFieldKey("Phone ***"),               "phone");
test("leading whitespace",      matchFieldKey("  City  "),                "city");

// ─────────────────────────────────────────────────────────────────────────
// SUMMARY
// ─────────────────────────────────────────────────────────────────────────
console.log(`\n${"─".repeat(50)}`);
console.log(`Results: ${passed} passed, ${failed} failed`);
if (failures.length > 0) {
  console.log(`\nFailed tests:`);
  failures.forEach(f => console.log(`  • ${f}`));
}
console.log(`${"─".repeat(50)}`);
if (failed > 0) process.exit(1);
