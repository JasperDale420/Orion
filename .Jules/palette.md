## 2024-05-23 - API Root Friendly Message
**Learning:** Developers often hit the root URL (`/`) first to check if the service is running. A generic 404 is confusing and unwelcoming.
**Action:** Always include a friendly root endpoint in APIs that returns a welcome message and links to documentation/health checks. This acts as a simple "hello" and signpost for the developer.

## 2026-01-08 - Friendly 404 Handler
**Learning:** When developers mistype a URL, a generic 404 response is unhelpful. Providing a list of "Did you mean?" suggestions (valid routes) significantly improves the developer experience and reduces frustration.
**Action:** Implement custom exception handlers for 404 errors in APIs that return a JSON response containing a list of available top-level resources and a link to the documentation.
