import csv, hashlib, hmac, json, random, re, threading, time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone, timedelta
import requests
from bs4 import BeautifulSoup

PROFILE='https://mp.sohu.com/profile?xpt=RDM4QjdFRURBRDdBRERCMTgxRjFFRjVFRkNBMTg5NDFAcXEuc29odS5jb20='
ODIN='https://odin.sohu.com/odin/api/blockdata'
RSSHUB_SOURCE='https://raw.githubusercontent.com/DIYgod/RSSHub/master/lib/routes/sohu/mp.tsx'
KEY='FeedSlideloadAuthor_2_0_pc_1655965929143_data2'
MKEY='252291'
MAX_PAGE=450
ODIN_WORKERS=4
ARTICLE_WORKERS=4
TZ8=timezone(timedelta(hours=8))
START=datetime(2025,1,1,0,0,0,tzinfo=TZ8)
END=datetime(2026,8,19,23,59,59,999999,tzinfo=TZ8)
CHARS='ABCDEFGHJKMNPQRSTWXYZabcdefhijkmnprstwxyz2345678'
odin_local=threading.local()
article_local=threading.local()

def rand(n):
    return ''.join(random.choice(CHARS) for _ in range(n))

# Use RSSHub's maintained public Sohu implementation as the source of the browser signing constant.
src=requests.get(RSSHUB_SOURCE,timeout=30).text
m=re.search(r"HmacSHA1\(e,\s*'([^']+)'\)",src)
if not m:
    raise RuntimeError('Could not locate current public Sohu signing constant in RSSHub source')
SIGNING_KEY=m.group(1).encode()

def make_asid():
    ms=int(time.time()*1000)
    sig=hmac.new(SIGNING_KEY,f't{ms}'.encode(),hashlib.sha1).hexdigest()
    return f'v1{ms}{sig}'

bootstrap=requests.Session()
bootstrap.headers.update({
    'User-Agent':'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124 Safari/537.36',
    'Accept-Language':'zh-CN,zh;q=0.9,en;q=0.8',
})
pr=bootstrap.get(PROFILE,timeout=30)
pr.raise_for_status()
profile_html=pr.text
pvm=re.search(r'"pvId":"([^"]+)"',profile_html)
PV_ID=pvm.group(1) if pvm else f'{int(time.time()*1000)}_{rand(7)}'
suv_value=bootstrap.cookies.get('SUV') or ''
count_match=re.search(r'"column_5_text":(\d+),"column_9_text":"立木信息咨询',profile_html)
PROFILE_COUNT=int(count_match.group(1)) if count_match else 8448
print('BOOTSTRAP',json.dumps({'profile_status':pr.status_code,'profile_bytes':len(profile_html),'pv_id':PV_ID,'has_suv':bool(suv_value),'profile_count':PROFILE_COUNT},ensure_ascii=False),flush=True)

def odin_client():
    if not hasattr(odin_local,'s'):
        s=requests.Session()
        s.headers.update({
            'User-Agent':bootstrap.headers['User-Agent'],
            'Accept':'application/json, text/plain, */*',
            'Accept-Language':'zh-CN,zh;q=0.9,en;q=0.8',
            'Content-Type':'application/json',
            'Origin':'https://mp.sohu.com',
            'Referer':PROFILE,
        })
        odin_local.s=s
    return odin_local.s

def article_client():
    if not hasattr(article_local,'s'):
        s=requests.Session()
        s.headers.update({
            'User-Agent':bootstrap.headers['User-Agent'],
            'Accept':'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
            'Accept-Language':'zh-CN,zh;q=0.9,en;q=0.8',
        })
        article_local.s=s
    return article_local.s

def cookie_header(mode,now):
    base=['itssohu=true','reqtype=pc',f't={now}']
    if not suv_value or mode=='none':
        return '; '.join(base)
    if mode=='rsshub_double':
        return '; '.join([f'SUV=SUV={suv_value}']+base)
    return '; '.join([f'SUV={suv_value}']+base)

