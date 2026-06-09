import json,urllib.request,re,datetime,math
UA={'User-Agent':'Mozilla/5.0','Accept':'application/json'}
d=json.load(urllib.request.urlopen(urllib.request.Request("https://cdn.cboe.com/api/global/delayed_quotes/options/MSTR.json",headers=UA),timeout=30))
data=d["data"]; S=data["close"]
print("MSTR spot",S,"| fetch",datetime.datetime.utcnow().isoformat(),"UTC (CBOE delayed)")
opts=data["options"]
def parse(o):
    m=re.match(r'^[A-Z]+(\d{6})([CP])(\d{8})$',o["option"]); 
    return (m.group(1),m.group(2),int(m.group(3))/1000.0) if m else (None,None,None)
expdates={"280121":datetime.date(2028,1,21),"281215":datetime.date(2028,12,15),"271217":datetime.date(2027,12,17)}
idx={}
for o in opts:
    e,cp,k=parse(o)
    if cp=="C" and e in expdates: idx[(e,k)]=o
today=datetime.date(2026,6,9)
cands=[("271217",100),("280121",100),("280121",120),("281215",100),("281215",120),("281215",130)]
print(f"\n{'exp':>9} {'K':>5} {'bid':>7} {'ask':>7} {'mid':>7} {'spr%':>5} {'IV':>5} {'delta':>6} {'OI':>6} {'vol':>4} {'lev':>5} {'BE':>6} {'BE%':>5} {'carry/yr':>8} {'$/contract':>10} {'2x payoff':>9}")
for e,K in cands:
    o=idx.get((e,K))
    if not o: print(e,K,"N/A"); continue
    b=o.get('bid',0);a=o.get('ask',0); mid=(b+a)/2 if (b and a) else o.get('last_trade_price',0)
    T=(expdates[e]-today).days/365.0
    intr=max(S-K,0);tv=mid-intr;dlt=o.get('delta',0)
    be=K+mid; lev=S/mid; carry=(tv/S)/T*100
    cost=mid*100
    payoff2x=(max(2*S-K,0)/mid-1)*100
    spr=(a-b)/mid*100 if mid else 0
    print(f"{e:>9} {K:>5.0f} {b:>7.2f} {a:>7.2f} {mid:>7.2f} {spr:>4.1f}% {o.get('iv',0):>5.2f} {dlt:>6.3f} {o.get('open_interest',0):>6.0f} {o.get('volume',0):>4.0f} {lev:>4.2f}x {be:>6.0f} {(be/S-1)*100:>4.0f}% {carry:>7.1f}% {cost:>10,.0f} {payoff2x:>8.0f}%")
