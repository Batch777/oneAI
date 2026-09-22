"""Five synthetic emails against live Jev; prints metrics, never input or secrets."""
import json
import time
import httpx
from oneai.jev import JevClassifier
from oneai.mailtriage import classify

CASES=[
 ('otp','verification','Your verification code','Use the code below to verify your email.\n001234\nExpires in ten minutes.'),
 ('promotion','promotion','Summer sale: 50% off','Shop our seasonal collection today. Unsubscribe at https://example.com/unsubscribe'),
 ('action','action','Please review the attached draft','Could you review my draft and reply with your corrections tomorrow?'),
 ('notification','notification','Your export is ready','The export you requested has finished. This is a status update; no reply is needed.'),
 ('mixed','uncertain','Weekly offers and a request','Our new products are on sale. Also, please reply to confirm the address for your existing order.')]

class Observed:
    provider='jev'
    def __init__(self):self.model=JevClassifier();self.error=None
    def __call__(self,excerpt):
        try:return self.model(excerpt)
        except httpx.HTTPStatusError as error:
            self.error='http_'+str(error.response.status_code);raise
        except Exception as error:
            self.error=type(error).__name__;raise

passed=0
for name,expected,title,body in CASES:
    model=Observed();start=time.perf_counter();decision=classify(title,body,model)
    ok=decision.source=='jev' and decision.category==expected
    passed+=int(ok)
    print(json.dumps({'case':name,'expected':expected,'actual':decision.category,
        'source':decision.source,'policy_score':decision.confidence,'filtered':decision.filtered,
        'duration_ms':round((time.perf_counter()-start)*1000,1),'error':model.error,'match':ok}),flush=True)
    if model.error:break  # Do not consume more requests after a configuration/billing failure.
print(json.dumps({'matched':passed,'total':len(CASES),'synthetic_only':True}))
raise SystemExit(0 if passed==len(CASES) else 1)
