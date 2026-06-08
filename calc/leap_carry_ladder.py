import json,urllib.request,re,math,datetime
UA={'User-Agent':'Mozilla/5.0','Accept':'application/json'}
d=json.load(urllib.request.urlopen(urllib.request.Request("https://cdn.cboe.com/api/global/delayed_quotes/options/MSTR.json",headers=UA),timeout=30))
data=d["data"]; S=data["close"]
print("FETCH",datetime.datetime.utcnow().isoformat(),"UTC   MSTR spot =",S)
opts=data["options"]
def parse(o):
    m=re.match(r'^[A-Z]+(\d{6})([CP])(\d{8})$',o["option"]); 
    return (m.group(1),m.group(2),int(m.group(3))/1000.0) if m else (None,None,None)
exp="271217"; T=( datetime.date(2027,12,17)-datetime.date(2026,6,9) ).days/365.0
print(f"exp {exp}  T={T:.3f}yr\n")
calls={}
for o in opts:
    e,cp,k=parse(o)
    if e==exp and cp=="C": calls[k]=o
print(f"{'strike':>6} {'prem(mid)':>9} {'intrins':>8} {'timeval':>8} {'TV%':>5} {'delta':>6} {'notlev':>6} {'elast':>6} {'BE':>6} {'BE%':>5} {'flat@exp':>9} {'carry%/yr':>9}")
for k in sorted(calls):
    if k<45 or k>135: continue
    o=calls[k]; bid=o.get('bid',0);ask=o.get('ask',0)
    mid=(bid+ask)/2 if (bid and ask) else o.get('last_trade_price',0)
    if mid<=0: continue
    intr=max(S-k,0); tv=mid-intr; dlt=o.get('delta',0)
    notlev=S/mid; elast=dlt*S/mid; be=k+mid
    flat=(max(S-k,0)/mid-1)*100      # if stock flat at expiry
    carry=(tv/S)/T*100               # annualized drag on exposure if flat
    print(f"{k:>6.0f} {mid:>9.2f} {intr:>8.1f} {tv:>8.1f} {tv/mid*100:>4.0f}% {dlt:>6.3f} {notlev:>5.2f}x {elast:>5.2f}x {be:>6.0f} {(be/S-1)*100:>4.0f}% {flat:>8.0f}% {carry:>8.1f}%")
print("\nReference: MSTU/MSTX 2x-ETF carry if flat ~ -79%/yr (vol drag). Margin loan ~ 6-7%/yr.")
