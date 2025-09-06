# nudge_engine.py
from datetime import datetime, timedelta, time
import math, random
from typing import Dict, Any, Optional

def in_quiet_hours(now: datetime, quiet_start: time, quiet_end: time) -> bool:
    qs, qe = quiet_start, quiet_end
    # handle ranges that pass midnight
    if qs <= qe:
        return qs <= now.time() < qe
    else:
        return now.time() >= qs or now.time() < qe

def ewma(prev: float, x: float, alpha: float=0.2) -> float:
    return alpha * x + (1 - alpha) * prev

def compute_gaps(metrics: Dict[str, Any], goals: Dict[str, Any]) -> Dict[str, Any]:
    gaps = {}
    if goals.get("steps") is not None and metrics.get("steps") is not None:
        gaps["steps_gap"] = max(0, goals["steps"] - metrics["steps"])
    if goals.get("water_ml") is not None and metrics.get("water_ml") is not None:
        gaps["gap_ml"] = max(0, goals["water_ml"] - metrics["water_ml"])
    if goals.get("sleep_minutes") is not None and metrics.get("sleep_minutes") is not None:
        gaps["sleep_gap"] = max(0, goals["sleep_minutes"] - metrics["sleep_minutes"])
    return gaps

def should_nudge(now: datetime, last_sent_at: Optional[datetime], cadence: str) -> bool:
    if cadence == "hourly":
        return (not last_sent_at) or (now - last_sent_at >= timedelta(hours=1))
    if cadence == "3_per_day":
        # min 4 hours apart
        return (not last_sent_at) or (now - last_sent_at >= timedelta(hours=4))
    # smart: only when drift or missed pattern
    return True

def bandit_ucb1(counts: Dict[str, int], rewards: Dict[str, float], c: float=1.2) -> str:
    # counts, rewards are per nudge_type
    total = sum(max(1, n) for n in counts.values()) or 1
    best, best_ucb = None, -1e9
    for arm in counts.keys():
        n = max(1, counts[arm])
        mean = rewards.get(arm, 0.0) / n
        ucb = mean + c * math.sqrt(math.log(total) / n)
        if ucb > best_ucb:
            best_ucb, best = ucb, arm
    return best

def select_nudge(candidates, bandit_stats) -> str:
    # candidates: list of types allowed by rules at this moment
    # bandit_stats: {counts: {type:int}, rewards:{type:float}}
    # Filter stats to candidates
    counts = {k: bandit_stats["counts"].get(k,0) for k in candidates}
    rewards = {k: bandit_stats["rewards"].get(k,0.0) for k in candidates}
    # If first time, explore randomly
    if all(v==0 for v in counts.values()):
        return random.choice(candidates)
    return bandit_ucb1(counts, rewards)

def rules_engine(now: datetime, baselines: Dict[str,float], latest: Dict[str, Any], gaps: Dict[str,Any]) -> list:
    """Return eligible nudge types given current context."""
    eligible = []
    # Hydration: gap > 200 ml and it’s not close to bedtime
    if gaps.get("gap_ml", 0) >= 200 and now.hour < 21:
        eligible.append("hydrate")
    # Movement: steps pace vs EWMA trend (falling behind)
    steps = latest.get("steps")
    if steps is not None and baselines.get("steps_ewma", 5000) * 0.5 > steps and now.hour in range(9,20):
        eligible.append("move")
    # Sleep: evening wind down cue
    if now.hour in (21, 22):
        eligible.append("sleep")
    # Meal logging: if >5h since last meal and calories low
    if latest.get("hours_since_last_meal", 0) >= 5:
        eligible.append("meal_log")
    # Mood checkin: once in mid-day if no mood today
    if latest.get("mood_logged_today") is False and now.hour in range(12,16):
        eligible.append("mood_checkin")
    # Breathing: fallback micro-break if nothing else
    if not eligible:
        eligible.append("breathe")
    return eligible
