import csv, gzip, io, json, os, re, time, zipfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from html import unescape
from urllib.parse import urlparse
import requests

ROOT='tmp/lmcmr-title-export'
os.makedirs(ROOT, exist_ok=True)
INDEX='tmp/lmcmr-crawl/lmcmr_all_index.json'
CC_CRAWLS=['CC-MAIN-2025-51','CC-MAIN-2025-47','CC-MAIN-2025-43','CC-MAIN-2025-38','CC-MAIN-2025-33','CC-MAIN-2025-26','CC-MAIN-2025-21','CC-MAIN-2025-13','CC-MAIN-2025-08','CC-MAIN-2025-05']
UA='LMCMR-title-archive-research/1.0'

with open(INDEX,encoding='utf-8') as f:
    current=json.load(f)

ver2026=[]
for x in current:
    title=str(x.get('title') or '').strip()
    if re.search(r'2026\s*版',title):
        ver2026.append({
            'version':'2026', 'title':title, 'url':x.get('url',''),
            'source':'LMCMR current site index', 'source_date':x.get('date',''),
            'confidence':'verified-current-title'
        })
ver2026.sort(key=lambda r:(r['title'],r['url']))
# URL-dedup
seen=set(); ver2026=[r for r in ver2026 if not (r['url'] in seen or seen.add(r['url']))]
print('2026_COUNT',len(ver2026),flush=True)

FIELDS=['version','title','url','source','source_date','confidence']
def write_csv(path,rows):
    with open(path,'w',encoding='utf-8-sig',newline='') as f:
        w=csv.DictWriter(f,fieldnames=FIELDS); w.writeheader(); w.writerows(rows)

def write_json(path,rows):
    with open(path,'w',encoding='utf-8') as f: json.dump(rows,f,ensure_ascii=False,indent=2)

write_csv(f'{ROOT}/lmcmr_2026_titles.csv',ver2026)
write_json(f'{ROOT}/lmcmr_2026_titles.json',ver2026)

s=requests.Session(); s.headers.update({'User-Agent':UA})

def cc_records(crawl):
    endpoint=f'https://index.commoncrawl.org/{crawl}-index'
    # Query both bare and www host through domain matching; collapse to one capture per URL in this crawl.
    params={'url':'lmcmr.com','matchType':'domain','output':'json','filter':'status:200','collapse':'urlkey'}
    r=s.get(endpoint,params=params,timeout=120)
    r.raise_for_status()
    records=[]
    for line in r.text.splitlines():
        try:
            x=json.loads(line)
        except Exception:
            continue
        u=x.get('url','')
        if not u or not u.lower().endswith('.html'):
            continue
        if not x.get('filename') or x.get('offset') is None or x.get('length') is None:
            continue
        records.append(x)
    print('CC_INDEX',crawl,len(records),flush=True)
    return records

def extract_html_from_warc(blob):
    try:
        raw=gzip.decompress(blob)
    except Exception:
        try:
            with gzip.GzipFile(fileobj=io.BytesIO(blob)) as g: raw=g.read()
        except Exception:
            raw=blob
    # WARC headers, then embedded HTTP headers, then body.
    pos=raw.find(b'\r\n\r\n')
    if pos>=0: raw=raw[pos+4:]
    pos=raw.find(b'\r\n\r\n')
    if pos>=0: raw=raw[pos+4:]
    for enc in ('utf-8','gb18030','latin1'):
        try: return raw.decode(enc,errors='replace')
        except Exception: pass
    return raw.decode('utf-8',errors='replace')

def title_from_html(html):
    # Prefer H1 because LM page <title> may append site name.
    for pat in [r'<h1[^>]*>(.*?)</h1>', r'<title[^>]*>(.*?)</title>']:
        m=re.search(pat,html,re.I|re.S)
        if m:
            t=re.sub(r'<[^>]+>',' ',m.group(1))
            t=unescape(re.sub(r'\s+',' ',t)).strip()
            t=re.sub(r'\s*[-_|]\s*LM立木信息咨询.*$','',t).strip()
            if t: return t
    return ''

def fetch_archived_title(rec,crawl):
    url='https://data.commoncrawl.org/'+rec['filename']
    off=int(rec['offset']); length=int(rec['length'])
    headers={'Range':f'bytes={off}-{off+length-1}','User-Agent':UA}
    last=''
    for a in range(3):
        try:
            r=requests.get(url,headers=headers,timeout=60)
            if r.status_code not in (200,206):
                last=f'http {r.status_code}'; time.sleep(.4*(a+1)); continue
            title=title_from_html(extract_html_from_warc(r.content))
            return {'url':rec.get('url',''),'title':title,'timestamp':rec.get('timestamp',''),'crawl':crawl,'error':''}
        except Exception as e:
            last=repr(e); time.sleep(.4*(a+1))
    return {'url':rec.get('url',''),'title':'','timestamp':rec.get('timestamp',''),'crawl':crawl,'error':last}

