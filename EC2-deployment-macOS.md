# 🚀 SecureLife MCP — EC2 Deployment Playbook (macOS Edition)

This is your **master deployment playbook** for macOS users — from a blank Ubuntu 24.04 EC2 instance to a fully live production application reachable on the public internet.

We use **Ubuntu 24.04 LTS** on the server because **Python 3.12 is the native default version** on that OS. No `pyenv` compilation, no hacky wrappers — everything works smoothly out of the box using the standard package manager.

> **⚠️ Why Ubuntu 24.04 and not the latest Ubuntu?**
> Chainlit, FastAPI, Starlette, and LangGraph are tested against **Python 3.10–3.12**. Newer Ubuntu releases (25.04+) ship Python 3.13 or 3.14, where unpinned dependencies can pull in incompatible versions of `starlette`/`anyio` and the app will crash with `anyio.NoEventLoopError` or similar. **Stay on 24.04 LTS.**

> **🍎 macOS users:** All commands in this guide that begin with `ubuntu@...$` run on the **remote EC2 instance** (via SSH). All commands prefixed with `you@Mac ~ %` (or shown in a *"on your Mac"* callout) run locally in **macOS Terminal** (or iTerm2). The macOS shell is `zsh` by default — every command below is zsh/bash-compatible.

---

## 📋 Step 0: Prerequisites (Before You SSH In)

Before touching the server, make sure you have all of these ready on your local Mac:

### 0.1 — Launch the EC2 instance

* **AMI:** Ubuntu Server 24.04 LTS (HVM), 64-bit (x86)
* **Instance type:** `t3.medium`
* **Storage:** At least 20 GB gp3
* **Key pair:** Create or reuse one — download the `.pem` file and keep it safe (it usually lands in `~/Downloads/`)

### 0.2 — Configure the Security Group (inbound rules)

In the AWS console, edit your instance's Security Group to allow these inbound rules. **Without this step you will not be able to SSH in or reach the app from a browser.**

| Type | Protocol | Port | Source | Purpose |
|------|----------|------|--------|---------|
| SSH | TCP | 22 | My IP | Your terminal access |
| HTTP | TCP | 80 | 0.0.0.0/0 | Public app access |
| HTTPS | TCP | 443 | 0.0.0.0/0 | Optional, for future SSL |

### 0.3 — Find your EC2 connection details

From the EC2 console, copy these two values for your instance:

* **Public IPv4 DNS** — looks like `ec2-43-xxx-xxx-xxx.ap-south-1.compute.amazonaws.com`
* **Public IPv4 address** — looks like `43.xxx.xxx.xxx`

You'll use the **DNS name** in `ssh` commands and the **IP address** in your browser.

### 0.4 — Set permissions on your `.pem` key (macOS)

If you skip this, `ssh` will refuse to use the key with a `"UNPROTECTED PRIVATE KEY FILE!"` error and abort.

Open **Terminal** (`⌘ + Space` → type "Terminal" → Enter), then move the key to a safe location and lock it down:

```bash
# Move the key out of Downloads into a stable location
mkdir -p ~/.ssh
mv ~/Downloads/securelife-mcp-keypair.pem ~/.ssh/

# Lock it down — read-only for you, no access for anyone else
chmod 400 ~/.ssh/securelife-mcp-keypair.pem

# Verify the permissions look like -r--------  (400)
ls -l ~/.ssh/securelife-mcp-keypair.pem
```

> 💡 You can also keep the `.pem` in your project folder if you prefer — just remember to run `chmod 400` on it there, and adjust the paths in the SSH/SCP commands below.

### 0.5 — Prepare your local `.env` file

