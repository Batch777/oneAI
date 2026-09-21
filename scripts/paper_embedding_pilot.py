"""Small local dense-retrieval smoke test, NOT a statistically valid benchmark.

Uses first 3 pages per PDF, up to 2,000 characters per chunk with 200 overlap.
No text leaves the machine. Downloads public model weights into output/models.
"""
import argparse
import json
import os
from pathlib import Path
import sqlite3
import time

parser = argparse.ArgumentParser(__doc__)
parser.add_argument('--output', type=Path, required=True)
parser.add_argument('--model', choices=['BAAI/bge-m3', 'Qwen/Qwen3-Embedding-0.6B'], required=True)
args = parser.parse_args()
os.environ['HF_HOME'] = str((args.output / 'models').resolve())
os.environ['HF_HUB_DISABLE_TELEMETRY'] = '1'
from sentence_transformers import SentenceTransformer
import torch

# Manually grounded questions; a smoke-test target isn't an exhaustive relevance label.
queries = [
 ('哪篇论文通过两阶段预取流水线重叠计算与内存访问来加速高斯渲染？', 'FlashGS.pdf', 2),
 ('Which method reports up to four times speedup and 49 percent memory reduction?', 'FlashGS.pdf', 2),
 ('哪种方法能够从时序多视角图像直接预测驾驶场景的四维高斯，并编辑或移除物体？', 'DrivingRecon.pdf', 1),
 ('Which work combines context awareness with deformation awareness for dynamic driving scenes?', 'CoDa-4DGS.pdf', 1),
]
with sqlite3.connect(args.output / 'pages.sqlite') as db:
    rows = db.execute('SELECT version,path,page,text FROM pages WHERE CAST(page AS INTEGER)<=3 ORDER BY path,page').fetchall()
chunks = [(v,p,n,text[start:start+2000]) for v,p,n,text in rows for start in range(0,len(text),1800) if len(text[start:start+2000].strip())>=100]
started = time.monotonic()
device = 'mps' if torch.backends.mps.is_available() else 'cpu'
model = SentenceTransformer(args.model, device=device, model_kwargs={'dtype': torch.float32})
model.max_seq_length = 1024
loaded = time.monotonic()
vectors = model.encode([c[3] for c in chunks], batch_size=4, normalize_embeddings=True, show_progress_bar=True)
kw = {'prompt': 'Instruct: Given a research question, retrieve passages from scientific papers that answer it.\nQuery: '} if args.model.startswith('Qwen/') else {}
query_vectors = model.encode([q[0] for q in queries], normalize_embeddings=True, **kw)
results=[]
for (query,target,page), scores in zip(queries, query_vectors @ vectors.T):
    order = scores.argsort()[::-1]
    target_rank = next((rank for rank,i in enumerate(order,1) if Path(chunks[i][1]).name==target and int(chunks[i][2])==page),None)
    results.append({'query':query,'target':target,'page':page,'target_chunk_rank':target_rank,'top5':[{'file':Path(chunks[i][1]).name,'page':chunks[i][2],'score':float(scores[i])} for i in order[:5]]})
report={'model':args.model,'device':device,'chunks':len(chunks),'documents':len(set(c[0] for c in chunks)),'load_seconds':loaded-started,'encode_seconds':time.monotonic()-loaded,'max_seq_length':1024,'results':results,'warning':'Four hand-picked known-source queries on first-three-page chunks; dense only, no reranking. Not a benchmark or basis to declare a winner.'}
path=args.output/(args.model.split('/')[-1]+'-smoke.json')
path.write_text(json.dumps(report,ensure_ascii=False,indent=2))
print(json.dumps(report,ensure_ascii=False,indent=2))
