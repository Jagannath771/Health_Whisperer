# services/nutrition_llm.py
import os, json, math
from datetime import datetime, timezone, timedelta
from typing import Dict, Any, List, Optional
import google.generativeai as genai
from supabase import create_client
from zoneinfo import ZoneInfo

# ---------- Setup ----------
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

if not (SUPABASE_URL and SUPABASE_KEY):
    raise RuntimeError("Missing SUPABASE_URL or SUPABASE_KEY")
sb = create_client(SUPABASE_URL, SUPABASE_KEY)

if not GEMINI_API_KEY:
    raise RuntimeError("Missing GEMINI_API_KEY")
genai.configure(api_key=GEMINI_API_KEY)

SYSTEM_INSTRUCTION = """You are a nutrition estimator. Parse the user's meal text into JSON that matches the provided schema.
Infer portion sizes in grams and estimate nutrition WITHOUT asking questions.
Use regional dish knowledge (e.g., pesarattu is a moong-dal crepe).
Account for oils/chutneys mentioned.
Return ONLY minified JSON (no prose), strictly following the schema.
If something is unknown, make a best-effort estimate rather than leaving it blank.
"""

MODEL = genai.GenerativeModel(
    model_name="gemini-1.5-flash",
    system_instruction=SYSTEM_INSTRUCTION
)

# ---------- Helpers ----------
def _now_utc():
    return datetime.now(timezone.utc)

def _safe_int(x, default=0):
    try: return int(x)
    except: return default

def _clamp(v, lo, hi):
    try:
        return max(lo, min(hi, float(v)))
    except:
        return lo

def _parse_json(text: str) -> dict:
    try:
        return json.loads(text)
    except Exception:
        start = text.find("{")
        end = text.rfind("}")
        if start >= 0 and end > start:
            try:
                return json.loads(text[start:end+1])
            except Exception:
                return {}
        return {}

SCHEMA = {
  "type": "object",
  "properties": {
    "meal_type": {"type": "string", "enum": ["breakfast", "lunch", "dinner", "snacks", "unknown"]},
    "items": {
      "type": "array",
      "items": {
        "type": "object",
        "properties": {
          "name": {"type": "string"},
          "qty_g": {"type": "number"},
          "notes": {"type": "string"}
        },
        "required": ["name", "qty_g"]
      }
    },
    "totals": {
      "type": "object",
      "properties": {
        "calories": {"type": "number"},
        "protein_g": {"type": "number"},
        "carbs_g": {"type": "number"},
        "fat_g": {"type": "number"},
        "fiber_g": {"type": "number"},
        "sugar_g": {"type": "number"},
        "sodium_mg": {"type": "number"}
      }
    },
    "confidence": {"type": "number"}
  },
  "required": ["items"]
}

def llm_extract_meal(text: str) -> dict:
    user = (
        "Parse the following meal into the schema below. "
        "Return only compact JSON, no prose.\n\n"
        f"Text: {text}\n\nSchema (JSON): {json.dumps(SCHEMA)}"
    )
    out = MODEL.generate_content(user)
    raw = out.text.strip() if hasattr(out, "text") and out.text else "{}"
    data = _parse_json(raw)
    data.setdefault("meal_type", "unknown")
    data.setdefault("items", [])
    data.setdefault("totals", {})
    data["confidence"] = _clamp(data.get("confidence", 0.7), 0.0, 1.0)
    return data

# Optional reference lookup (kept for better estimates later)
def _embed(text: str) -> List[float]:
    e = genai.embed_content(model="text-embedding-004", content=text)
    return e["embedding"]["values"]

def _search_food_reference(query: str, top_k: int = 3) -> List[dict]:
    try:
        v = _embed(query)
        res = sb.rpc("match_foods", {"query_embedding": v, "match_count": top_k}).execute()
        if getattr(res, "data", None):
            return res.data
    except Exception:
        pass
    alt = sb.table("hw_food_nutrition").select("*").ilike("food_name", f"%{query}%").limit(top_k).execute().data or []
    return alt

