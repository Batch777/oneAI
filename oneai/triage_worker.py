"""Bounded Jev classification, independently scheduled from task preparation."""
import json
import time
from .config import Config
from .jev import JevClassifier
from .mailtriage import classify_pending
from .tasks import Tasks
from .vault import atomic_write


def tick(cfg, model_factory=JevClassifier):
    cfg.ensure_dirs()
    status={'provider':'jev','model':'jev-1.13.0','at':time.time(),'classified':0}
    if (cfg.state_path/'cloud-sync.json').exists():
        status['state']='cloud_managed'
    else:
        try:
            model=model_factory()
        except ValueError:
            status['state']='missing_key'
        else:
            store=Tasks(cfg.state_path/'tasks.sqlite')
            try:
                # Two requests at most, each with a 10s network timeout. Failures
                # become visible uncertain decisions, not an automatic paid retry loop.
                status['classified']=classify_pending(store,limit=2,model=model)
                status['state']='ready'
                status['fallback_total']=store.db.execute("SELECT count(*) FROM mail_triage WHERE source='fallback'").fetchone()[0]
            finally:store.db.close()
    atomic_write(cfg.state_path/'triage-status.json',json.dumps(status)+'\n')
    return status


def main():
    import fcntl
    cfg=Config.load();cfg.ensure_dirs()
    with (cfg.state_path/'triage.lock').open('a') as lock:
        try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:return
        print(json.dumps(tick(cfg)),flush=True)


if __name__=='__main__':main()