def request_page(page,cookie_mode='standard',diagnostic=False):
    last=None
    for attempt in range(4):
        try:
            now=int(time.time()*1000)
            body={
                'pvId':PV_ID,
                'pageId':f'{now}_1612268936507k_{rand(3)}',
                'mainContent':{
                    'productType':'13','productId':'324','secureScore':'5','categoryId':'47','adTags':'11111111','authorId':121135924
                },
                'resourceList':[{
                    'tplCompKey':KEY,
                    'isServerRender':True,
                    'isSingleAd':False,
                    'configSource':'mp',
                    'content':{
                        'productId':'325','productType':'13','size':20,'pro':'0,1,3,4,5','feedType':'XTOPIC_SYNTHETICAL',
                        'view':'operateFeedMode','innerTag':'work','spm':'smpc.channel_248.block3_308_hHsK47_2_fd',
                        'page':page,'requestId':f'{now}{rand(7)}_324'
                    },
                    'adInfo':{},
                    'context':{'mkey':MKEY}
                }],
                'asId':make_asid()
            }
            r=odin_client().post(ODIN,json=body,headers={'Cookie':cookie_header(cookie_mode,now)},timeout=30)
            r.raise_for_status()
            payload=r.json()
            data=payload.get('data') or {}
            node=data.get(KEY) or {}
            raw_list=node.get('list') or []
            out=[]
            for pos,x in enumerate(raw_list):
                if x.get('id') is None:
                    continue
                out.append({
                    'id':int(x['id']),
                    'title':str(x.get('title') or '').strip(),
                    'url':f"https://www.sohu.com/a/{x['id']}_{MKEY}",
                    'page':page,
                    'position':pos,
                })
            diag=None
            if diagnostic:
                first=raw_list[0] if raw_list else {}
                diag={
                    'http_status':r.status_code,
                    'cookie_mode':cookie_mode,
                    'payload_code':payload.get('code'),
                    'data_keys':list(data.keys())[:20],
                    'node_keys':list(node.keys())[:30],
                    'node_list_count':len(raw_list),
                    'first_item_keys':list(first.keys())[:50],
                    'first_item_id':first.get('id'),
                    'first_item_title':first.get('title'),
                    'first_item_extraInfoList':first.get('extraInfoList'),
                }
            return page,out,None,diag
        except Exception as e:
            last=repr(e)
            time.sleep(0.5*(attempt+1))
    return page,[],last,{'cookie_mode':cookie_mode,'error':last} if diagnostic else None

def fetch_article_date(item):
    last=None
    for attempt in range(4):
        try:
            r=article_client().get(item['url'],timeout=30)
            r.raise_for_status()
            soup=BeautifulSoup(r.text,'html.parser')
            meta=soup.find('meta',attrs={'itemprop':'dateUpdate'})
            value=(meta.get('content') if meta else None)
            if not value:
                # Fallback to the JSON-LD/OG publication date exposed on article pages.
                mm=re.search(r'"datePublished"\s*:\s*"([^"]+)"',r.text)
                value=mm.group(1) if mm else None
            if not value:
                raise ValueError('dateUpdate/datePublished not found')
            value=value.strip()
            parsed=None
            for fmt in ('%Y-%m-%d %H:%M:%S','%Y-%m-%d %H:%M','%Y-%m-%dT%H:%M:%S%z','%Y-%m-%dT%H:%M%z'):
                try:
                    parsed=datetime.strptime(value,fmt)
                    break
                except ValueError:
                    pass
            if parsed is None:
                raise ValueError(f'unparsed publication date: {value!r}')
            if parsed.tzinfo is None:
                parsed=parsed.replace(tzinfo=TZ8)
            else:
                parsed=parsed.astimezone(TZ8)
            x=dict(item)
            x['datetime']=parsed.isoformat()
            x['date']=parsed.strftime('%Y-%m-%d')
            x['timestamp']=int(parsed.timestamp())
            return x,None
        except Exception as e:
            last=repr(e)
            time.sleep(0.5*(attempt+1))
    return dict(item),last

# Pick a request context that actually returns the feed.
variant_results=[]
selected_mode=None
for mode in ('standard','rsshub_double','none'):
    test=request_page(1,mode,True)
    variant_results.append({'mode':mode,'count':len(test[1]),'error':test[2],'diagnostic':test[3]})
    if test[1] and selected_mode is None:
        selected_mode=mode