def estimate_meal(text: str) -> dict:
    """
    1) LLM parses & estimates portions + totals.
    2) If totals are missing, estimate via references per 100g.
    """
    data = llm_extract_meal(text)
    items = data.get("items", [])
    totals = data.get("totals") or {}
    agg = {k: 0.0 for k in ["calories","protein_g","carbs_g","fat_g","fiber_g","sugar_g","sodium_mg"]}
    llm_totals_ok = "calories" in totals and totals["calories"] and totals["calories"] > 0

    if not llm_totals_ok:
        for it in items:
            name = (it.get("name") or "").strip()
            qty_g = float(it.get("qty_g") or 0.0)
            if not name or qty_g <= 0:
                continue
            candidates = _search_food_reference(name, top_k=1)
            if candidates:
                r = candidates[0]
                per = max(1, int(r.get("base_qty_g") or 100))
                scale = qty_g / per
                for k in agg.keys():
                    val = r.get(k)
                    if val is not None:
                        agg[k] += float(val) * scale
        totals = {k: int(round(v)) for k, v in agg.items()}
        data["totals"] = totals

    return data

def save_meal(uid: str, raw_text: str, parsed: dict, when_utc: Optional[datetime] = None, meal_type: Optional[str] = None):
    when_utc = when_utc or _now_utc()
    mt = meal_type or parsed.get("meal_type") or "unknown"
    totals = parsed.get("totals") or {}
    items = parsed.get("items") or []
    conf = float(parsed.get("confidence") or 0.7)

    payload = {
        "uid": uid,
        "ts": when_utc.isoformat(),
        "meal_type": mt,
        "raw_text": raw_text,
        "items": items,
        "calories": _safe_int(totals.get("calories")),
        "protein_g": _safe_int(totals.get("protein_g")),
        "carbs_g": _safe_int(totals.get("carbs_g")),
        "fat_g": _safe_int(totals.get("fat_g")),
        "fiber_g": _safe_int(totals.get("fiber_g")),
        "sugar_g": _safe_int(totals.get("sugar_g")),
        "sodium_mg": _safe_int(totals.get("sodium_mg")),
        "micros": parsed.get("micros") or None,
        "source": "llm",
        "confidence": conf,
    }
    sb.table("hw_meals").insert(payload).execute()
    return payload

def _user_tz(sb, uid: str) -> ZoneInfo:
    try:
        r = sb.table("hw_preferences").select("tz").eq("uid", uid).maybe_single().execute()
        tz = (r.data or {}).get("tz") or "America/New_York"
    except Exception:
        tz = "America/New_York"
    try:
        return ZoneInfo(tz)
    except Exception:
        return ZoneInfo("America/New_York")

def _today_bounds_local_utc(uid: str):
    tz = _user_tz(sb, uid)
    now_local = datetime.now(timezone.utc).astimezone(tz)
    start_local = datetime(now_local.year, now_local.month, now_local.day, tzinfo=tz)
    return start_local.astimezone(timezone.utc).isoformat(), now_local.astimezone(timezone.utc).isoformat()

def upsert_today_totals(uid: str):
    start_iso, now_iso = _today_bounds_local_utc(uid)
    meals = (
        sb.table("hw_meals")
        .select("calories,protein_g,carbs_g,fat_g,fiber_g,sugar_g,sodium_mg")
        .eq("uid", uid)
        .gte("ts", start_iso)
        .lte("ts", now_iso)
        .execute().data or []
    )
    def s(key): return sum(int(m.get(key) or 0) for m in meals)
    totals = {k: s(k) for k in ["calories","protein_g","carbs_g","fat_g","fiber_g","sugar_g","sodium_mg"]}

    sb.table("hw_metrics").insert({
        "uid": uid,
        "source": "aggregate",
        "calories": totals["calories"],
        "notes": "auto-upsert from meals aggregation (today, local)"
    }).execute()
    return totals

def parse_and_log(uid: str, meal_text: str, meal_type_hint: Optional[str] = None):
    """
    Parse meal text with Gemini, save to hw_meals, upsert today's totals to hw_metrics,
    and emit a real-time event so the nudge worker can react immediately.
    """
    parsed = estimate_meal(meal_text)
    if meal_type_hint and parsed.get("meal_type", "unknown") == "unknown":
        parsed["meal_type"] = meal_type_hint

    saved = save_meal(uid, meal_text, parsed)
    totals = upsert_today_totals(uid)

    # Emit RT event for nudges
    try:
        sb.table("hw_events").insert({
            "uid": uid,
            "kind": "meal_logged",
            "payload": {
                "meal_type": saved.get("meal_type"),
                "calories": saved.get("calories"),
                "protein_g": saved.get("protein_g"),
                "sugar_g": saved.get("sugar_g"),
                "ts": saved.get("ts"),
                "raw_text": meal_text
            }
        }).execute()
    except Exception:
        pass

    return {"saved": saved, "day_totals": totals, "parsed": parsed}
