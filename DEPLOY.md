# Putting ClipCore OS on a server

Takes about 15 minutes. You don't need to know how to code; you'll copy and paste one command.

## 1. Rent a server (DigitalOcean)

1. Sign up at **digitalocean.com** and add a payment method.
2. Click **Create** (top right) → **Droplets**.
3. **Region:** the one closest to you (for example, New York).
4. **Image:** **Ubuntu 24.04 (LTS)**.
5. **Size:** **Basic → Regular**, then pick **4 CPUs / 8 GB memory / 160 GB disk**
   (about $48 a month). That handles 3–5 streamers comfortably. You can resize later.
6. **Authentication:** choose **Password** and create a strong root password. Save it in your
   password manager. This is the server's master password, separate from your dashboard password.
7. Click **Create Droplet**. Wait about a minute until you see its **IP address**
   (looks like `164.92.123.45`). Copy it.

## 2. Install ClipCore OS

1. Click your droplet's name, then **Access → Launch Droplet Console**. A black terminal
   window opens in your browser, already logged in.
2. Copy this whole line, paste it into that window, and press **Enter**:

   ```
   curl -fsSL https://raw.githubusercontent.com/resurgencecreditandfunding-netizen/clippertool/main/deploy/install.sh | sudo bash
   ```

3. Wait 3–5 minutes. When it's done you'll see a box saying **ClipCore OS is running**
   with your address.

## 3. First login

1. In your browser, go to `http://YOUR-IP-ADDRESS` (the IP from step 1).
2. **Create your dashboard password** (10+ characters). This is what you'll log in with.
3. You land on **Settings**:
   - Paste your Twitch **Client ID** and **Client Secret**, then click **Save**.
   - For **Game category**, type `Grand Theft Auto V` for now (the GTA VI category may not
     exist until launch). Click **Save**, then **Test connection**.
   - A green ✓ means you're connected. Change the category back to `Grand Theft Auto VI`
     on launch day.
   - Set your **timezone** and posting hours, then click **Save**.

## 4. Set up your first campaign

1. **Campaigns → New campaign:** the deal you joined (payout per 1k views, budget, rules).
2. **Sources → Add source:** the streamers that campaign covers.
3. **Accounts → Add account:** your YouTube / TikTok accounts.

That's it. The server watches your streamers around the clock. When one goes live, it records;
when they finish, new clips show up in **Review**.

---

## Recommended: add your own web address (HTTPS)

Without a domain, the dashboard runs on plain `http://`, which isn't encrypted. To fix that:

1. Wherever your domain is managed (GoDaddy, Namecheap, Cloudflare…), add a DNS record:
   **Type** `A`, **Name** `clips`, **Value** your server's IP.
2. Wait about 10 minutes, then run this in the Droplet Console (with your domain):

   ```
   curl -fsSL https://raw.githubusercontent.com/resurgencecreditandfunding-netizen/clippertool/main/deploy/install.sh | sudo DOMAIN=clips.yourdomain.com bash
   ```

3. Your dashboard is now at `https://clips.yourdomain.com`.

## Updating to the latest version

Run the same install command again in the Droplet Console. Your campaigns, clips and settings are kept.

## Troubleshooting

| Problem | Fix |
|---|---|
| Page won't load | Wait 2 minutes after install, then check that you typed `http://` and not `https://` (unless you set up a domain). |
| "Twitch rejected these keys" | Re-copy the Client ID. In the Twitch console, click **New Secret**, paste the new one, and click Save. |
| Forgot dashboard password | In the Droplet Console: `sudo -u clipcore CLIPCORE_DATA_DIR=/var/lib/clipcore /opt/clipcore/.venv/bin/clipcore reset-password`, then visit the site to set a new one. |
| "Disk almost full" warning | Settings → Storage → **Clean up now**, lower the days kept, or switch to 720p. |
| See what the server is doing | In the Droplet Console: `journalctl -u clipcore -f` (press Ctrl+C to stop). |
