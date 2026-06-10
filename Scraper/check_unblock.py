"""
Quick probe to check if NYT / Reuters IP blocks have lifted.
Run this periodically:  python check_unblock.py

When a domain shows "UNBLOCKED", immediately run:
    python media_pipeline.py --mode patch --domain <domain>
"""
from curl_cffi import requests

PROBES = {
    "nytimes.com": {
        "url": "https://www.nytimes.com/2025/05/07/us/trump-biden-interview.html",
        "headers": {"Referer": "https://www.google.com/"},
        "blocked_codes": (403,),
    },
    "reuters.com": {
        "url": "https://www.reuters.com/world/us/nearly-500-tsa-agents-quit-us-airport-security-delays-continue-2026-03-26/",
        "headers": {},
        "blocked_codes": (401,),
    },
}

for domain, cfg in PROBES.items():
    try:
        s = requests.Session(impersonate="chrome")
        r = s.get(cfg["url"], timeout=10, headers=cfg["headers"])
        if r.status_code in cfg["blocked_codes"]:
            print(f"  {domain}: BLOCKED ({r.status_code})")
        elif r.status_code == 200:
            if len(r.text) > 5000:
                print(f"  {domain}: UNBLOCKED  <-- run: python media_pipeline.py --mode patch --domain {domain}")
            else:
                print(f"  {domain}: 200 but suspiciously short ({len(r.text)}c) -- may still be blocked")
        else:
            print(f"  {domain}: {r.status_code} (unexpected)")
    except Exception as e:
        print(f"  {domain}: ERROR ({e})")
