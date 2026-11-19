import time

from common.jobs import r

# Atomically reserve the next free call slot, spaced 60/LLM_RPM seconds apart.
_RESERVE = r.register_script("""
local t = redis.call('TIME')
local now = tonumber(t[1]) + tonumber(t[2]) / 1000000
local slot = math.max(now, tonumber(redis.call('GET', KEYS[1]) or 0) + tonumber(ARGV[1]))
redis.call('SET', KEYS[1], tostring(slot), 'EX', 3600)
return tostring(slot - now)
""")

def wait_for_llm_slot(model, rpm):
    wait = float(_RESERVE(keys=[f"llm:next_slot:{model}"], args=[60 / rpm]))
    if wait > 0.5:
        print(f"LLM rate limiter ({model}): waiting {wait:.1f}s for a free slot")
    time.sleep(max(wait, 0))
