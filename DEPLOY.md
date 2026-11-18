# Putting ClipCore OS on a server

Takes about 15 minutes. You don't need to know how to code; you'll copy and paste one command.

## 1. Rent a server (DigitalOcean)

1. Sign up at **digitalocean.com** and add a payment method.
2. Click **Create** (top right) → **Droplets**.
3. **Region:** the one closest to you (for example, New York).
4. **Image:** **Ubuntu 24.04 (LTS)**.
5. **Size:** **Basic → Regular**, then pick **4 CPUs / 8 GB memory / 160 GB disk**
   (about $48 a month). That's enough to start. Each video uses the whole server for a
   few minutes, so if users end up waiting in line, resize the droplet bigger (Resize tab)
   and raise **Videos processing at once** in Admin.
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

3. Wait 5–10 minutes (it also downloads the caption model). Near the end it asks you to
   **create your admin login**: type your email, press Enter, then choose a password
   (nothing appears on screen while you type it, which is normal), press Enter, and type it
   again. This is the only account that can open the Admin page. Nobody can become admin
   from the website.
4. When it's done you'll see a box saying **ClipCore OS is running** with your address.

## 3. First login

1. In your browser, go to `http://YOUR-IP-ADDRESS` (the IP from step 1).
2. Click **Log in** and sign in with the admin email and password you set during install.
3. Click **Admin** (top right):
   - Paste your Twitch **Client ID** and **Client Secret**, click **Save**, then
     **Test connection**. This is optional, but for Twitch links it adds ranking by the clips
     viewers already made, which noticeably improves the picks.
   - **Access code:** leave it blank to let anyone sign up, or set a code to make it
     invite-only. Sell access through your checkout and send buyers the code.
4. Go to **My clips**, paste a VOD link and click **Get clips**.

That's it. Share your site's address in your ads. New visitors land on the sign-up page.

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

Run the same install command again in the Droplet Console. Accounts, clips and settings are kept.

## Troubleshooting

| Problem | Fix |
|---|---|
| Page won't load | Wait 2 minutes after install, then check that you typed `http://` and not `https://` (unless you set up a domain). |
| "Twitch rejected these keys" | Re-copy the Client ID. In the Twitch console, click **New Secret**, paste the new one, and click Save. |
| A user forgot their password | Admin → Users → **Reset password**, then send them the new one privately. |
| You forgot the admin password | In the Droplet Console: `sudo -u clipcore env HOME=/var/lib/clipcore CLIPCORE_DATA_DIR=/var/lib/clipcore /opt/clipcore/.venv/bin/clipcore create-admin you@email.com`, then choose a new password when asked. |
| YouTube links fail with "YouTube blocked the download" | YouTube often blocks downloads from data-center servers. Twitch and Kick links aren't affected. |
| Disk filling up | Admin → lower **Keep finished clips for** (days). Old projects are deleted automatically every hour. |
| See what the server is doing | In the Droplet Console: `journalctl -u clipcore -f` (press Ctrl+C to stop). |
