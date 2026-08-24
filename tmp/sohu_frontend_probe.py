import json, re, requests
from urllib.parse import urljoin

PROFILE='https://mp.sohu.com/profile?xpt=RDM4QjdFRURBRDdBRERCMTgxRjFFRjVFRkNBMTg5NDFAcXEuc29odS5jb20='
s=requests.Session()
s.headers.update({'User-Agent':'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/124 Safari/537.36','Accept-Language':'zh-CN,zh;q=0.9'})
r=s.get(PROFILE,timeout=60); r.raise_for_status()
html=r.text
scripts=re.findall(r'<script[^>]+src=["\']([^"\']+Clientindex[^"\']+\.js)["\']',html,re.I)
if not scripts:
    scripts=re.findall(r'<script[^>]+src=["\']([^"\']+\.js)["\']',html,re.I)
urls=[]
for src in scripts:
    if src.startswith('//'):
        src='https:'+src
    else:
        src=urljoin(PROFILE,src)
    if src not in urls:
        urls.append(src)

needles=['authorColumnId','FeedSlideloadAuthor','blockdata','operateFeedMode','XTOPIC_SYNTHETICAL','author-page-api','columnId','authorColumn','InfiniteLoadComp','FeedSlideload']
out={'profile_status':r.status_code,'profile_bytes':len(html),'candidate_urls':urls[:20],'bundles':[]}
for url in urls[:10]:
    try:
        rr=s.get(url,headers={'Referer':PROFILE},timeout=60)
        text=rr.text
        bundle={'url':url,'status':rr.status_code,'bytes':len(text),'prefix':text[:300],'matches':{}}
        for needle in needles:
            entries=[]
            for m in re.finditer(re.escape(needle),text,re.I):
                a=max(0,m.start()-1800); b=min(len(text),m.end()+2800)
                entries.append(text[a:b])
                if len(entries)>=12: break
            bundle['matches'][needle]=entries
        out['bundles'].append(bundle)
    except Exception as e:
        out['bundles'].append({'url':url,'error':repr(e)})
open('sohu_frontend_probe.json','w',encoding='utf-8').write(json.dumps(out,ensure_ascii=False,indent=2))
print(json.dumps([{'url':b.get('url'),'status':b.get('status'),'bytes':b.get('bytes'),'match_counts':{k:len(v) for k,v in b.get('matches',{}).items()}} for b in out['bundles']],ensure_ascii=False))
