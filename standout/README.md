# StandOut (working name)

One login for local service businesses. Three panels, nothing to configure per client:

| Panel | What it does | Replaces |
|---|---|---|
| **Content** | One click → 30-day calendar on a fixed weekly rhythm (Teach, Behind the Scenes, Proof, Mistake/Myth, Offer, Local, Story) and a monthly arc (Get Noticed → Build Trust → Prove It → Fill the Schedule). Every day has a hook, a shoot-ready script, filming notes and a caption that links to one funnel. Schedule it, and it posts to the Facebook Page or emails the owner a copy-paste reminder. | Content agency / copywriter, scheduling app |
| **Funnels** | 4 pages already built from the business profile: Free Estimate, Seasonal Special, Same-Day Help, Review & Refer. Each has its own follow-up sequence (text + email) and **matching Facebook ad copy**, so the ad and the page say the same thing. | Funnel builder, autoresponder |
| **Leads** | Pipeline (New → Contacted → Quoted → Won/Lost) holding up to 500 contacts. Every funnel lead is followed up automatically; follow-up stops when the owner moves the lead or the lead texts back. Two-way conversation view, manual text/email, CSV import/export. | CRM |

Setup is a 10-minute guided onboarding. Everything else fills itself in from it.

## Onboarding (what the customer sees after the setup link)

1. **Brand:** paste their website. We read it and pull the logo, brand colors, font, and the words they use to describe the business. Or they upload a logo and pick colors. Every funnel page then matches their site.
2. **Business:** trade, city, services, offer, guarantee.
3. **Voice:** pick a tone (Friendly neighbor / Straight shooter / Polished professional), words they use and avoid, a real message they've sent a customer, and their sign-off. One click rewrites all 15 follow-up texts and emails in their voice (Claude, using the website copy + those answers). Timing, channels and fill-in fields are kept, and invented prices or promises are not allowed.
4. **Facebook tracking:** Pixel ID + Conversions API token (3-step instructions on screen) + "what a lead is worth", then **Send test event**.
5. **Domain (optional):** `go.theirbusiness.com`. One CNAME record, then a **Check** button.

Finishing builds their first 30-day content plan automatically. Everything stays editable under ⚙️ Setup.

## Admin dashboard (`/admin`, for you)

- **Enroll a customer:** enter business, email, website and contact limit. We create the account, import their brand, and give you a one-time setup link (valid 7 days) to send them.
- **Customer list:** setup progress with what's missing, funnel visits and leads (30 days), contacts vs. limit, tracking health (Meta rejections in the last 24h), domain, and last seen.
- **Customer detail:** per-funnel results, recent Facebook events, quick edits (brand import, colors, domain, contact limit, rewrite in their voice).
- **Open their app and customize:** work inside their account, with a banner and a "Back to admin" link.

Create your admin login on the server (it can't be created from the website):

```bash
python -m standout.cli create-admin you@yourbrand.com
```

## Facebook Pixel + Conversions API

Every funnel event is sent twice: from the browser (Pixel) and from our server (Conversions API). Both copies carry the same event ID, so Meta counts each event once. Customer details are hashed before they leave the server. That recovers the events ad blockers and iPhones hide, so Meta's algorithm has more to learn from.

| Moment | Event sent | Details |
|---|---|---|
| Funnel page opens | PageView + ViewContent | Browser + server, deduplicated, with the click ID (`fbclid` → `fbc`) and the browser ID (`_fbp`) |
| Form submitted | Lead (value = "what a lead is worth") | Browser + server, deduplicated, with hashed email, phone, first and last name, ZIP and an external ID (advanced matching) |
| Lead moved to Contacted | Contact | Server |
| Lead moved to Quoted | QualifiedLead (custom) | Server |
| Lead moved to Won | Purchase (value = job $) | Server, with the original click ID remembered from the lead |

The CRM stages are the real advantage. Once there's volume, ads can optimize for **QualifiedLead** or **Purchase** (actual jobs) instead of form fills. That's how cost per *customer* drops, not just cost per lead. The Graph API version is set by `META_API_VERSION` (default `v26.0`).

## Custom funnel domains

The customer adds a CNAME for `go.theirbusiness.com` → your app host (`STANDOUT_CNAME_TARGET`). Their pages then live at `go.theirbusiness.com/` (Free Estimate), `/special`, `/emergency` and `/referral`. For automatic HTTPS, use Caddy's on-demand TLS. It asks this app before issuing a certificate, so only connected domains get one:

```
{
  on_demand_tls {
    ask http://127.0.0.1:8000/internal/tls-check
  }
}
https:// {
  tls { on_demand }
  reverse_proxy 127.0.0.1:8000
}
```

Run uvicorn with `--proxy-headers` behind Caddy, so the visitor's IP (used by the Conversions API) is correct.

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
| `STANDOUT_DATA_DIR` | `./data` | Where the SQLite database and uploaded logos live |
| `STANDOUT_CNAME_TARGET` | Your base host | The hostname customers point their funnel domain at |
| `META_API_VERSION` | `v26.0` | Graph API version for the Conversions API and Page posting |

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
- **One-click Facebook connect:** today customers paste a Pixel ID and token, with on-screen steps. A "Log in with Facebook" button that picks the Pixel and creates the token automatically needs a Meta app with Business Login approved by Meta (App Review). Worth doing once you have paying customers.
- **Privacy policy:** sending hashed customer data to Meta needs a privacy policy on the funnel pages that says so. Have one written and link it in the page footer.
- **Missed-call text-back:** needs a Twilio voice webhook. It's the natural next feature.
- **Hosting:** one small VPS works. Run behind HTTPS with a single uvicorn worker, because the follow-up worker runs in-process. Back up the `data/` folder.
