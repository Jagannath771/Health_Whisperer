# services/nutrition_llm.py
import os, json, math
from datetime import datetime, timezone, timedelta
from typing import Dict, Any, List, Optional

import google.generativeai as genai
from supabase import create_client

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
MODEL = genai.GenerativeModel("gemini-1.5-flash")

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

# Convert a JSON-like string to dict safely
def _parse_json(text: str) -> dict:
    try:
        return json.loads(text)
    except Exception:
        # best effort: extract between first and last {...}
        start = text.find("{")
        end = text.rfind("}")
        if start >= 0 and end > start:
            try:
                return json.loads(text[start:end+1])
            except Exception:
                return {}
        return {}

# ---------- LLM prompts ----------
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
          "qty_g": {"type": "number"},        # grams inferred (no hard-coded mapping)
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

SYSTEM = """You are a nutrition estimator. Parse the user's meal text into JSON that matches the provided schema.
Infer portion sizes in grams and estimate nutrition WITHOUT asking questions. 
Use regional dish knowledge (e.g., pesarattu is a moong-dal crepe). 
Account for oils/chutneys mentioned. 
Return ONLY minified JSON (no prose), strictly following the schema."""

def llm_extract_meal(text: str) -> dict:
    user = f"Text: {text}\nSchema (JSON): {json.dumps(SCHEMA)}\nReturn only JSON."
    out = MODEL.generate_content([{"role":"system","parts":[SYSTEM]},
                                  {"role":"user","parts":[user]}])
    raw = out.text.strip() if hasattr(out, "text") else "{}"
    data = _parse_json(raw)
    # fill sane defaults
    data.setdefault("meal_type", "unknown")
    data.setdefault("items", [])
    data.setdefault("totals", {})
    data["confidence"] = _clamp(data.get("confidence", 0.6), 0.0, 1.0)
    return data

# ---------- Reference lookup (optional) ----------
def _embed(text: str) -> List[float]:
    # Gemini embeddings: 768 dims for text-embedding-004
    e = genai.embed_content(model="text-embedding-004", content=text)
    return e["embedding"]["values"]

def _search_food_reference(query: str, top_k: int = 3) -> List[dict]:
    v = _embed(query)
    res = sb.rpc("match_foods", {"query_embedding": v, "match_count": top_k}).execute()
    # If you don't want to define a RPC, fallback to simple ILIKE match:
    if getattr(res, "data", None):
        return res.data
    # Fallback: naive alias search
    alt = sb.table("hw_food_nutrition").select("*").ilike("food_name", f"%{query}%").limit(top_k).execute().data or []
    return alt

# (create this SQL function once if you want vector search)
# select id, food_name, aliases, base_qty_g, calories, protein_g, carbs_g, fat_g, fiber_g, sugar_g, sodium_mg, micros
# from hw_food_nutrition
# order by embedding <=> query_embedding
# limit match_count;

# ---------- Nutrition estimation & save ----------
def estimate_meal(text: str) -> dict:
    """
    1) LLM parses & estimates portions + totals.
    2) (Optional) For each item, we try to refine using closest food reference row per 100g.
       If found, we adjust totals based on qty_g.
       If not found, keep LLM estimate.
    """
    data = llm_extract_meal(text)
    items = data.get("items", [])
    totals = data.get("totals") or {}
    # If LLM didn't include totals, compute from references where possible:
    agg = {k: 0.0 for k in ["calories","protein_g","carbs_g","fat_g","fiber_g","sugar_g","sodium_mg"]}

    # If LLM gave totals with reasonable numbers, adopt them; else recompute
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
            else:
                # No reference found; ask LLM for per-100g macros for this item
                sub = MODEL.generate_content(f"Give per-100g estimate as JSON: "
                                             f'{{"calories":...,"protein_g":...,"carbs_g":...,"fat_g":...,"fiber_g":...,"sugar_g":...,"sodium_mg":...}} '
                                             f"for: {name}. No prose.")
                ref = _parse_json(getattr(sub, "text", "{}"))
                for k in agg.keys():
                    val = ref.get(k)
                    if val is not None:
                        agg[k] += float(val) * (qty_g / 100.0)
        totals = {k: int(round(v)) for k, v in agg.items()}
        data["totals"] = totals

    return data

def save_meal(uid: str, raw_text: str, parsed: dict, when_utc: Optional[datetime] = None, meal_type: Optional[str] = None):
    when_utc = when_utc or _now_utc()
    mt = meal_type or parsed.get("meal_type") or "unknown"
    totals = parsed.get("totals") or {}
    items = parsed.get("items") or []
    conf = float(parsed.get("confidence") or 0.6)

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

def upsert_today_totals(uid: str):
    """
    Aggregate today's meals and upsert totals into hw_metrics so your dashboard/worker see them.
    """
    # Pull meals for the last 24h
    since = (_now_utc() - timedelta(hours=24)).isoformat()
    meals = sb.table("hw_meals").select("calories,protein_g,carbs_g,fat_g,fiber_g,sugar_g,sodium_mg")\
               .eq("uid", uid).gte("ts", since).execute().data or []

    def s(key): return sum(int(m.get(key) or 0) for m in meals)
    totals = {
        "calories": s("calories"),
        "protein_g": s("protein_g"),
        "carbs_g": s("carbs_g"),
        "fat_g": s("fat_g"),
        "fiber_g": s("fiber_g"),
        "sugar_g": s("sugar_g"),
        "sodium_mg": s("sodium_mg"),
    }
    # Write a metrics row (source=aggregate)
    sb.table("hw_metrics").insert({
        "uid": uid,
        "source": "aggregate",
        "calories": totals["calories"],
        # you can add more columns to hw_metrics later (protein_g, etc.) if you wish
        "notes": "auto-upsert from meals aggregation"
    }).execute()
    return totals

def parse_and_log(uid: str, meal_text: str, meal_type_hint: Optional[str] = None):
    parsed = estimate_meal(meal_text)
    if meal_type_hint and parsed.get("meal_type", "unknown") == "unknown":
        parsed["meal_type"] = meal_type_hint
    saved = save_meal(uid, meal_text, parsed)
    totals = upsert_today_totals(uid)
    return {"saved": saved, "day_totals": totals, "parsed": parsed}
