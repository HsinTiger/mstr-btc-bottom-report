import json,urllib.request,re,datetime,time
UA={'User-Agent':'Mozilla/5.0','Accept':'application/json'}
url="https://cdn.cboe.com/api/global/delayed_quotes/options/MSTR.json"
d=json.load(urllib.request.urlopen(urllib.request.Request(url,headers=UA),timeout=30))
data=d["data"]; S=data["close"]
# freshness
print("server fetch (UTC):",datetime.datetime.utcnow().isoformat())
print("CBOE underlying close/last:",S, " (CBOE = 15-min delayed / EOD, NOT live tick)")
opts=data["options"]
def parse(o):
    m=re.match(r'^[A-Z]+(\d{6})([CP])(\d{8})$',o["option"]); 
    return (m.group(1),m.group(2),int(m.group(3))/1000.0) if m else (None,None,None)
for exp,lbl in [("271217","Dec-2027"),("280121","Jan-2028")]:
    by={}
    for o in opts:
        e,cp,k=parse(o)
        if e==exp and cp=="C": by[k]=o
    print(f"\n=== {lbl} ({exp}) calls — liquidity / spread ===")
    print(f"{'strike':>6} {'bid':>7} {'ask':>7} {'mid':>7} {'spread$':>8} {'spread%':>8} {'OI':>7} {'vol':>5} {'delta':>6}")
    for k in sorted(by):
        if k<45 or k>135: continue
        o=by[k]; b=o.get('bid',0);a=o.get('ask',0)
        if not(b and a): continue
        mid=(b+a)/2; sp=a-b; spp=sp/mid*100
        print(f"{k:>6.0f} {b:>7.2f} {a:>7.2f} {mid:>7.2f} {sp:>8.2f} {spp:>7.1f}% {o.get('open_interest',0):>7.0f} {o.get('volume',0):>5.0f} {o.get('delta',0):>6.3f}")
