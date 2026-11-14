import time

from common import config
from common.jobs import r

# Atomically reserve the next free call slot, spaced 60/LLM_RPM seconds apart.
_RESERVE = r.register_script("""
local t = redis.call('TIME')
local now = tonumber(t[1]) + tonumber(t[2]) / 1000000
local slot = math.max(now, tonumber(redis.call('GET', KEYS[1]) or 0) + tonumber(ARGV[1]))
redis.call('SET', KEYS[1], tostring(slot), 'EX', 3600)
return tostring(slot - now)
""")

def wait_for_llm_slot():
    wait = float(_RESERVE(keys=["llm:next_slot"], args=[60 / config.LLM_RPM]))
    if wait > 0.5:
        print(f"LLM rate limiter: waiting {wait:.1f}s for a free slot")
    time.sleep(max(wait, 0))
