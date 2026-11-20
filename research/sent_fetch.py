import requests, time, json, os, sys, datetime as dt
C='sent_cache'; os.makedirs(C,exist_ok=True)
H={'User-Agent':'research-script/1.0'}
def get(url,fn,tries=3):
    p=os.path.join(C,fn)
    if os.path.exists(p) and os.path.getsize(p)>20: return
    for t in range(tries):
        time.sleep(8)
        try:
            r=requests.get(url,headers=H,timeout=90)
            if r.status_code==200 and r.text.strip().startswith('{'):
                open(p,'w',encoding='utf-8').write(r.text); return
            print('bad',fn,r.status_code,r.text[:80].replace('\n',' '),flush=True)
        except Exception as e: print('err',fn,e,flush=True)
        time.sleep(90)
# wikipedia
for a in ['Bitcoin','Ethereum','Solana_(blockchain_platform)','Gold','Nvidia','Tesla,_Inc.','Federal_Reserve']:
    get(f'https://wikimedia.org/api/rest_v1/metrics/pageviews/per-article/en.wikipedia/all-access/user/{a}/daily/20160101/20261119',f'wiki_{a.split("(")[0]}.json')
Q={'bitcoin':'bitcoin','ethereum':'ethereum','solana':'solana','gold':'"gold price"','nvidia':'nvidia','tesla':'tesla','fed':'"federal reserve"','stocks':'"stock market"'}
yrs=[(f'{y}0101000000',f'{y}1231235959') for y in range(2018,2026)]+[('20260101000000','20261119235959')]
for k,q in Q.items():
    for mode in ['timelinetone','timelinevolraw']:
        for s,e in yrs:
            u=f'https://api.gdeltproject.org/api/v2/doc/doc?query={requests.utils.quote(q)}&mode={mode}&format=json&startdatetime={s}&enddatetime={e}'
            get(u,f'gdelt_{k}_{mode}_{s[:4]}.json')
print('DONE')
