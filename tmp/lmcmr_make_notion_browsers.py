import html, json, os

ROOT='tmp/lmcmr-title-export'

def load(name):
    with open(os.path.join(ROOT,name),encoding='utf-8') as f:
        return json.load(f)

def render(rows,title,subtitle,outfile):
    trs=[]
    for i,r in enumerate(rows,1):
        t=html.escape(str(r.get('title','')))
        u=html.escape(str(r.get('url','')),quote=True)
        v=html.escape(str(r.get('version','')))
        c=html.escape(str(r.get('confidence','')))
        s=html.escape(str(r.get('source','')))
        d=html.escape(str(r.get('source_date','')))
        trs.append(f'<tr data-text="{html.escape((t+" "+v+" "+c+" "+s+" "+d).lower(),quote=True)}"><td>{i}</td><td class="title">{t}</td><td>{v}</td><td>{c}</td><td>{d}</td><td><a href="{u}" target="_blank" rel="noopener">来源</a></td></tr>')
    body=''.join(trs)
    doc=f'''<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>{html.escape(title)}</title><style>
    body{{font-family:system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;margin:0;background:#fff;color:#222}}.wrap{{padding:16px}}h1{{font-size:20px;margin:0 0 6px}}p{{margin:0 0 12px;color:#666;font-size:13px}}input{{width:100%;box-sizing:border-box;padding:10px 12px;border:1px solid #ddd;border-radius:8px;margin:0 0 10px;font-size:14px}}.meta{{font-size:12px;color:#777;margin:0 0 10px}}table{{width:100%;border-collapse:collapse;font-size:13px}}th,td{{text-align:left;padding:8px;border-bottom:1px solid #eee;vertical-align:top}}th{{position:sticky;top:0;background:#fafafa;z-index:2}}td.title{{min-width:360px}}a{{color:#2160c4;text-decoration:none}}tr.hide{{display:none}}@media(max-width:700px){{th:nth-child(4),td:nth-child(4),th:nth-child(5),td:nth-child(5){{display:none}}td.title{{min-width:220px}}}}
    </style></head><body><div class="wrap"><h1>{html.escape(title)}</h1><p>{html.escape(subtitle)}</p><input id="q" placeholder="搜索标题 / 版本 / 验证状态…"><div class="meta">总计 <span id="count">{len(rows)}</span> 条</div><table><thead><tr><th>#</th><th>报告标题</th><th>版本</th><th>验证状态</th><th>来源日期</th><th>链接</th></tr></thead><tbody>{body}</tbody></table></div><script>
    const q=document.getElementById('q'), rows=[...document.querySelectorAll('tbody tr')], count=document.getElementById('count');q.addEventListener('input',()=>{{const v=q.value.trim().toLowerCase();let n=0;for(const r of rows){{const ok=!v||r.dataset.text.includes(v);r.classList.toggle('hide',!ok);if(ok)n++}}count.textContent=n}});
    </script></body></html>'''
    with open(os.path.join(ROOT,outfile),'w',encoding='utf-8') as f:f.write(doc)

v26=load('lmcmr_2026_titles.json')
v25=load('lmcmr_2025_titles_verified.json')
r25=load('lmcmr_2025_titles_reconstructed_unverified.json')
render(v26,'LMCMR 2026 版报告标题','9,373 条，来自当前 LMCMR 全站索引，已验证。','lmcmr_2026_browser.html')
combined=v25+r25
render(combined,'LMCMR 2025 版报告标题','绿色/历史快照为已验证；重建项明确标记为未验证候选。','lmcmr_2025_browser.html')
print('generated',len(v26),len(combined))
