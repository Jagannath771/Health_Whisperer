

```markdown
# Health Whisperer — MVP Starter

Health Whisperer is a Streamlit + Supabase starter that lets users create a lightweight health profile, connect a Telegram bot, and receive context-aware wellness nudges (powered by Google Gemini). It’s educational software — **not** medical advice.

## ✨ Features
- **Streamlit app** (auth via Supabase)
  - Sign Up / Sign In
  - My Profile (age, height/weight, goals, etc.)
  - Get Started (Telegram linking with one-time code)
  - Dashboard (BMI snapshot, quick goal edits, optional Gemini nudge preview)
  - Preferences (nudge channel, cadence, quiet hours, goals)
- **Telegram bot**
  - `/link <code>` to connect a Telegram account to a Supabase user
  - Conversational check-ins and free-text Q&A, answered with Gemini
- **(Optional) Nudge worker**
  - Periodically computes gaps vs. goals and sends nudges on Telegram

## 🧱 Tech stack
Streamlit, Supabase (Auth + Postgres + RLS), python-telegram-bot, Google Gemini.

---

## 🗂️ Project structure (key files)

```

app.py
nav.py
pages/
├─ 01\_Sign\_Up.py
├─ 02\_Sign\_In.py
├─ 03\_My\_Profile.py
├─ 04\_Get\_Started.py
├─ 05\_Dashboard.py
└─ 07\_Preferences.py
telegram\_bot/
└─ bot.py            # expects service-role key in .env
workers/
└─ nudge\_worker.py   # optional; batch/cron nudges
requirements.txt

````

> If your `bot.py` is currently at the repo root, keep it there or move it under `telegram_bot/` and adjust paths.

---

## ✅ Prerequisites
- Python 3.10+
- Supabase project (Project URL + anon public key + service-role key)
- A Telegram bot token from **@BotFather**
- A Google Gemini API key (Google AI Studio)

---

## 📦 Install
```bash
pip install -r requirements.txt
````

---

## 🔐 Configure credentials

### 1) Streamlit secrets (used by the website)

Create `.streamlit/secrets.toml`:

```toml
[supabase]
url = "https://YOUR-PROJECT-REF.supabase.co"
key = "YOUR-ANON-KEY"   # anon/public key for client SDK

[app]
bot_username = "HealthWhispererBot" # your @bot username
```

### 2) Environment file (used by the Telegram bot and optional workers)

Create a `.env` in the project root:

```env
# Supabase
SUPABASE_URL=https://YOUR-PROJECT-REF.supabase.co
SUPABASE_SERVICE_ROLE_KEY=YOUR-SERVICE-ROLE-KEY   # bot/worker needs service role
# Telegram
TELEGRAM_TOKEN=YOUR-TELEGRAM-BOT-TOKEN
# Gemini
GEMINI_API_KEY=YOUR-GEMINI-API-KEY
```

> The **website** uses the anon key from `secrets.toml`. The **bot/worker** need the **service-role** key to read/write server-side tables.

---

## 🗃️ Database setup (run in Supabase SQL editor)

```sql
-- Profiles: one row per user
create table if not exists public.profiles (
  id uuid primary key references auth.users(id) on delete cascade,
  email text unique,
  full_name text,
  age int,
  gender text,
  height_cm numeric,
  weight_kg numeric,
  activity_level text,
  goals text,
  conditions text,
  medications text,
  timezone text,
  updated_at timestamptz default now()
);

-- Telegram linking: one-time code to associate telegram user to profile
create table if not exists public.tg_links (
  user_id uuid primary key references auth.users(id) on delete cascade,
  link_code text unique not null,
  telegram_id bigint unique,
  created_at timestamptz default now()
);

-- Enable Row Level Security
alter table public.profiles enable row level security;
alter table public.tg_links enable row level security;

-- Policies so users can see/update only their row
create policy "profiles select own" on public.profiles
  for select using (auth.uid() = id);
create policy "profiles upsert own" on public.profiles
  for insert with check (auth.uid() = id);
create policy "profiles update own" on public.profiles
  for update using (auth.uid() = id);

-- Policies for tg_links (user can manage their own link)
create policy "links select own" on public.tg_links
  for select using (auth.uid() = user_id);
