# utils/db.py
import time
from httpx import ReadError

def exec_with_retry(req, tries: int = 3, base_delay: float = 0.4):
    """
    Executes a Supabase/Postgrest request with simple retries to smooth out
    transient Windows non-blocking socket (WinError 10035) read errors.
    """
    for i in range(tries):
        try:
            return req.execute()
        except Exception as e:
            msg = str(e)
            if "10035" in msg or isinstance(e, ReadError):
                time.sleep(base_delay * (i + 1))
                continue
            raise
    return req.execute()
