import base64,csv,hashlib,json,os,shutil,zipfile
ROOT='tmp/lmcmr-title-export'
CHUNK_DIR=f'{ROOT}/zip_chunks'
with open(f'{ROOT}/summary.json',encoding='utf-8') as f: summary=json.load(f)
with open(f'{ROOT}/reconstruction_validation.json',encoding='utf-8') as f: validation=json.load(f)
with open(f'{ROOT}/lmcmr_2025_titles_verified.json',encoding='utf-8') as f: v25=json.load(f)
with open(f'{ROOT}/lmcmr_2025_titles_reconstructed_unverified.json',encoding='utf-8') as f: r25=json.load(f)
with open(f'{ROOT}/lmcmr_2026_titles.json',encoding='utf-8') as f: v26=json.load(f)
combined=list(v25)+list(r25)
combined.sort(key=lambda x:(x.get('confidence',''),x.get('title',''),x.get('url','')))
fields=['version','title','url','source','source_date','confidence']
with open(f'{ROOT}/lmcmr_2025_all_candidates.csv','w',encoding='utf-8-sig',newline='') as f:
    w=csv.DictWriter(f,fieldnames=fields); w.writeheader(); w.writerows(combined)
with open(f'{ROOT}/lmcmr_2025_all_candidates.json','w',encoding='utf-8') as f: json.dump(combined,f,ensure_ascii=False,indent=2)
readme=f'''LMCMR report title package — 2025 / 2026\n\n2026 verified titles: {len(v26)}\n2025 archived verified titles: {len(v25)}\n2025 reconstructed, unverified candidates: {len(r25)}\n2025 combined candidate rows: {len(combined)}\n\nReconstruction validation:\n- Archived 2025 samples with matching current 2026 URL: {validation['samples_with_current_2026']}\n- Exact year-substitution matches: {validation['exact_year_substitution_matches']}\n- Mismatches: {validation['mismatches']}\n- Exact-match rate: {validation['exact_match_rate']:.2%}\n\nImportant:\n1. lmcmr_2026_titles.* is verified from the current LMCMR full-site index.\n2. lmcmr_2025_titles_verified.* contains only titles recovered from archived page snapshots.\n3. lmcmr_2025_titles_reconstructed_unverified.* is derived by changing 2026版 to 2025版 for URLs without archived proof. It is NOT a verified historical set.\n4. lmcmr_2025_all_candidates.* combines verified and reconstructed rows while preserving the confidence field.\n5. Paid full PDF/Word reports are not included. LMCMR states they are delivered after contract and payment.\n'''
with open(f'{ROOT}/README_FINAL.txt','w',encoding='utf-8') as f: f.write(readme)
manifest={'summary':summary,'validation':validation,'counts':{'2026_verified':len(v26),'2025_verified':len(v25),'2025_reconstructed_unverified':len(r25),'2025_combined_candidates':len(combined)}}
with open(f'{ROOT}/final_manifest.json','w',encoding='utf-8') as f: json.dump(manifest,f,ensure_ascii=False,indent=2)
files=['README_FINAL.txt','final_manifest.json','reconstruction_validation.json','lmcmr_2026_titles.csv','lmcmr_2026_titles.json','lmcmr_2025_titles_verified.csv','lmcmr_2025_titles_verified.json','lmcmr_2025_titles_reconstructed_unverified.csv','lmcmr_2025_titles_reconstructed_unverified.json','lmcmr_2025_all_candidates.csv','lmcmr_2025_all_candidates.json']
zip_path=f'{ROOT}/LMCMR_2025_2026_report_titles.zip'
with zipfile.ZipFile(zip_path,'w',zipfile.ZIP_DEFLATED) as z:
    for name in files: z.write(f'{ROOT}/{name}',arcname=name)
raw=open(zip_path,'rb').read(); sha=hashlib.sha256(raw).hexdigest(); b64=base64.b64encode(raw).decode('ascii')
if os.path.exists(CHUNK_DIR): shutil.rmtree(CHUNK_DIR)
os.makedirs(CHUNK_DIR)
chunk_size=60000
chunks=[]
for i in range(0,len(b64),chunk_size):
    name=f'{i//chunk_size:04d}.txt'; data=b64[i:i+chunk_size]
    open(f'{CHUNK_DIR}/{name}','w',encoding='ascii').write(data)
    chunks.append(name)
chunk_manifest={'filename':'LMCMR_2025_2026_report_titles.zip','zip_bytes':len(raw),'sha256':sha,'base64_chars':len(b64),'chunk_size_chars':chunk_size,'chunk_count':len(chunks),'chunks':chunks}
with open(f'{CHUNK_DIR}/manifest.json','w',encoding='utf-8') as f: json.dump(chunk_manifest,f,ensure_ascii=False,indent=2)
print(json.dumps({'status':'complete','zip':zip_path,'zip_manifest':chunk_manifest,'counts':manifest['counts']},ensure_ascii=False))
