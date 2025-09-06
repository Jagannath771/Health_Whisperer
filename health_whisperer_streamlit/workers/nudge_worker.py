# workers/nudge_worker.py
import os
import json
from datetime import datetime, timedelta, timezone

from dotenv import load_dotenv
load_dotenv()

from utils.supa import get_supabase
from utils.tele import send_tg

# Try to import your existing nudge engine; fall back to local minimal logic
try:
    from nudge_engine import in_quiet_hours, compute_gaps, rules_engine, select_nudge
    HAVE_ENGINE = True
except Exception:
    HAVE_ENGINE = False


def utcnow():
    return datetime.now(timezone.utc)


def fetch_one(sb, table, **filters):
    """
    Return first row or None without using .single(), so 0 rows won't crash.
    """
    q = sb.table(table).select("*")
    for k, v in filters.items():
        q = q.eq(k, v)
    res = q.limit(1).execute().data
    return res[0] if res else None


def parse_iso_utc(ts: str | None):
    if not ts:
        return None
    try:
        # Accept "Z" or offset forms
        ts = ts.replace("Z", "+00:00")
        dt = datetime.fromisoformat(ts)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except Exception:
        return None


# ---------- Local fallbacks if nudge_engine is not ready for the new goals ----------
def _in_quiet_hours_fallback(now_utc: datetime, start_hhmm: str, end_hhmm: str) -> bool:
    """Quiet hours across midnight friendly."""
    s_h, s_m = [int(x) for x in (start_hhmm or "22:00").split(":")]
    e_h, e_m = [int(x) for x in (end_hhmm or "07:00").split(":")]
    start = now_utc.replace(hour=s_h, minute=s_m, second=0, microsecond=0)
    end = now_utc.replace(hour=e_h, minute=e_m, second=0, microsecond=0)
    if start <= end:
        return start <= now_utc <= end
    # crosses midnight
    return now_utc >= start or now_utc <= end


def _compute_gaps_fallback(latest_ctx: dict, goals: dict) -> dict:
    """
    Positive gap means "we're behind target" for the day.
    - steps: target minimum
    - water_ml: target minimum
    - sleep_minutes: target minimum (we'll look at last night)
    - calories: we target >= ~80% by evening but avoid > goal too early
    - protein/sugar/fiber: optional; compute only if latest metric available
    - meditation/mood_target: qualitative; used in rules, not gap
    """
    g = goals or {}
    gaps = {}

    steps_now = int(latest_ctx.get("steps") or 0)
    steps_goal = int(g.get("steps") or 8000)
    gaps["steps_gap"] = max(0, steps_goal - steps_now)

    water_now = int(latest_ctx.get("water_ml") or 0)
    water_goal = int(g.get("water_ml") or 2500)
    gaps["water_gap_ml"] = max(0, water_goal - water_now)

    sleep_now = int(latest_ctx.get("sleep_minutes") or 0)
    sleep_goal = int(g.get("sleep_minutes") or 420)
    gaps["sleep_gap_min"] = max(0, sleep_goal - sleep_now)

    # Calories: treat gap as "how far under daily goal we are right now"
    c_now = int(latest_ctx.get("calories") or 0)
    c_goal = int(g.get("calories") or 2000)
    gaps["calories_gap"] = max(0, c_goal - c_now)

    # Optional macros/limits if present in metrics (likely not yet)
    p_now = latest_ctx.get("protein_g")
    if p_now is not None:
        gaps["protein_gap_g"] = max(0, int(g.get("protein") or 75) - int(p_now))
    s_now = latest_ctx.get("sugar_g")
    if s_now is not None:
        # sugar is a limit; gap is 0 if we've exceeded
        gaps["sugar_remaining_g"] = max(0, int(g.get("sugar") or 50) - int(s_now))
    f_now = latest_ctx.get("fiber_g")
    if f_now is not None:
        gaps["fiber_gap_g"] = max(0, int(g.get("fiber") or 0) - int(f_now))

    return gaps


