# StandOut (working name)

One login for local service businesses. Three panels, nothing to configure per client:

| Panel | What it does | Replaces |
|---|---|---|
| **Content** | One click → 30-day calendar on a fixed weekly rhythm (Teach, Behind the Scenes, Proof, Mistake/Myth, Offer, Local, Story) and a monthly arc (Get Noticed → Build Trust → Prove It → Fill the Schedule). Every day has a hook, a shoot-ready script, filming notes and a caption that links to one funnel. Schedule it, and it posts to the Facebook Page or emails the owner a copy-paste reminder. | Content agency / copywriter, scheduling app |
| **Funnels** | 4 pages already built from the business profile: Free Estimate, Seasonal Special, Same-Day Help, Review & Refer. Each has its own follow-up sequence (text + email) and **matching Facebook ad copy**, so the ad and the page say the same thing. | Funnel builder, autoresponder |
| **Leads** | Pipeline (New → Contacted → Quoted → Won/Lost) holding up to 500 contacts. Every funnel lead is followed up automatically; follow-up stops when the owner moves the lead or the lead texts back. Two-way conversation view, manual text/email, CSV import/export. | CRM |

Setup is one form (business, trade, city, services, offer, guarantee). Everything else fills itself in.

## Run it

```bash
cd standout
pip install -e .            # or: pip install fastapi uvicorn httpx anthropic
uvicorn standout.main:app --port 8000
# open http://localhost:8000 and create an account
```

Tests: `pip install pytest && python -m pytest tests`

## Settings (environment variables)

| Variable | Without it | With it |
|---|---|---|
| `STANDOUT_BASE_URL` | Funnel links use `http://localhost:8000` | Your real domain, e.g. `https://app.yourbrand.com` |
| `ANTHROPIC_API_KEY` | Built-in templates write the scripts | "Write with AI" checkbox appears; Claude writes trade-specific scripts on the same rhythm |
| `SMTP_HOST`, `SMTP_PORT`, `SMTP_USER`, `SMTP_PASSWORD`, `SMTP_FROM` | Emails are logged as **simulated** (shown in the conversation) | Emails really send |
| `TWILIO_ACCOUNT_SID`, `TWILIO_AUTH_TOKEN`, `TWILIO_FROM_NUMBER` | Texts are logged as **simulated** | Texts really send; point the number's inbound webhook at `/hooks/sms` so replies stop follow-up |
| `STANDOUT_ACCESS_CODE` | Anyone can sign up | Sign-up needs the code you hand out after payment |
| `STANDOUT_CONTACT_LIMIT` | 500 | Change the plan cap |
| `STANDOUT_DATA_DIR` | `./data` | Where the SQLite database lives |

Demo mode (no Twilio/SMTP) is deliberate: on the live session you can submit a funnel form from your phone and show the texts and emails "sent" in the lead's conversation, without texting anyone.

## Live demo flow (45-minute segment)

1. **Setup (2 min):** create an account for an attendee's business and fill the one form.
2. **Content (10 min):** click *Make my 30-day plan*. Walk the weekly rhythm and the monthly arc. Open Friday's Offer script, then copy the caption.
3. **Funnels (10 min):** open the Free Estimate page on your phone. Show the matching Facebook ad copy. This is the bridge from the ads hour: the ad's promise and the page's promise match.
4. **Leads (10 min):** submit the form from your phone. The lead lands in *New*, and the instant text and email show in the conversation. Drag the lead to *Contacted* and the follow-up stops.
5. **Savings (rest):** a scheduler, a funnel builder, a CRM with automations and a content writer, all replaced by one login.

## Before charging customers (not built yet)

- **Text messaging compliance:** US carriers require A2P 10DLC registration per business. Plan for one Twilio subaccount and number per customer, registered during onboarding.
- **Email deliverability:** send through a provider (Postmark, SendGrid, SES) with your domain authenticated (SPF/DKIM).
- **Auto-posting:** Facebook Page posting works with a Page access token. Selling it to many businesses needs a Meta app that has passed App Review (`pages_manage_posts`). Until then, the email reminder fallback works for every account.
- **Billing:** take payment with Stripe and hand out the access code, or add Stripe subscriptions.
- **Missed-call text-back:** needs a Twilio voice webhook. It's the natural next feature.
- **Hosting:** one small VPS works. Run behind HTTPS with a single uvicorn worker, because the follow-up worker runs in-process. Back up the `data/` folder.
