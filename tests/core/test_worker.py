import json
import pytest
from oneai.config import Config
from oneai.tasks import Tasks
from oneai.worker import tick, ingest_mail
from connectors.outlook.connector import DeltaStore


def cfg(tmp_path):
    c=Config(tmp_path/'vault',tmp_path/'state',None); c.ensure_dirs(); return c


def test_phone_to_evidence_draft_revision_and_archive(tmp_path):
    c=cfg(tmp_path)
    (c.vault_path/'facts').mkdir()
    (c.vault_path/'facts/research.md').write_text('# Gaussian\n\nGaussian research notes with enough context to find the original evidence.')
    commands=c.vault_path/'inbox/commands'; commands.mkdir(parents=True)
    (commands/'1.json').write_text(json.dumps({'id':'request1','action':'create','title':'Gaussian','body':'Prepare a research reply'}))
    assert tick(c)['prepared']==1
    tasks=Tasks(c.state_path/'tasks.sqlite')
    row=dict(tasks.db.execute('SELECT * FROM tasks').fetchone())
    assert row['status']=='needs_review' and 'facts/research.md@' in row['sources']
    assert tick(c)['prepared']==0
    tasks.command({'id':'a','action':'approve','task_id':row['id'],'revision':1})
    tasks.command({'id':'r','action':'revise','task_id':row['id'],'revision':1,'body':'User corrected draft'})
    assert tasks.get(row['id'])['approved_revision'] is None
    with pytest.raises(ValueError,match='Stale'):
        tasks.command({'id':'stale','action':'approve','task_id':row['id'],'revision':1})
    tasks.command({'id':'done','action':'complete','task_id':row['id'],'revision':2})
    assert tasks.get(row['id'])['status']=='completed'
    tasks.db.close()


def test_mail_replay_stale_and_tombstone(tmp_path):
    c=cfg(tmp_path); store=DeltaStore(c.state_path/'outlook.sqlite','a')
    end='https://graph.microsoft.com/v1.0/me/mailFolders/inbox/messages/delta?token=x'
    store.apply({'value':[{'id':'m','subject':'old','bodyPreview':'a'}],'@odata.deltaLink':end})
    store.apply({'value':[{'id':'m','subject':'new'}],'@odata.deltaLink':end})
    tasks=Tasks(c.state_path/'tasks.sqlite')
    assert ingest_mail(c,tasks)==1
    # Emulate crash after task commit but before the mailbox receipt.
    with store.db: store.db.execute("UPDATE work SET status='pending'")
    ingest_mail(c,tasks)
    assert tasks.db.execute('SELECT COUNT(*) FROM tasks').fetchone()[0]==1
    store.apply({'value':[{'id':'m','@removed':{}}],'@odata.deltaLink':end})
    assert ingest_mail(c,tasks)==0
    store.db.close(); tasks.db.close()


def test_command_receipt_rejects_changed_payload_and_send(tmp_path):
    tasks=Tasks(tmp_path/'tasks.sqlite')
    c={'id':'x','action':'create','title':'t','body':'b'}
    tasks.command(c); tasks.command(c)
    with pytest.raises(ValueError,match='reused'): tasks.command({**c,'body':'different'})
    with pytest.raises(ValueError,match='Unsupported'): tasks.command({'id':'send','action':'send'})
    assert tasks.db.execute('SELECT COUNT(*) FROM tasks').fetchone()[0]==1
    tasks.db.close()