def _rules_engine_fallback(now_utc: datetime, base: dict, latest_ctx: dict, goals: dict, gaps: dict, prefs: dict) -> list[str]:
    """
    Very simple heuristic:
    - Morning: hydration/steps
    - Afternoon: steps/calories status
    - Evening: steps catch-up, hydration catch-up, bedtime prep if sleep gap remains
    - Optional smart reminder toggles from prefs: remind_hydration, remind_steps, remind_sleep
    """
    hour = now_utc.astimezone(timezone.utc).hour  # treat schedule in UTC; your prod may want local tz
    cand: list[str] = []

    remind_hydration = bool(prefs.get("remind_hydration", True))
    remind_steps = bool(prefs.get("remind_steps", True))
    remind_sleep = bool(prefs.get("remind_sleep", False))

    # Hydration
    if remind_hydration and gaps.get("water_gap_ml", 0) > 0:
        # stronger during morning/afternoon hours
        if 9 <= hour <= 17:
            cand.append("hydrate")

    # Steps
    if remind_steps and gaps.get("steps_gap", 0) > 0:
        if 10 <= hour <= 19:
            cand.append("walk")
        elif 19 < hour <= 22:
            cand.append("evening_walk")

    # Sleep prep in late evening if still under last-night goal
    if remind_sleep and 20 <= hour <= 23 and gaps.get("sleep_gap_min", 0) > 0:
        cand.append("sleep_prep")

    # Calories guidance (if too low by afternoon, suggest balanced snack)
    if gaps.get("calories_gap", 0) > 0 and 15 <= hour <= 18:
        cand.append("balanced_snack")

    # Mindfulness/mental health gentle touch if user set goals
    if (goals.get("journaling") and goals.get("journaling") != "None") or int(goals.get("meditation", 0)) > 0:
        if 12 <= hour <= 16:
            cand.append("mindfulness")

    # Mood check-in if no mood logged today
    if not latest_ctx.get("mood_logged_today"):
        cand.append("checkin_mood")

    # Fallback default
    if not cand:
        cand = ["encouragement"]

    # order unique while preserving order
    seen = set()
    out = []
    for x in cand:
        if x not in seen:
            seen.add(x)
            out.append(x)
    return out


def _select_nudge_fallback(candidates: list[str], bandit_stats: dict) -> str:
    """
    Super-simple epsilon-greedy-ish: prefer types with fewer sends so far.
    """
    counts = (bandit_stats or {}).get("counts", {}) or {}
    best = None
    best_count = None
    for c in candidates:
        ct = counts.get(c, 0)
        if best is None or ct < best_count:
            best, best_count = c, ct
    return best or (candidates[0] if candidates else "encouragement")


def render_template(tpl: str, ctx: dict) -> str:
    safe = {k: "" if v is None else str(v) for k, v in ctx.items()}
    for k, v in safe.items():
        tpl = tpl.replace("{" + k + "}", v)
    return tpl


