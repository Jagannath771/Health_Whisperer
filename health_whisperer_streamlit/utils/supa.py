# utils/supa.py
import os
from supabase import create_client

def get_supabase(client_role="anon"):
    url = os.getenv("SUPABASE_URL")
    if client_role == "service":
        key = os.getenv("SUPABASE_SERVICE_ROLE_KEY")
    else:
        key = os.getenv("SUPABASE_ANON_KEY")
    return create_client(url, key)
