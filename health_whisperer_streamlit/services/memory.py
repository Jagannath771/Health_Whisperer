# services/memory.py
import os
from datetime import datetime, timezone
from typing import List, Optional

import google.generativeai as genai
from supabase import create_client

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

def _now(): return datetime.now(timezone.utc)

def embed_text(text: str) -> List[float]:
    e = genai.embed_content(model="text-embedding-004", content=text)
    return e["embedding"]["values"]

def log_chat(uid: str, role: str, text: str, metadata: dict | None = None):
    emb = embed_text(text)
    sb.table("hw_chat_history").insert({
        "uid": uid,
        "role": role,
        "text": text,
        "metadata": metadata or {},
        "embedding": emb
    }).execute()

def retrieve_context(uid: str, query: str, k: int = 6) -> list[dict]:
    qv = embed_text(query)
    # simple vector search
    res = sb.rpc("match_user_history", {"uid_in": uid, "query_embedding": qv, "match_count": k}).execute()
    if getattr(res, "data", None):
        return res.data
    # fallback: last k messages
    msgs = (
        sb.table("hw_chat_history").select("*").eq("uid", uid)
        .order("ts", desc=True).limit(k).execute().data or []
    )
    return msgs

def update_user_summary(uid: str):
    # Summarize last N messages into a long-term memory note
    msgs = (
        sb.table("hw_chat_history").select("role,text").eq("uid", uid)
        .order("ts", desc=True).limit(50).execute().data or []
    )
    if not msgs: return None
    convo = "\n".join([f"{m['role']}: {m['text']}" for m in msgs[::-1]])
    prompt = f"""Summarize the user’s stable preferences, routines, constraints, and health goals from this chat.
Return a concise paragraph (<= 10 lines). Avoid PII. 
Conversation:
{convo}
"""
    out = MODEL.generate_content(prompt)
    summ = (out.text or "").strip()
    emb = embed_text(summ)
    sb.table("hw_user_memory").upsert({"uid": uid, "summary": summ, "updated_at": _now().isoformat(), "embedding": emb}).execute()
    return summ

def personal_context(uid: str, query_hint: str = "nudges") -> str:
    # Combine long-term memory + top-k recent
    mem = sb.table("hw_user_memory").select("summary").eq("uid", uid).maybe_single().execute().data
    summ = (mem or {}).get("summary") or ""
    recents = retrieve_context(uid, query_hint, k=5)
    tail = "\n".join([f"- {m.get('text','')}" for m in recents])
    ctx = f"Long-term summary:\n{summ}\n\nRecent notes:\n{tail}"
    return ctx