def hours_since_last_meal(sb, uid, now):
    meals = (
        sb.table("hw_metrics")
        .select("ts,calories,meal_quality")
        .eq("uid", uid)
        .not_.is_("calories", "null")
        .order("ts", desc=True)
        .limit(1)
        .execute()
        .data
    )
    if not meals:
        return 12
    ts = meals[0]["ts"]
    dt = parse_iso_utc(ts)
    if not dt:
        return 12
    return int(max(0, (now - dt).total_seconds() // 3600))


def should_now(cadence: str, last_sent_at, gaps: dict, now: datetime, prefs: dict) -> bool:
    def since_last_ok(min_seconds):
        return (not last_sent_at) or ((now - last_sent_at).total_seconds() >= min_seconds)

    if cadence == "hourly":
        return since_last_ok(3600)
    if cadence == "3_per_day":
        return since_last_ok(4 * 3600)

    # smart: nudge if any meaningful gap exists OR if it's been a while
    # consider hydration and steps as primary
    if (gaps.get("water_gap_ml", 0) > 250) or (gaps.get("steps_gap", 0) > 1000) or (gaps.get("calories_gap", 0) > 300):
        return True

    # bedtime reminder can also trigger smart near late evening
    if prefs.get("remind_sleep") and 20 <= now.hour <= 23 and gaps.get("sleep_gap_min", 0) > 30:
        return True

    return since_last_ok(6 * 3600)


def main():
    sb = get_supabase(client_role="service")
    now = utcnow()

    # Iterate all users
    users = sb.table("hw_users").select("*").execute().data or []
    for u in users:
        uid = u["uid"]

        # --- Preferences (safe fetch) ---
        pref = fetch_one(sb, "hw_preferences", uid=uid) or {}
        goals = (pref.get("goals") or {})
        # merge with sensible defaults
        goals = {
            "steps": int(goals.get("steps") or 8000),
            "water_ml": int(goals.get("water_ml") or 2500),
            "sleep_minutes": int(goals.get("sleep_minutes") or 420),
            "calories": int(goals.get("calories") or 2000),
            "protein": int(goals.get("protein") or 75),
            "sugar": int(goals.get("sugar") or 50),
            "fiber": int(goals.get("fiber") or 0),
            "journaling": goals.get("journaling") or "None",
            "meditation": int(goals.get("meditation") or 10),
            "mood_target": float(goals.get("mood_target") or 3.5),
        }

        quiet_start = (pref.get("quiet_start") or "22:00")[:5]
        quiet_end = (pref.get("quiet_end") or "07:00")[:5]

        # Quiet hours
        if HAVE_ENGINE:
            qh = in_quiet_hours(
                now,
                datetime.strptime(quiet_start, "%H:%M").time(),
                datetime.strptime(quiet_end, "%H:%M").time(),
            )
        else:
            qh = _in_quiet_hours_fallback(now, quiet_start, quiet_end)
        if qh:
            continue

        # --- Latest metrics (last 24h) ---
        metrics = (
            sb.table("hw_metrics")
            .select("*")
            .eq("uid", uid)
            .order("ts", desc=True)
            .limit(1)
            .execute()
            .data
        )
        latest = metrics[0] if metrics else {}
        today = (
            sb.table("hw_metrics")
            .select("id,mood,ts,steps,calories,water_ml,sleep_minutes")
            .eq("uid", uid)
            .gte("ts", (now - timedelta(hours=24)).isoformat())
            .execute()
            .data
            or []
        )

        # Build latest context for templates/rules
        latest_ctx = {
            "steps": latest.get("steps"),
            "water_ml": latest.get("water_ml"),
            "sleep_minutes": latest.get("sleep_minutes"),
            "calories": latest.get("calories"),
            "hours_since_last_meal": hours_since_last_meal(sb, uid, now),
            "mood_logged_today": any(m.get("mood") is not None for m in today),
        }

        # --- Gaps ---
        if HAVE_ENGINE:
            # If your engine already supports the extended goals, it can compute richer gaps.
            gaps = compute_gaps(latest_ctx, goals)
        else:
            gaps = _compute_gaps_fallback(latest_ctx, goals)

        # --- Cadence gate ---
        last = (
            sb.table("hw_nudge_logs")
            .select("decided_at")
            .eq("uid", uid)
            .order("decided_at", desc=True)
            .limit(1)
            .execute()
            .data
        )
        last_sent_at = parse_iso_utc(last[0]["decided_at"]) if last else None

        if not should_now(pref.get("nudge_cadence", "smart"), last_sent_at, gaps, now, pref):
            continue

        # --- Baselines (optional, safe fetch) ---
        base = fetch_one(sb, "hw_user_baselines", uid=uid) or {}

        # --- Candidate selection ---
        if HAVE_ENGINE:
            candidates = rules_engine(now, base, latest_ctx, gaps)
        else:
            candidates = _rules_engine_fallback(now, base, latest_ctx, goals, gaps, pref)

        # --- Bandit stats ---
        logs = (
            sb.table("hw_nudge_logs")
            .select("nudge_type,reward")
            .eq("uid", uid)
            .limit(200)
            .execute()
            .data
            or []
        )
        counts, rewards = {}, {}
        for r in logs:
            nt = r["nudge_type"]
            counts[nt] = counts.get(nt, 0) + 1
            rewards[nt] = rewards.get(nt, 0.0) + float(r.get("reward") or 0)

        bandit = {"counts": counts, "rewards": rewards}
        if HAVE_ENGINE:
            chosen = select_nudge(candidates, bandit)
        else:
            chosen = _select_nudge_fallback(candidates, bandit)

        # --- Template lookup (required for nice copy) ---
        tmpl = fetch_one(sb, "hw_nudge_types", nudge_type=chosen)
        if tmpl and tmpl.get("template"):
            msg = render_template(tmpl["template"], {**latest_ctx, **gaps, **goals})
        else:
            # Plain fallback message if no template in DB
            if chosen == "hydrate":
                need = max(0, gaps.get("water_gap_ml", 0))
                msg = f"💧 Quick sip? You’re ~{int(need/250)} glasses away from today’s goal."
            elif chosen in ("walk", "evening_walk"):
                msg = "🚶‍♂️ 5–10 min brisk walk break? Tiny steps add up."
            elif chosen == "sleep_prep":
                msg = "🌙 Wind-down time: dim lights, put phone aside, 10 min to settle."
            elif chosen == "balanced_snack":
                msg = "🥗 Energy dip? Try a protein + fiber snack (e.g., yogurt + nuts)."
            elif chosen == "mindfulness":
                msg = "🧘 2-minute box-breathing: inhale 4, hold 4, exhale 4, hold 4."
            elif chosen == "checkin_mood":
                msg = "🙂 How’s your mood right now (1–5)? A tiny check-in helps spot patterns."
            else:
                msg = "✨ You’re doing great — one tiny healthy choice right now?"

        # --- Deliver via configured channel (telegram default) ---
        chat_id = u.get("tg_chat_id")
        delivered = False
        if (pref.get("nudge_channel") or "telegram") == "telegram" and chat_id:
            sent = send_tg(chat_id, msg)
            delivered = sent is not None and "error" not in (sent or {})
        # (inapp channel could be implemented by inserting into a notifications table, etc.)

        # --- Log decision ---
        sb.table("hw_nudge_logs").insert({
            "uid": uid,
            "nudge_type": chosen,
            "delivered": delivered,
            "channel": pref.get("nudge_channel", "telegram"),
            "decided_at": utcnow().isoformat(),
            "context": {**latest_ctx, "gaps": gaps, "goals": goals},
        }).execute()


if __name__ == "__main__":
    import time
    while True:
        main()
        time.sleep(300)  # run every 5 minutes
