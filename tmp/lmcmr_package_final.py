import csv,json,os,zipfile
ROOT='tmp/lmcmr-title-export'
with open(f'{ROOT}/summary.json',encoding='utf-8') as f: summary=json.load(f)
with open(f'{ROOT}/reconstruction_validation.json',encoding='utf-8') as f: validation=json.load(f)
with open(f'{ROOT}/lmcmr_2025_titles_verified.json',encoding='utf-8') as f: v25=json.load(f)
with open(f'{ROOT}/lmcmr_2025_titles_reconstructed_unverified.json',encoding='utf-8') as f: r25=json.load(f)
with open(f'{ROOT}/lmcmr_2026_titles.json',encoding='utf-8') as f: v26=json.load(f)
combined=[]
for x in v25: combined.append(x)
for x in r25: combined.append(x)
combined.sort(key=lambda x:(x.get('confidence',''),x.get('title',''),x.get('url','')))
fields=['version','title','url','source','source_date','confidence']
with open(f'{ROOT}/lmcmr_2025_all_candidates.csv','w',encoding='utf-8-sig',newline='') as f:
    w=csv.DictWriter(f,fieldnames=fields); w.writeheader(); w.writerows(combined)
with open(f'{ROOT}/lmcmr_2025_all_candidates.json','w',encoding='utf-8') as f: json.dump(combined,f,ensure_ascii=False,indent=2)
readme=f'''LMCMR report title package — 2025 / 2026\n\n2026 verified titles: {len(v26)}\n2025 archived verified titles: {len(v25)}\n2025 reconstructed, unverified candidates: {len(r25)}\n2025 combined candidate rows: {len(combined)}\n\nReconstruction validation:\n- Archived 2025 samples with matching current 2026 URL: {validation['samples_with_current_2026']}\n- Exact year-substitution matches: {validation['exact_year_substitution_matches']}\n- Mismatches: {validation['mismatches']}\n- Exact-match rate: {validation['exact_match_rate']:.2%}\n\nImportant:\n1. lmcmr_2026_titles.* is verified from the current LMCMR full-site index.\n2. lmcmr_2025_titles_verified.* contains only titles recovered from archived page snapshots.\n3. lmcmr_2025_titles_reconstructed_unverified.* is derived by changing 2026版 to 2025版 for URLs without archived proof. It is NOT a verified historical set.\n4. lmcmr_2025_all_candidates.* combines verified and reconstructed rows while preserving the confidence field.\n5. Paid full PDF/Word reports are not included. LMCMR states they are delivered after contract and payment.\n'''
with open(f'{ROOT}/README_FINAL.txt','w',encoding='utf-8') as f: f.write(readme)
with open(f'{ROOT}/final_manifest.json','w',encoding='utf-8') as f:
    json.dump({'summary':summary,'validation':validation,'counts':{'2026_verified':len(v26),'2025_verified':len(v25),'2025_reconstructed_unverified':len(r25),'2025_combined_candidates':len(combined)}},f,ensure_ascii=False,indent=2)
files=['README_FINAL.txt','final_manifest.json','reconstruction_validation.json','lmcmr_2026_titles.csv','lmcmr_2026_titles.json','lmcmr_2025_titles_verified.csv','lmcmr_2025_titles_verified.json','lmcmr_2025_titles_reconstructed_unverified.csv','lmcmr_2025_titles_reconstructed_unverified.json','lmcmr_2025_all_candidates.csv','lmcmr_2025_all_candidates.json']
with zipfile.ZipFile(f'{ROOT}/LMCMR_2025_2026_report_titles.zip','w',zipfile.ZIP_DEFLATED) as z:
    for name in files: z.write(f'{ROOT}/{name}',arcname=name)
print(json.dumps({'status':'complete','zip':f'{ROOT}/LMCMR_2025_2026_report_titles.zip','counts':{'2026_verified':len(v26),'2025_verified':len(v25),'2025_reconstructed_unverified':len(r25),'2025_combined_candidates':len(combined)}},ensure_ascii=False))
