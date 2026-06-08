import json,urllib.request,re
UA={'User-Agent':'Mozilla/5.0','Accept':'application/json'}
d=json.load(urllib.request.urlopen(urllib.request.Request("https://cdn.cboe.com/api/global/delayed_quotes/options/MSTR.json",headers=UA),timeout=30))
data=d["data"]; spot=data["close"]; print("SPOT",spot)
opts=data["options"]
def parse(o):
    m=re.match(r'^[A-Z]+(\d{6})([CP])(\d{8})$',o["option"]); 
    return (m.group(1),m.group(2),int(m.group(3))/1000.0) if m else (None,None,None)
print("fields sample:",list(opts[0].keys()))
for exp in ["271217","280121","261218"]:
    calls=[]
    for o in opts:
        e,cp,k=parse(o)
        if e==exp and cp=="C": calls.append((k,o))
    calls.sort()
    print(f"\n=== EXP {exp}  (calls near ATM, spot {spot}) ===")
    print(f"{'strike':>8} {'bid':>7} {'ask':>7} {'last':>7} {'mid':>7} {'IV':>6} {'delta':>6} {'theta':>7} {'vega':>6} {'OI':>6}")
    for k,o in calls:
        if k<spot*0.7 or k>spot*1.5: continue
        bid=o.get('bid',0);ask=o.get('ask',0);mid=(bid+ask)/2 if (bid and ask) else o.get('last_trade_price',0)
        print(f"{k:>8.0f} {bid:>7.2f} {ask:>7.2f} {o.get('last_trade_price',0):>7.2f} {mid:>7.2f} {o.get('iv',0):>6.2f} {o.get('delta',0):>6.3f} {o.get('theta',0):>7.3f} {o.get('vega',0):>6.3f} {o.get('open_interest',0):>6}")