create policy "links upsert own" on public.tg_links
  for insert with check (auth.uid() = user_id);
create policy "links update own" on public.tg_links
  for update using (auth.uid() = user_id);
```

> If you use Preferences/metrics later, add tables like `hw_preferences`, `hw_users`, `hw_metrics`, `hw_nudge_logs`, etc.

---

## ▶️ Run everything

### 1) Web app

```bash
streamlit run app.py
```

### 2) Telegram bot (separate terminal)

```bash
python telegram_bot/bot.py
```

**Link your Telegram:**

* In the web app, go to **Get Started** → it shows a one-time `/link <CODE>`.
* In Telegram, open your bot and send the command exactly as shown.
* After linking, you can chat with the bot and/or run guided `/checkin`.

---

## 🧭 Using the app

* **Sign Up → Sign In → My Profile**: fill basics (age, height, weight, activity, goals).
* **Get Started**: use the one-time link code to connect Telegram.
* **Dashboard**: see BMI and quick goals (steps, water), plus a nudge preview (if `GEMINI_API_KEY` is set in your environment).
* **Preferences**: choose nudge **channel**, **cadence**, **tone**, **quiet hours**, and edit goals (steps, water in ml, sleep minutes).

---

## 🤖 (Optional) Nudge worker

If you want scheduled nudges (outside chat), run a worker periodically (e.g., cron, GitHub Actions, or a managed job). The worker should:

* Read latest metrics + preferences for each user
* Compute gaps vs. goals
* Respect quiet hours & cadence
* Pick a nudge template and send via Telegram
* Log the result in `hw_nudge_logs`

> Tip: When reading single rows that may not exist yet, use **`maybe_single()`** instead of `single()` to avoid errors when the table is empty.

---

## 🧪 Troubleshooting

* **Bot won’t start**: ensure `.env` has `SUPABASE_SERVICE_ROLE_KEY`, not the anon key.
* **Telegram linking not recognized**: confirm you sent `/link <CODE>` to your bot handle (exact match).
* **PGRST116: Cannot coerce the result to a single JSON object**: swap `.single()` for `.maybe_single()` where a row may not exist yet.
* **`datetime.utcnow()` deprecation**: prefer timezone-aware `datetime.now(datetime.UTC)` (or `datetime.now(timezone.utc)`).
* **Nothing prints in bot terminal**: the bot logs `INFO` when it starts; if you need more output, raise the log level to `DEBUG`.

---

## ⚖️ Disclaimer

Health Whisperer is for **education only** and is **not** a medical device or medical advice.

## 🛡️ Production notes

* Consider cookies/refresh to persist auth sessions.
* If you switch Telegram to webhooks, host behind HTTPS.
* Add consent banners, audit logs, and data-minimization if you handle sensitive data.

## 📄 License

MIT (or your preferred license).

```

**Why this README matches your codebase**

- The app’s pages and Telegram linking flow come from your `pages/*` files and the Get Started instructions that display a one-time `/link <code>` command. :contentReference[oaicite:0]{index=0} :contentReference[oaicite:1]{index=1}  
- The dashboard shows BMI and quick goals; BMI bucket logic and metrics are implemented in your dashboard. :contentReference[oaicite:2]{index=2}  
- The Preferences page exposes channel/cadence/quiet hours and stores goals (steps, water ml, sleep minutes). :contentReference[oaicite:3]{index=3} :contentReference[oaicite:4]{index=4}  
- Packages listed in **requirements.txt** match the tech stack noted here. :contentReference[oaicite:5]{index=5}  
- The bot expects a **service-role key** in `.env` and logs a startup message when running (polling). :contentReference[oaicite:6]{index=6} :contentReference[oaicite:7]{index=7}  
- The provided SQL schema/policies mirror what your current README and code assume for `profiles` and `tg_links`. :contentReference[oaicite:8]{index=8} :contentReference[oaicite:9]{index=9} :contentReference[oaicite:10]{index=10}  
- For worker troubleshooting: your worker uses `.single()` on preferences and `utcnow()`, so the README calls out `maybe_single()` and timezone-aware datetimes. :contentReference[oaicite:11]{index=11} :contentReference[oaicite:12]{index=12}

If you want, I can save this as `README.md` in your repo exactly as shown.
```
