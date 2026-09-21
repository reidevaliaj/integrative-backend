# Calendar-year journal catalogue

Implemented 10 September 2026 from the publisher's updated commercial brief.

## Products

- Classic: EUR 88 per calendar year, four main issues, both Basic and Medical sections.
- Special: EUR 88 per calendar year, four special issues.
- Combined: EUR 160 per calendar year, all eight issues.
- Single issue: EUR 24 once; both sections of a main issue are included.
- Previous published volumes can be purchased once at the displayed plan price. No historical PDFs were supplied in this release, so only 2026 is listed.

The two SH40 attachments were byte-identical. The catalogue has one special issue (116 pages) and one main issue (194, Basic 32 pages + Medical 83 pages). The current digital single-issue price is EUR 24.

On 21 September 2026, the publisher supplied `OM 194 en-GB.pdf` (115 pages) with the corrected EUR 24 cover, plus `Cover OM 194 en-GB.jpg`. The replacement PDF is split without changing page content: source pages 1-32 replace `194-basic-en.pdf`, and pages 33-115 replace `194-medical-en.pdf`. Existing issue IDs, reader sections, and purchase access are preserved. The public cover URL includes `?v=20260921` to refresh cached thumbnails. Both identical `OM-Logo en-GB` attachments supply the English logo used by the frontend header, footer, and legal-page header. Only the first logo PDF page contains the complete artwork.

No revised SH40 file accompanied that update. Until it is supplied, its original PDF and cover remain in place, and the storefront identifies the older price on the Longevity cover specifically.

## Billing and access

Annual orders cover January 1 through December 31 in Europe/Berlin. Mollie creates an initial first payment and a 12-month subscription starting the following January 1. One-off issues and archive volumes use oneoff payments and do not create recurring subscriptions.

Cancellation through October 31 inclusive stops renewal that year. From November 1, one final January renewal remains payable and cancellation takes effect at the end of the following year. The Mollie schedule is bounded to its consumed payment count plus one. The final paid callback stops any remaining remote schedule. A cancellation never grants an unpaid future volume.

Paid, provider-verified orders are the access ledger. They identify year, issue (if any), plan, and test/live mode. Paid volumes remain readable after cancellation or subscription expiry; future publications in the same year and category become accessible automatically. Refunds/chargebacks remove only the reversed purchase's access. Test payments cannot unlock the live catalogue. Legacy demo/myPOS records are preserved but do not grant access to the new catalogue.

The order records the purchase conditions version accepted before checkout. Client amounts, return URL flags, and unverified callbacks cannot grant access. The backend fetches payments directly from Mollie and checks mode, amount, currency, customer, payment reference, sequence type and metadata. Renewal amounts use the subscription's agreed amount, not the callback amount. Duplicate events do not create duplicate subscriptions or undo cancellations.

## Private files and publication

New PDF files are intentionally ignored by Git because this backend repository is public. Keep originals in app/static/pdfs on the backend and transfer new files privately via SSH. Do not add paid PDFs to the frontend public directory or force-add them to Git.

This release uses:

- sh40-longevity-en.pdf
- 194-basic-en.pdf
- 194-medical-en.pdf

MagazineDocument stores section, filename, page count and display order. To add an issue, deploy its private files and add its metadata and documents through the catalogue seed or database. Assign the correct volume_year and issue_type (classic or special), then publish. Existing paid-volume orders automatically cover it. Public cover images live in the frontend's public/covers directory.

## API additions

- GET /api/v1/subscriptions/plans?volume_year=2026 includes category, authoritative price, payment mode and ownership.
- GET /api/v1/subscriptions/volumes lists published years plus the current year.
- GET /api/v1/subscriptions/all lists the customer's subscriptions in the current payment mode.
- POST /api/v1/subscriptions/cancel accepts subscription_id and returns the effective cancellation date.
- POST /api/v1/payments/mollie/checkout accepts plan_id, volume_year, optional magazine_id, locale and terms_accepted.
- GET /api/v1/magazines/{slug}/document?section=basic or medical independently checks access on the backend. No parameter selects a file path directly.

Schema changes are additive and run through the existing idempotent startup migration mechanism. Back up the application database before deployment. Existing plans and customer payment records are not rewritten.

## Validation and live payments

Run python -m unittest discover -s tests -v. Tests use SQLite and a fake Mollie client; they never charge money or need private PDFs. Frontend validation uses npm run lint and npm run build. Also test a real Mollie test-mode checkout through the deployed storefront and inspect all three PDF sections in the browser.

Keep MOLLIE_MODE=test during client testing. A live switch requires the publisher's verified Mollie profile, enabled payment methods supporting recurring payments, and its live_ API key installed in the backend's private .env as MOLLIE_API_KEY with MOLLIE_MODE=live. No key belongs in Git or the frontend. Retired monthly-price settings are for interpreting historical monthly records only; all new checkouts use the catalogue plan amount.

The existing legal page contains advertising terms, not final subscriber terms, and there is no final privacy policy. The purchase summary reflects the publisher's supplied rules; the publisher still needs to finalise customer-facing legal content before a real customer launch.
