"""Provider facts only. Missing usage is unknown; token counts are not bills."""
import math
import time

def number(value):
    return value if type(value) in (int,float) and math.isfinite(value) and value>=0 else None

def codex_usage(value):
    total=value.get('total') or {};last=value.get('last') or {}
    return {'source':'codex','observed_at':time.time(),'input':number(total.get('inputTokens')),'output':number(total.get('outputTokens')),'cached_input':number(total.get('cachedInputTokens')),'reasoning_output':number(total.get('reasoningOutputTokens')),'total':number(total.get('totalTokens')),'context_tokens':number(last.get('totalTokens')),'context_window':number(value.get('modelContextWindow')),'cost_usd':None,'cost_kind':'not_reported'}

def pi_usage(value):
    tokens=value.get('tokens') or {};context=value.get('contextUsage') or {}
    return {'source':'pi','observed_at':time.time(),'input':number(tokens.get('input')),'output':number(tokens.get('output')),'cached_input':number(tokens.get('cacheRead')),'cache_write':number(tokens.get('cacheWrite')),'total':number(tokens.get('total')),'context_tokens':number(context.get('tokens')),'context_window':number(context.get('contextWindow')),'cost_usd':number(value.get('cost')),'cost_kind':'runtime_estimate_not_invoice'}

def quota(value):
    buckets=value.get('rateLimitsByLimitId')
    if not isinstance(buckets,dict) or not buckets:buckets={'codex':value.get('rateLimits') or {}}
    result=[]
    for key,bucket in list(buckets.items())[:20]:
        for name in ('primary','secondary'):
            window=bucket.get(name) or {};used=number(window.get('usedPercent'))
            if used is not None:result.append({'bucket':str(key)[:100],'window':name,'remaining_percent':max(0,min(100,100-used)),'window_minutes':number(window.get('windowDurationMins')),'resets_at':number(window.get('resetsAt'))})
    return {'observed_at':time.time(),'windows':result,'scope':'account_shared'}
