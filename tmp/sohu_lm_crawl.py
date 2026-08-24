import csv, hashlib, hmac, json, random, re, threading, time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone, timedelta
import requests

PROFILE='https://mp.sohu.com/profile?xpt=RDM4QjdFRURBRDdBRERCMTgxRjFFRjVFRkNBMTg5NDFAcXEuc29odS5jb20='
ODIN='https://odin.sohu.com/odin/api/blockdata'
RSSHUB_SOURCE='https://raw.githubusercontent.com/DIYgod/RSSHub/master/lib/routes/sohu/mp.tsx'
KEY='FeedSlideloadAuthor_2_0_pc_1655965929143_data2'
MKEY='252291'
MAX_PAGE=450
WORKERS=4
TZ8=timezone(timedelta(hours=8))
START=datetime(2025,1,1,tzinfo=TZ8)
END=datetime(2026,8,19,23,59,59,999999,tzinfo=TZ8)
CHARS='ABCDEFGHJKMNPQRSTWXYZabcdefhijkmnprstwxyz2345678'
local=threading.local()

def rand(n): return ''.join(random.choice(CHARS) for _ in range(n))

# Keep the public web-signing implementation sourced from its maintained open-source implementation.
src=requests.get(RSSHUB_SOURCE,timeout=30).text
m=re.search(r"HmacSHA1\(e,\s*'([^']+)'\)",src)
if not m: raise RuntimeError('Could not locate current public Sohu signing constant in RSSHub source')
SIGNING_KEY=m.group(1).encode()

def make_asid():
    ms=int(time.time()*1000)
    sig=hmac.new(SIGNING_KEY,f't{ms}'.encode(),hashlib.sha1).hexdigest()
    return f'v1{ms}{sig}'

bootstrap=requests.Session()
bootstrap.headers['User-Agent']='Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/124 Safari/537.36'
pr=bootstrap.get(PROFILE,timeout=30); pr.raise_for_status()
suv=bootstrap.cookies.get('SUV') or ''

def client():
    if not hasattr(local,'s'):
        s=requests.Session()
        s.headers.update({'User-Agent':bootstrap.headers['User-Agent'],'Accept':'application/json, text/plain, */*','Content-Type':'application/json','Origin':'https://mp.sohu.com','Referer':PROFILE})
        local.s=s
    return local.s

def request_page(page):
    last=None
    for attempt in range(4):
        try:
            now=int(time.time()*1000)
            body={'pvId':f'{now}_{rand(7)}','pageId':f'{now}_1612268936507_{rand(3)}','mainContent':{'productType':'13','productId':'324','secureScore':'5','categoryId':'47','adTags':'11111111','authorId':121135924},'resourceList':[{'tplCompKey':KEY,'isServerRender':True,'isSingleAd':False,'configSource':'mp','content':{'productId':'325','productType':'13','size':20,'pro':'0,1,3,4,5','feedType':'XTOPIC_SYNTHETICAL','view':'operateFeedMode','innerTag':'work','spm':'smpc.channel_248.block3_308_hHsK47_2_fd','page':page,'requestId':f'{now}{rand(7)}_324'},'adInfo':{},'context':{'mkey':MKEY}}],'asId':make_asid()}
            cookies=(([f'SUV={suv}'] if suv else [])+['itssohu=true','reqtype=pc',f't={now}'])
            r=client().post(ODIN,json=body,headers={'Cookie':'; '.join(cookies)},timeout=30); r.raise_for_status()
            node=(r.json().get('data') or {}).get(KEY) or {}
            out=[]
            for x in node.get('list') or []:
                if x.get('postTime') is None: continue
                dt=datetime.fromtimestamp(int(x['postTime'])/1000,tz=TZ8)
                out.append({'id':int(x['id']),'title':str(x.get('title') or '').strip(),'post_time_ms':int(x['postTime']),'datetime':dt.isoformat(),'date':dt.strftime('%Y-%m-%d'),'url':f"https://www.sohu.com/a/{x['id']}_{MKEY}",'page':page})
            return page,out,None
        except Exception as e:
            last=repr(e); time.sleep(0.5*(attempt+1))
    return page,[],last

p1=request_page(1); p2=request_page(2)
probe={'page1_count':len(p1[1]),'page2_count':len(p2[1]),'page1_error':p1[2],'page2_error':p2[2],'page1_first':p1[1][0] if p1[1] else None,'page1_last':p1[1][-1] if p1[1] else None,'page2_first':p2[1][0] if p2[1] else None,'page2_last':p2[1][-1] if p2[1] else None,'overlap':len({x['id'] for x in p1[1]} & {x['id'] for x in p2[1]})}
print('PROBE',json.dumps(probe,ensure_ascii=False),flush=True)
pages={1:p1[1],2:p2[1]}; errors={}
if p1[2]: errors[1]=p1[2]
if p2[2]: errors[2]=p2[2]

if p1[1] and p2[1] and {x['id'] for x in p1[1]} != {x['id'] for x in p2[1]}:
    with ThreadPoolExecutor(max_workers=WORKERS) as ex:
        futs=[ex.submit(request_page,p) for p in range(3,MAX_PAGE+1)]
        done=2
        for fut in as_completed(futs):
            p,items,err=fut.result(); pages[p]=items
            if err: errors[p]=err
            done+=1
            if done%25==0: print('PROGRESS',done,MAX_PAGE,'errors',len(errors),flush=True)

ordered=[]
for p in range(1,MAX_PAGE+1): ordered.extend(pages.get(p,[]))
by_id={}
for x in ordered: by_id.setdefault(x['id'],x)
all_items=sorted(by_id.values(),key=lambda x:x['post_time_ms'],reverse=True)
selected=[x for x in all_items if START <= datetime.fromtimestamp(x['post_time_ms']/1000,tz=TZ8) <= END]
nonempty=[p for p,v in pages.items() if v]
summary={'status':'complete' if not errors else 'complete_with_errors','probe':probe,'pages_requested':MAX_PAGE,'pages_with_items':len(nonempty),'max_nonempty_page':max(nonempty) if nonempty else None,'failed_pages':errors,'unique_items':len(all_items),'expected_profile_content_count':8448,'difference_from_profile_count':len(all_items)-8448,'newest_datetime':all_items[0]['datetime'] if all_items else None,'oldest_datetime':all_items[-1]['datetime'] if all_items else None,'selected_count':len(selected),'selected_newest_datetime':selected[0]['datetime'] if selected else None,'selected_oldest_datetime':selected[-1]['datetime'] if selected else None}
open('sohu_summary.json','w',encoding='utf-8').write(json.dumps(summary,ensure_ascii=False,indent=2))
open('sohu_all.json','w',encoding='utf-8').write(json.dumps(all_items,ensure_ascii=False,indent=2))
open('sohu_selected.json','w',encoding='utf-8').write(json.dumps(selected,ensure_ascii=False,indent=2))
with open('sohu_selected.csv','w',encoding='utf-8-sig',newline='') as f:
    w=csv.DictWriter(f,fieldnames=['date','datetime','title','id','url','page']); w.writeheader()
    for x in selected: w.writerow({k:x[k] for k in w.fieldnames})
print('SUMMARY',json.dumps(summary,ensure_ascii=False),flush=True)