print('COOKIE_VARIANTS',json.dumps(variant_results,ensure_ascii=False),flush=True)

pages={}
odin_errors={}
if selected_mode:
    p1=request_page(1,selected_mode,True)
    p2=request_page(2,selected_mode,True)
    pages[1]=p1[1]
    pages[2]=p2[1]
    if p1[2]: odin_errors[1]=p1[2]
    if p2[2]: odin_errors[2]=p2[2]
else:
    p1=(1,[],None,None)
    p2=(2,[],None,None)

probe={
    'selected_cookie_mode':selected_mode,
    'variants':variant_results,
    'page1_count':len(p1[1]),
    'page2_count':len(p2[1]),
    'page1_first':p1[1][0] if p1[1] else None,
    'page1_last':p1[1][-1] if p1[1] else None,
    'page2_first':p2[1][0] if p2[1] else None,
    'page2_last':p2[1][-1] if p2[1] else None,
    'page1_page2_overlap':len({x['id'] for x in p1[1]} & {x['id'] for x in p2[1]}),
}
print('PROBE',json.dumps(probe,ensure_ascii=False),flush=True)

if selected_mode and p1[1] and p2[1] and {x['id'] for x in p1[1]} != {x['id'] for x in p2[1]}:
    with ThreadPoolExecutor(max_workers=ODIN_WORKERS) as ex:
        futs=[ex.submit(request_page,p,selected_mode,False) for p in range(3,MAX_PAGE+1)]
        done=2
        for fut in as_completed(futs):
            p,items,err,_=fut.result()
            pages[p]=items
            if err:
                odin_errors[p]=err
            done+=1
            if done%25==0 or done==MAX_PAGE:
                print('ODIN_PROGRESS',done,MAX_PAGE,'errors',len(odin_errors),flush=True)

ordered=[]
for p in range(1,MAX_PAGE+1):
    ordered.extend(pages.get(p,[]))
by_id={}
duplicate_ids=0
for x in ordered:
    if x['id'] in by_id:
        duplicate_ids+=1
    else:
        by_id[x['id']]=x
all_index=sorted(by_id.values(),key=lambda x:(x['page'],x['position']))
nonempty_pages=sorted(p for p,v in pages.items() if v)
max_nonempty=max(nonempty_pages) if nonempty_pages else None
print('ODIN_INDEX',json.dumps({'unique_items':len(all_index),'duplicates':duplicate_ids,'nonempty_pages':len(nonempty_pages),'max_nonempty_page':max_nonempty,'profile_count':PROFILE_COUNT},ensure_ascii=False),flush=True)

# Locate the page containing the 2025-01-01 lower boundary with a small number of article-date reads.
page_date_cache={}
page_date_errors={}
def page_first_date(page):
    if page in page_date_cache:
        return page_date_cache[page]
    items=pages.get(page) or []
    if not items:
        page_date_cache[page]=None
        return None
    dated,err=fetch_article_date(items[0])
    if err:
        page_date_errors[page]=err
        page_date_cache[page]=None
        return None
    dt=datetime.fromisoformat(dated['datetime'])
    page_date_cache[page]=dt
    return dt

boundary_page=None
if max_nonempty:
    lo,hi=1,max_nonempty
    while lo<=hi:
        mid=(lo+hi)//2
        dt=page_first_date(mid)
        if dt is None:
            # Fallback conservatively: move toward newer pages if a sample cannot be read.
            hi=mid-1
            continue
        print('BOUNDARY_SAMPLE',mid,dt.isoformat(),flush=True)
        if dt >= START:
            boundary_page=mid
            lo=mid+1
        else:
            hi=mid-1

# Include one page beyond the binary-search boundary for an explicit safety check.
last_candidate_page=min(max_nonempty or 0,(boundary_page or 0)+1)
candidate_index=[]
for p in range(1,last_candidate_page+1):
    candidate_index.extend(pages.get(p,[]))
# Dedupe candidates in case the live feed shifted while pages were being fetched.
candidate_by_id={}
for x in candidate_index:
    candidate_by_id.setdefault(x['id'],x)
