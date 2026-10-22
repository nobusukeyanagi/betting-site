"""Inspect public Minnano Keirin article HTML and browser errors (no article dump)."""
import sys
import subprocess
import shutil
from pathlib import Path
from bs4 import BeautifulSoup
sys.path.insert(0,str(Path(__file__).resolve().parent))
from build import WebClient, FEEDS
feed=next(f for f in FEEDS if f.key=="minnano")
client=WebClient()
for id in ("25833","25484"):
    url="https://bet.minnano-keirin.com/info/"+id
    try:
        page=client.fetch_article(feed,url)
        soup=BeautifulSoup(page,"html.parser")
        heads=[(h.name,h.get_text(" ",strip=True)[:80]) for h in soup.select("h1,h2,h3")][:12]
        types=[(s.get("type"),s.get("id"),len(s.string or "")) for s in soup.find_all("script")][-15:]
        after=soup.find("h2")
        fragments=after.parent.get_text(" ",strip=True)[:300] if after else ""
        print("PROBE",id,"htmlsize",len(page),"title",soup.title.get_text(" ",strip=True)[:70] if soup.title else "",flush=True)
        print("PROBE headings",heads,"scripts",types,flush=True)
        print("PROBE text_after_h2",repr(fragments),"iframe_count",len(soup.find_all("iframe")),flush=True)
        print("PROBE main_nodes",[(x.name,x.get("class"),len(x.get_text(" ",strip=True))) for x in soup.select("main,article,[class*=body],[class*=content]")[:16]],flush=True)
        print("PROBE scripts_detail",[(i, len(x.string or ""), (x.string or "")[:115].replace("\\n"," "), [ (k,(x.string or "").lower().find(k.lower())) for k in ("__next_f","body","content","いつも","メンテナンス","title","25833") if k.lower() in (x.string or "").lower()]) for i,x in enumerate(soup.find_all("script")) if len(x.string or "") > 400][:25],flush=True)
        print("PROBE current_main_html",str(soup.find("main"))[:1200] if soup.find("main") else "",flush=True)
    except Exception as e:
        print("PROBE html_error",id,type(e).__name__,str(e)[:300],flush=True)
binary=next((shutil.which(n) for n in ("google-chrome","google-chrome-stable","chromium","chromium-browser") if shutil.which(n)),None)
print("PROBE browser",binary,flush=True)
if False and binary:
    opts=[binary,"--headless=new","--disable-gpu","--disable-dev-shm-usage","--no-first-run","--virtual-time-budget=4500","--dump-dom","https://bet.minnano-keirin.com/info/25833"]
    try:
        p=subprocess.run(opts,capture_output=True,timeout=16)
        soup=BeautifulSoup(p.stdout[:3000000].decode("utf-8","replace"),"html.parser")
        print("PROBE chrome_exit",p.returncode,"size",len(p.stdout),"stderr",repr(p.stderr.decode("utf-8","replace")[-550:]),flush=True)
        print("PROBE chrome_text",repr(soup.get_text(" ",strip=True)[-500:]),flush=True)
    except subprocess.TimeoutExpired as e:
        print("PROBE chrome_timeout",len(e.stdout or b""),repr((e.stderr or b"")[-550:]),flush=True)
    except Exception as e:
        print("PROBE chrome_exception",type(e).__name__,str(e)[:500],flush=True)
