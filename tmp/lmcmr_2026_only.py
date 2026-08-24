import csv,json,re,os,zipfile
SRC='tmp/lmcmr-crawl/lmcmr_all_index.json'
OUT='tmp/lmcmr-2026-export'; os.makedirs(OUT,exist_ok=True)
with open(SRC,encoding='utf-8') as f: rows=json.load(f)
out=[]; seen=set()
for x in rows:
    title=str(x.get('title') or '').strip(); url=str(x.get('url') or '')
    if not re.search(r'2026\s*版',title): continue
    if url in seen: continue
    seen.add(url)
    out.append({'title':title,'url':url,'index_update_date':x.get('date',''),'verification':'LMCMR current index'})
out.sort(key=lambda x:(x['title'],x['url']))
with open(f'{OUT}/lmcmr_2026_titles.csv','w',encoding='utf-8-sig',newline='') as f:
    w=csv.DictWriter(f,fieldnames=['title','url','index_update_date','verification']); w.writeheader(); w.writerows(out)
with open(f'{OUT}/lmcmr_2026_titles.json','w',encoding='utf-8') as f: json.dump(out,f,ensure_ascii=False,indent=2)
summary={'status':'complete','current_index_unique_urls':len(rows),'verified_2026_titles':len(out)}
with open(f'{OUT}/summary.json','w',encoding='utf-8') as f: json.dump(summary,f,ensure_ascii=False,indent=2)
with zipfile.ZipFile(f'{OUT}/lmcmr_2026_titles.zip','w',zipfile.ZIP_DEFLATED) as z:
    for n in ['lmcmr_2026_titles.csv','lmcmr_2026_titles.json','summary.json']: z.write(f'{OUT}/{n}',arcname=n)
print(json.dumps(summary,ensure_ascii=False))
