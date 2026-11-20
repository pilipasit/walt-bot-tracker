import requests, time, os
C='sent_cache'; H={'User-Agent':'research-script/1.0'}
Q=[('bitcoin','bitcoin'),('gold','"gold price"'),('nvidia','nvidia'),('fed','"federal reserve"'),('ethereum','ethereum'),('tesla','tesla'),('solana','solana'),('stocks','"stock market"')]
for k,q in Q:
    for mode in ['timelinetone','timelinevolraw']:
        p=f'{C}/gdeltfull_{k}_{mode}.json'
        if os.path.exists(p): continue
        u=f'https://api.gdeltproject.org/api/v2/doc/doc?query={requests.utils.quote(q)}&mode={mode}&format=json&startdatetime=20180101000000&enddatetime=20261119235959'
        for t in range(6):
            time.sleep(20)
            try:
                r=requests.get(u,headers=H,timeout=120)
                if r.status_code==200 and r.text.strip().startswith('{'):
                    open(p,'w',encoding='utf-8').write(r.text); print('ok',p,len(r.text),flush=True); break
                print('bad',p,r.status_code,r.text[:60].replace('\n',' '),flush=True)
            except Exception as e: print('err',p,e,flush=True)
            time.sleep(45)
print('DONE',flush=True)
