import json, re, requests
URL='https://g1.itc.cn/dpfe-prreeg-prod/202608201635/Clientindex-eae4b1af4f.js'
text=requests.get(URL,timeout=60).text
needles=['authorColumnId','FeedSlideloadAuthor','blockdata','operateFeedMode','XTOPIC_SYNTHETICAL','author-page-api','columnId','authorColumn','InfiniteLoadComp']
out={'url':URL,'bytes':len(text),'matches':{}}
for needle in needles:
    entries=[]
    for m in re.finditer(re.escape(needle),text,re.I):
        a=max(0,m.start()-1800); b=min(len(text),m.end()+2800)
        entries.append(text[a:b])
        if len(entries)>=12: break
    out['matches'][needle]=entries
open('sohu_frontend_probe.json','w',encoding='utf-8').write(json.dumps(out,ensure_ascii=False,indent=2))
print(json.dumps({k:len(v) for k,v in out['matches'].items()},ensure_ascii=False))
