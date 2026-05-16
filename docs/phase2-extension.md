# Phase 2 Extension Notes

The Chrome extension remains in this repository for future ATS autofill work.

Current status:

- Backend defaults are now aligned to `http://127.0.0.1:5001`
- Extension settings should point to that backend URL
- Resume tailoring from the web is still present, but the dashboard-first workflow is the supported Phase 1 path

If you want to work on the extension next, focus on:

1. ATS field detection and form-filling reliability
2. Platform-specific integration tests
3. Chrome extension UX cleanup
4. End-to-end automation against the standardized backend