In your project folder (same folder where you'll be working), create a file called `.env` with your actual API keys. Use `.env.example` as a template.

**Option A — Use `nano` in Terminal:**

```bash
cd ~/path/to/SecureLife-MCP-Project
nano .env
```

Paste the following, then save with **`Ctrl+O`** → **`Enter`** → **`Ctrl+X`**:

```env
OPENAI_API_KEY=sk-proj-...your-real-key-here...
# Optional — only if you want LangSmith tracing
# LANGSMITH_API_KEY=ls-...your-real-key-here...
# LANGSMITH_TRACING=true
# LANGSMITH_PROJECT=securelife-mcp-chainlit-ec2
```

**Option B — Use VS Code (if installed):**

```bash
cd ~/path/to/SecureLife-MCP-Project
code .env
```

**Option C — Use TextEdit:**

`open -e .env` will open it in TextEdit. Make sure to choose **Format → Make Plain Text** before saving, otherwise it writes a `.rtf` file.

> ⚠️ Never commit this file to GitHub. It's already in `.gitignore`.

### 0.6 — Connect via SSH

From Terminal:

```bash
ssh -i ~/.ssh/securelife-mcp-keypair.pem ubuntu@ec2-XX-XX-XX-XX.region.compute.amazonaws.com
```

The first time, you'll see a prompt: `Are you sure you want to continue connecting (yes/no/[fingerprint])?` — type `yes`. You should now see a prompt like `ubuntu@ip-172-31-xx-xx:~$`. You're in. 🎉

> 💡 **Pro tip:** To avoid retyping the long SSH command every time, add this to your `~/.ssh/config` file:
>
> ```sshconfig
> Host securelife
>     HostName ec2-XX-XX-XX-XX.region.compute.amazonaws.com
>     User ubuntu
>     IdentityFile ~/.ssh/securelife-mcp-keypair.pem
> ```
>
> After that, you can just type `ssh securelife` to connect.

---

## 🛠️ Step 1: Update OS & Install Dependencies

On the EC2 instance, update the system and install Python 3.12 (native), SQLite, Git, and Nginx.

```bash
sudo apt update && sudo apt upgrade -y

# Install Python 3.12 virtual environment tools, SQLite, Nginx, and Git
sudo apt install -y python3-venv python3-pip python3-dev sqlite3 nginx git curl
```

**Verify you got Python 3.12** (critical — if it shows 3.13 or 3.14, stop and confirm you're really on Ubuntu 24.04):

```bash
python3 --version
# Expected: Python 3.12.x
```

If you see anything other than 3.12, the rest of this playbook will not work reliably. Re-launch the instance with the correct AMI.

---

## 📥 Step 2: Clone the Repository

Clone your project into the `ubuntu` home directory.

```bash
cd ~
git clone https://github.com/prashant9501/SecureLife-MCP-Project.git
cd SecureLife-MCP-Project

# Confirm the structure
ls
# You should see: README.md  SecureLife_claims.db  requirements.txt  securelife_client_app  securelife_mcp_server
```

> ℹ️ If the repo is private, you'll need a [GitHub Personal Access Token](https://github.com/settings/tokens) and use the URL form `https://<TOKEN>@github.com/prashant9501/SecureLife-MCP-Project.git`.

---

## 🐍 Step 3: Create Virtual Environment & Install Packages

Since Python 3.12 is the system default, we use `python3` to create the environment **in the project root** (not inside the client app subfolder — both the MCP server and the Chainlit client share this single venv).

```bash
# Stay in the project root: ~/SecureLife-MCP-Project
pwd
# Should print: /home/ubuntu/SecureLife-MCP-Project

# Create the virtual environment
python3 -m venv .venv

# Activate it
source .venv/bin/activate

# Upgrade pip
pip install --upgrade pip

# Install dependencies — requirements.txt lives in the PROJECT ROOT
pip install -r requirements.txt
```

> ⚠️ **Common mistake:** Older versions of this guide said `cd securelife_client_app && pip install -r requirements.txt`. The `requirements.txt` is actually at the **project root**, not inside `securelife_client_app/`. Run `pip install` from the root.

**Verify the install:**

```bash
pip list | grep -E "chainlit|langgraph|fastmcp|langchain-openai"
```

You should see all four packages listed.

---

## 🔐 Step 4: Upload the `.env` File (From Your Mac)

**⚠️ Stop here and open a new Terminal window on your LOCAL Mac.** Do **not** run this inside the EC2 SSH session — `scp` runs locally and pushes the file up to the server.

Navigate to where your `.env` file lives, then securely copy it to the EC2 instance. *(Replace `<YOUR_EC2_DNS>` with your instance's Public IPv4 DNS).*

```bash
cd ~/path/to/SecureLife-MCP-Project

scp -i ~/.ssh/securelife-mcp-keypair.pem .env \
    ubuntu@<YOUR_EC2_DNS>:~/SecureLife-MCP-Project/securelife_client_app/.env
```

> 💡 The backslash `\` lets you split a long command across multiple lines in macOS Terminal. You can also write it on one line if you prefer.

Once the transfer reaches `100%`, switch back to your **EC2 SSH terminal** and verify the file arrived and contains your real key (not the placeholder):

```bash
cat ~/SecureLife-MCP-Project/.env
```

You should see your actual `OPENAI_API_KEY=sk-proj-...` value.

> 💡 **Why does the `.env` go into `securelife_client_app/`?** Because [agent.py](securelife_client_app/agent.py) calls `load_dotenv()` from that directory. The MCP server doesn't need API keys — it only talks to SQLite.

---

## 🧪 Step 5: Smoke Test Each Piece Manually (Recommended)

Before wrapping the app in systemd services, prove that each piece runs cleanly on its own. This saves hours of debugging later.

### 5.1 — Test the MCP backend server

In your current SSH session (with the venv still activated):

```bash
cd ~/SecureLife-MCP-Project/securelife_mcp_server
python server.py
```

**Expected output:**

```
🚀 Starting SecureLife MCP Server on port 8765...
```

Press **`Ctrl+C`** to stop. If you see `FileNotFoundError: SecureLife_claims.db not found`, your DB file is missing — confirm with `ls ~/SecureLife-MCP-Project/SecureLife_claims.db`.

### 5.2 — Test the Chainlit frontend

Open a **second Terminal window on your Mac** and SSH into the same instance again (so the MCP server can keep running in the first one).

```bash
# In a new Mac Terminal window:
ssh -i ~/.ssh/securelife-mcp-keypair.pem ubuntu@<YOUR_EC2_DNS>
```

Then on the EC2 instance:

```bash
cd ~/SecureLife-MCP-Project
source .venv/bin/activate

# Start the MCP server in the background
cd securelife_mcp_server
nohup python server.py > /tmp/mcp.log 2>&1 &

# Wait a moment, then start Chainlit
cd ../securelife_client_app
chainlit run app.py --host 127.0.0.1 --port 8000
```

**Expected output:** `Your app is available at http://127.0.0.1:8000` (no traceback).

Press **`Ctrl+C`** to stop Chainlit. Then kill the background MCP server:

```bash
pkill -f "python server.py"
```

If both pieces ran cleanly, you're ready to make them permanent with systemd.

---

## ⚙️ Step 6: Create Systemd Services

We will create **two background services** so your app starts automatically on boot and restarts if it crashes.

### 6.1 — The MCP Backend Service

```bash
sudo nano /etc/systemd/system/securelife-backend.service
```

Paste this configuration:

```ini
[Unit]
Description=SecureLife MCP Backend Server
After=network.target

[Service]
User=ubuntu
WorkingDirectory=/home/ubuntu/SecureLife-MCP-Project/securelife_mcp_server
ExecStart=/home/ubuntu/SecureLife-MCP-Project/.venv/bin/python server.py
Restart=always
RestartSec=5
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
```

*(Save and exit: `Ctrl+O`, `Enter`, `Ctrl+X`)*

### 6.2 — The Chainlit Frontend Service

```bash
sudo nano /etc/systemd/system/securelife-frontend.service
```

Paste this configuration:

```ini
[Unit]
Description=SecureLife Chainlit Frontend App
After=network.target securelife-backend.service
Requires=securelife-backend.service

[Service]
User=ubuntu
WorkingDirectory=/home/ubuntu/SecureLife-MCP-Project/securelife_client_app
ExecStart=/home/ubuntu/SecureLife-MCP-Project/.venv/bin/chainlit run app.py --host 127.0.0.1 --port 8000
Restart=always
RestartSec=5
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
```

*(Save and exit: `Ctrl+O`, `Enter`, `Ctrl+X`)*

> 💡 **Why `Requires=`?** It tells systemd the frontend depends on the backend — if the backend stops, the frontend stops with it, preventing zombie states where the UI tries to call an unavailable MCP server.

---

## ▶️ Step 7: Register, Start, and Check Services

Reload the system daemon to recognize the new files, enable them to run on boot, and start them.

```bash
sudo systemctl daemon-reload

# Enable for auto-start on reboot
sudo systemctl enable securelife-backend.service
sudo systemctl enable securelife-frontend.service

# Start them now
sudo systemctl start securelife-backend.service
sudo systemctl start securelife-frontend.service
```

**Verify both are running cleanly:**

```bash
sudo systemctl status securelife-backend.service
sudo systemctl status securelife-frontend.service
```

You should see **green `active (running)`** for both. Press **`q`** to exit the status view.

**If either shows `failed` or `activating (auto-restart)`**, jump to the [Troubleshooting](#-troubleshooting) section below before continuing.

---

## 🌐 Step 8: Create and Configure Nginx

Finally, we route external traffic on port `80` to your Chainlit app on port `8000`, ensuring WebSockets (which Chainlit uses for streaming responses) are handled correctly.

```bash
sudo nano /etc/nginx/sites-available/securelife
```

Paste this configuration:

```nginx
server {
    listen 80;
    server_name _;

    # Increase max body size for potential file uploads
    client_max_body_size 10M;

    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;

        # Mandatory for streaming Chainlit WebSockets
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_read_timeout 86400;
    }
}
```

*(Save and exit: `Ctrl+O`, `Enter`, `Ctrl+X`)*

Enable the configuration and restart Nginx:

```bash
# Create symlink to enable the site
sudo ln -s /etc/nginx/sites-available/securelife /etc/nginx/sites-enabled/

# Remove default Nginx welcome page
sudo rm -f /etc/nginx/sites-enabled/default

# Test Nginx syntax — must say "syntax is ok" and "test is successful"
sudo nginx -t

# Restart Nginx to apply
sudo systemctl restart nginx

# Verify Nginx is active
sudo systemctl status nginx
```

---

## 🎉 Step 9: You Are Live!

Open Safari, Chrome, or your favorite browser on your Mac and navigate to your **EC2 Public IPv4 address** (the plain IP, not the DNS):

```
http://43.205.214.140
```

You should see the welcome message:
> 👋 **Welcome to SecureLife Claims Processing Hub**

Try one of the test prompts from the README to confirm the full pipeline works:

```text
CLM-2025-0001 | Routine health checkup reimbursement request.
```

You should see five collapsible steps stream in (`triage` → `doc_verifier` → `fraud_analyst` → `decision_maker` → `compliance_auditor`) followed by a Final Evaluation Summary.

---

## 🔧 Troubleshooting

### Service won't start — check the logs first

```bash
# Live tail the logs for either service
sudo journalctl -u securelife-backend.service -f
sudo journalctl -u securelife-frontend.service -f

# Or the last 50 lines without following
sudo journalctl -u securelife-frontend.service -n 50 --no-pager
```

Press **`Ctrl+C`** to exit a `-f` (follow) tail.

### Common failure modes

| Symptom in logs | Cause | Fix |
|---|---|---|
| `FileNotFoundError: SecureLife_claims.db` | DB missing | `ls ~/SecureLife-MCP-Project/SecureLife_claims.db` — re-clone if absent |
| `OPENAI_API_KEY` missing or `401 Unauthorized` | `.env` not uploaded or wrong key | Re-run Step 4 `scp` |
| `anyio.NoEventLoopError` | Python 3.13+ pulled incompatible deps | Confirm `python3 --version` is 3.12.x; rebuild venv |
| `Address already in use: 8000` | Old Chainlit process still running | `sudo lsof -i :8000` then `kill <PID>` |
| `502 Bad Gateway` from browser | Chainlit service not running | `sudo systemctl status securelife-frontend.service` |
| Can't reach the IP from browser | Security Group missing HTTP rule | Re-check Step 0.2 |

### macOS-specific local issues

| Symptom on your Mac | Cause | Fix |
|---|---|---|
| `WARNING: UNPROTECTED PRIVATE KEY FILE!` when running `ssh` | `.pem` is world-readable | `chmod 400 ~/.ssh/securelife-mcp-keypair.pem` |
| `Permission denied (publickey)` | Wrong key, wrong user, or key not added to instance | Confirm the right `.pem` path; user is `ubuntu` for Ubuntu AMIs |
| `ssh: Could not resolve hostname` | Typo in EC2 DNS, or instance stopped | Re-copy the DNS from the EC2 console |
| `scp` writes `.env.rtf` instead of `.env` | TextEdit saved as rich text | Open in TextEdit → Format → Make Plain Text, or use `nano` |

### Restart everything cleanly

```bash
sudo systemctl restart securelife-backend.service
sudo systemctl restart securelife-frontend.service
sudo systemctl restart nginx
```

### Check what's listening on each port

```bash
sudo ss -tlnp | grep -E "80|8000|8765"
```

You should see Nginx on `:80`, Chainlit on `127.0.0.1:8000`, and FastMCP on `:8765`.

---

## 🔄 Updating the App (Redeploy Workflow)

After your initial deploy, to pull in new code changes from GitHub:

```bash
cd ~/SecureLife-MCP-Project

# Pull the latest code
git pull

# Activate venv and update dependencies (in case requirements.txt changed)
source .venv/bin/activate
pip install -r requirements.txt

# Restart the services to load the new code
sudo systemctl restart securelife-backend.service
sudo systemctl restart securelife-frontend.service

# Confirm they're still healthy
sudo systemctl status securelife-frontend.service
```

> 💡 If you only changed the frontend, you don't need to restart the backend — and vice versa.

### Pushing local code changes from your Mac via `scp`

If you've made changes on your Mac that aren't yet committed to GitHub, you can push individual files directly:

```bash
# Example: push an updated agent.py
scp -i ~/.ssh/securelife-mcp-keypair.pem \
    ./securelife_client_app/agent.py \
    ubuntu@<YOUR_EC2_DNS>:~/SecureLife-MCP-Project/securelife_client_app/agent.py

# Then on the EC2 instance, restart the affected service:
ssh -i ~/.ssh/securelife-mcp-keypair.pem ubuntu@<YOUR_EC2_DNS> \
    "sudo systemctl restart securelife-frontend.service"
```

> ⚠️ For anything beyond a quick fix, prefer the `git push` → `git pull` workflow so your changes stay versioned.

---

## 🔒 (Optional) Step 10: Enable HTTPS with Let's Encrypt

For production use, you'll want HTTPS. This requires a **domain name** pointed at your EC2 IP (Route53, GoDaddy, Namecheap, etc.).

```bash
sudo apt install -y certbot python3-certbot-nginx
sudo certbot --nginx -d your-domain.example.com
```

Certbot will automatically:
1. Obtain a free SSL certificate
2. Update your Nginx config to listen on `:443` with TLS
3. Set up auto-renewal

After this, your app is reachable at `https://your-domain.example.com`.

---

## 🧹 (Optional) Tear-Down Checklist

If you want to fully remove the app from the instance:

```bash
sudo systemctl stop securelife-frontend.service securelife-backend.service
sudo systemctl disable securelife-frontend.service securelife-backend.service
sudo rm /etc/systemd/system/securelife-frontend.service /etc/systemd/system/securelife-backend.service
sudo systemctl daemon-reload

sudo rm /etc/nginx/sites-enabled/securelife /etc/nginx/sites-available/securelife
sudo systemctl restart nginx

rm -rf ~/SecureLife-MCP-Project
```

To stop billing entirely, **terminate the EC2 instance** from the AWS console.
