import base64,hashlib,json,os,shutil
ROOT='tmp/lmcmr-title-export'; ZIP=f'{ROOT}/LMCMR_2025_2026_report_titles.zip'; OUT=f'{ROOT}/zip_chunks_large'
raw=open(ZIP,'rb').read(); b64=base64.b64encode(raw).decode('ascii'); sha=hashlib.sha256(raw).hexdigest()
if os.path.exists(OUT): shutil.rmtree(OUT)
os.makedirs(OUT)
size=180000; chunks=[]
for i in range(0,len(b64),size):
    name=f'{i//size:03d}.txt'; open(f'{OUT}/{name}','w',encoding='ascii').write(b64[i:i+size]); chunks.append(name)
manifest={'filename':os.path.basename(ZIP),'zip_bytes':len(raw),'sha256':sha,'base64_chars':len(b64),'chunk_size_chars':size,'chunk_count':len(chunks),'chunks':chunks}
open(f'{OUT}/manifest.json','w',encoding='utf-8').write(json.dumps(manifest,indent=2))
print(json.dumps(manifest))
