import json,re,os
CUR='tmp/lmcmr-title-export/lmcmr_2026_titles.json'
OLD='tmp/lmcmr-title-export/lmcmr_2025_titles_verified.json'
OUT='tmp/lmcmr-title-export/reconstruction_validation.json'
def normurl(u): return u.replace('http://','https://').replace('https://www.','https://')
with open(CUR,encoding='utf-8') as f: cur=json.load(f)
with open(OLD,encoding='utf-8') as f: old=json.load(f)
by={normurl(x['url']):x for x in cur}
matched=[]; mismatched=[]; no_current=[]
for x in old:
    c=by.get(normurl(x['url']))
    if not c:
        no_current.append(x); continue
    pred=re.sub(r'2026\s*版','2025版',c['title'])
    rec={'url':x['url'],'archived_2025':x['title'],'predicted_2025':pred,'current_2026':c['title']}
    if pred==x['title']: matched.append(rec)
    else: mismatched.append(rec)
summary={'archived_2025_samples':len(old),'samples_with_current_2026':len(matched)+len(mismatched),'exact_year_substitution_matches':len(matched),'mismatches':len(mismatched),'no_current_2026_match':len(no_current),'exact_match_rate':(len(matched)/(len(matched)+len(mismatched)) if matched or mismatched else None),'mismatch_examples':mismatched[:20]}
os.makedirs(os.path.dirname(OUT),exist_ok=True)
with open(OUT,'w',encoding='utf-8') as f: json.dump(summary,f,ensure_ascii=False,indent=2)
print(json.dumps(summary,ensure_ascii=False))