# Recover 2025 title snapshots. Stop asking older crawls for a URL once a verified 2025 title is found.
recovered={}
archive_errors=[]
for crawl in CC_CRAWLS:
    try:
        recs=cc_records(crawl)
    except Exception as e:
        print('CC_INDEX_ERROR',crawl,repr(e),flush=True); continue
    todo=[]
    for rec in recs:
        key=rec.get('url','').replace('http://','https://').replace('https://www.','https://')
        if key in recovered: continue
        todo.append(rec)
    # Bound concurrency to avoid hammering Common Crawl.
    with ThreadPoolExecutor(max_workers=10) as ex:
        futs={ex.submit(fetch_archived_title,r,crawl):r for r in todo}
        done=0; found=0
        for fut in as_completed(futs):
            z=fut.result(); done+=1
            if z['error']: archive_errors.append(z); continue
            if re.search(r'2025\s*版',z['title']):
                key=z['url'].replace('http://','https://').replace('https://www.','https://')
                recovered[key]={
                    'version':'2025','title':z['title'],'url':z['url'],
                    'source':f'Common Crawl {crawl}','source_date':z['timestamp'][:8],
                    'confidence':'verified-archived-title'
                }
                found+=1
            if done%500==0:
                print('CC_WARC_PROGRESS',crawl,done,'found_this_crawl',found,'total_recovered',len(recovered),flush=True)
    print('CC_CRAWL_DONE',crawl,'total_recovered',len(recovered),flush=True)
    # Once recovery is high, older crawls add diminishing value; still retain all configured crawls when count is modest.

ver2025=sorted(recovered.values(),key=lambda r:(r['title'],r['url']))
write_csv(f'{ROOT}/lmcmr_2025_titles_verified.csv',ver2025)
write_json(f'{ROOT}/lmcmr_2025_titles_verified.json',ver2025)

# Also generate a clearly labelled reconstruction for URLs whose current title is 2026 but no archived 2025 title was recovered.
# This is NOT presented as verified; it is useful only as a candidate cross-check list.
reconstructed=[]
verified_norm={r['url'].replace('http://','https://').replace('https://www.','https://') for r in ver2025}
for r in ver2026:
    norm=r['url'].replace('http://','https://').replace('https://www.','https://')
    if norm in verified_norm: continue
    candidate=re.sub(r'2026\s*版','2025版',r['title'])
    reconstructed.append({
        'version':'2025','title':candidate,'url':r['url'],'source':'derived from current 2026 title',
        'source_date':'','confidence':'reconstructed-not-verified'
    })
write_csv(f'{ROOT}/lmcmr_2025_titles_reconstructed_unverified.csv',reconstructed)
write_json(f'{ROOT}/lmcmr_2025_titles_reconstructed_unverified.json',reconstructed)

summary={
    'current_index_unique_urls':len(current),
    'verified_2026_titles':len(ver2026),
    'verified_archived_2025_titles':len(ver2025),
    'unverified_2025_reconstructions':len(reconstructed),
    'commoncrawl_crawls_attempted':CC_CRAWLS,
    'archive_fetch_errors':len(archive_errors),
    'note':'2025 reconstructed rows are explicitly unverified and must not be merged into the verified set without archival evidence.'
}
with open(f'{ROOT}/summary.json','w',encoding='utf-8') as f: json.dump(summary,f,ensure_ascii=False,indent=2)
with open(f'{ROOT}/README.txt','w',encoding='utf-8') as f:
    f.write('LMCMR report title export\n\n')
    f.write(json.dumps(summary,ensure_ascii=False,indent=2))
    f.write('\n\nThe paid full reports are not included. LMCMR states full PDF/Word reports are delivered after contract/payment.\n')

zip_path=f'{ROOT}/lmcmr_report_titles_2025_2026.zip'
with zipfile.ZipFile(zip_path,'w',zipfile.ZIP_DEFLATED) as z:
    for name in os.listdir(ROOT):
        if name.endswith(('.csv','.json','.txt')):
            z.write(os.path.join(ROOT,name),arcname=name)
print('SUMMARY',json.dumps(summary,ensure_ascii=False),flush=True)
