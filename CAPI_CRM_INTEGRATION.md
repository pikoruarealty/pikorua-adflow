# AdFlow ← CRM: Lead Status → Meta Feedback Integration

## What this is

Whenever a lead's status changes in the CRM (marked warm/hot/interested, or
cold/lost/not-interested/broker), call one endpoint on AdFlow. AdFlow figures
out whether it's good or bad news, matches it to the original Meta ad lead,
and reports it back to Meta so ad delivery improves over time. You do not
need to look anything up, hash anything, or know which Meta lead this
corresponds to — AdFlow already tracks that internally.

## Endpoint

```
POST https://<adflow-domain>/capi-lead-update
```

Replace `<adflow-domain>` with wherever AdFlow is actually hosted (ask
whoever deployed it — this is not something I can fill in for you).

## Authentication

No login, no OAuth, no session. Just one header with a fixed shared key:

```
X-Api-Key: cswYZPS4RfVmyxVG0eW2sMhv5Q8CYR1AGfI7Zp8axZI
```

Treat this key like a password — don't log it, don't put it in client-side
JS, don't commit it to a public repo. It's stored server-side in AdFlow's
`.env` as `CRM_CAPI_API_KEY`. If it ever leaks, tell us and we'll rotate it
(one-line change on our side, you'd just need the new value).

## Request body (JSON)

```json
{
  "phone": "9876543210",
  "email": "buyer@example.com",
  "client_status": "warm",
  "buying_status": "",
  "hwc": "",
  "site_visit_status": ""
}
```

| Field               | Required?                          | Notes                                                             |
|---------------------|-------------------------------------|--------------------------------------------------------------------|
| `phone`             | at least one of phone/email         | any format — with/without +91, spaces, dashes. We normalise it.    |
| `email`             | at least one of phone/email         | case-insensitive.                                                  |
| `client_status`     | at least one status field           | e.g. `warm`, `hot`, `interested`, `cold`, `not interested`, `lost`, `broker`, `low budget`. |
| `buying_status`     | optional                            | e.g. `exploring`, `hot`, `not_ready`, `not interested`.             |
| `hwc`               | optional                            | `hot` if you track a separate hot/warm/cold flag.                  |
| `site_visit_status` | optional                            | e.g. `visited`, `confirmed`.                                       |

Send whichever of these four status fields your CRM actually has — you
don't need all of them. AdFlow runs the same classification rules used
everywhere else in the system, so this stays consistent with our own
reporting.

## When to call it

**Any time a lead's status is edited.** No particular order, no batching
needed — call it once per status change, right after you save the change in
your own database. It's safe to call it more than once for the same lead
(e.g. if you retry after a network blip): AdFlow only ever sends one
qualified signal and one disqualified signal per lead to Meta, no matter how
many times you call this.

## Example request

```bash
curl -X POST https://<adflow-domain>/capi-lead-update \
  -H "X-Api-Key: cswYZPS4RfVmyxVG0eW2sMhv5Q8CYR1AGfI7Zp8axZI" \
  -H "Content-Type: application/json" \
  -d '{"phone": "9876543210", "client_status": "warm"}'
```

## Responses

Always HTTP 200 unless the request itself is malformed or the key is wrong
— a "nothing to send yet" case is not an error, so you don't need special
handling for it. Just log the response if you want visibility.

**Sent successfully:**
```json
{"ok": true, "sent": true, "category": "good", "leadgen_id": "1202...", "error": null}
```

**Status not recognised as good or bad (e.g. "new", "follow-up scheduled"):**
```json
{"ok": true, "sent": false, "reason": "unclassified — no signal to send", "category": "unclassified"}
```

**Recognised, but AdFlow hasn't yet matched this phone/email to a Meta lead**
**(normal for very new leads, or if AdFlow's Meta-side sync isn't caught up):**
```json
{"ok": true, "sent": false, "reason": "no Meta leadgen_id mapped yet for this phone/email...", "category": "good"}
```

**Already reported (duplicate call for the same lead+direction):**
```json
{"ok": true, "sent": false, "reason": "already sent for this lead+direction", "category": "good", "leadgen_id": "1202..."}
```

**Wrong or missing API key:**
```json
{"detail": "Missing or invalid X-Api-Key."}
```
(HTTP 401)

## What you don't need to worry about

- Meta credentials, dataset IDs, hashing phone/email — all handled on our side.
- Matching this lead to the original Meta ad — AdFlow does that from its own
  records of who filled out which form.
- Rate limiting or retries on our end — call it once per status change and move on.