candidate_index=list(candidate_by_id.values())
print('DATE_CANDIDATES',len(candidate_index),'through_page',last_candidate_page,flush=True)

article_errors={}
dated_items=[]
with ThreadPoolExecutor(max_workers=ARTICLE_WORKERS) as ex:
    futs={ex.submit(fetch_article_date,x):x['id'] for x in candidate_index}
    done=0
    for fut in as_completed(futs):
        item,err=fut.result()
        if err:
            article_errors[item['id']]=err
        else:
            dated_items.append(item)
        done+=1
        if done%100==0 or done==len(candidate_index):
            print('ARTICLE_DATE_PROGRESS',done,len(candidate_index),'errors',len(article_errors),flush=True)

dated_items.sort(key=lambda x:x['timestamp'],reverse=True)
selected=[x for x in dated_items if START <= datetime.fromisoformat(x['datetime']) <= END]

# Verify page chronology across every page for which all/most dates were fetched.
page_ranges={}
for x in dated_items:
    page_ranges.setdefault(x['page'],[]).append(x['timestamp'])
page_order_violations=[]
prev_oldest=None
for p in sorted(page_ranges):
    vals=page_ranges[p]
    newest=max(vals); oldest=min(vals)
    if prev_oldest is not None and newest > prev_oldest:
        page_order_violations.append({'page':p,'newest':newest,'previous_oldest':prev_oldest})
    prev_oldest=oldest

# Check whether the extra page beyond the boundary contained anything still inside the requested range.
extra_page_in_range=0
if boundary_page and last_candidate_page>boundary_page:
    extra_page_in_range=sum(1 for x in selected if x['page']>boundary_page)

summary={
    'status':'complete' if selected_mode and not odin_errors and not article_errors else ('complete_with_errors' if selected_mode else 'probe_failed'),
    'profile_status':pr.status_code,
    'profile_content_count':PROFILE_COUNT,
    'pv_id':PV_ID,
    'has_suv':bool(suv_value),
    'probe':probe,
    'odin_pages_requested':MAX_PAGE,
    'odin_pages_with_items':len(nonempty_pages),
    'odin_max_nonempty_page':max_nonempty,
    'odin_failed_pages':odin_errors,
    'odin_unique_items':len(all_index),
    'odin_duplicate_ids_seen':duplicate_ids,
    'difference_from_profile_count':len(all_index)-PROFILE_COUNT,
    'boundary_page':boundary_page,
    'boundary_samples':{str(k):v.isoformat() if v else None for k,v in sorted(page_date_cache.items())},
    'boundary_sample_errors':page_date_errors,
    'last_candidate_page':last_candidate_page,
    'article_date_candidates':len(candidate_index),
    'article_date_successes':len(dated_items),
    'article_date_errors_count':len(article_errors),
    'article_date_errors':dict(list(article_errors.items())[:100]),
    'page_order_violation_count':len(page_order_violations),
    'page_order_violations':page_order_violations[:50],
    'extra_page_items_in_requested_range':extra_page_in_range,
    'selected_count':len(selected),
    'selected_newest_datetime':selected[0]['datetime'] if selected else None,
    'selected_oldest_datetime':selected[-1]['datetime'] if selected else None,
    'range_start':START.isoformat(),
    'range_end':END.isoformat(),
}
open('sohu_summary.json','w',encoding='utf-8').write(json.dumps(summary,ensure_ascii=False,indent=2))
open('sohu_all.json','w',encoding='utf-8').write(json.dumps(all_index,ensure_ascii=False,indent=2))
open('sohu_selected.json','w',encoding='utf-8').write(json.dumps(selected,ensure_ascii=False,indent=2))
with open('sohu_selected.csv','w',encoding='utf-8-sig',newline='') as f:
    fields=['date','datetime','title','id','url','page','position']
    w=csv.DictWriter(f,fieldnames=fields)
    w.writeheader()
    for x in selected:
        w.writerow({k:x[k] for k in fields})
print('SUMMARY',json.dumps(summary,ensure_ascii=False),flush=True)
