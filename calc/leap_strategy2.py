import math
def N(x): return 0.5*(1+math.erf(x/math.sqrt(2)))
def bs_call(S,K,T,r,sig):
    if T<=0: return max(S-K,0)
    d1=(math.log(S/K)+(r+sig*sig/2)*T)/(sig*math.sqrt(T)); d2=d1-sig*math.sqrt(T)
    return S*N(d1)-K*math.exp(-r*T)*N(d2)
S0=127.5;K=125;T=1.52;r=0.043;IV=0.88;prem=56.03
B0=15.90
CAP=100000

# ---- Strategy 2: 50% LEAP + 25% MSTR spot + 25% BMNR spot ----
call_cash=0.50*CAP; ncontracts=int(call_cash//(prem*100)); call_spent=ncontracts*prem*100
mstr_cash=0.25*CAP; mstr_sh=mstr_cash/S0
bmnr_cash=0.25*CAP; bmnr_sh=bmnr_cash/B0
tv_per=prem-max(S0-K,0); tv_risk=ncontracts*100*tv_per
print("=== STRATEGY 2 build ($100k) ===")
print(f"LEAP: {ncontracts} contracts (=${call_spent:,.0f}, controls {ncontracts*100} sh notional ${ncontracts*100*S0:,.0f})")
print(f"MSTR spot: {mstr_sh:.0f} sh =${mstr_cash:,.0f}   BMNR spot: {bmnr_sh:.0f} sh =${bmnr_cash:,.0f}")
print(f">>> TIME VALUE AT RISK in call leg = ${tv_risk:,.0f} = {tv_risk/CAP*100:.1f}% of total portfolio")
print(f">>> (i.e. ~{tv_risk/CAP*100:.0f}% of your money evaporates over 18mo IF MSTR ends flat/below $125)")

print("\n=== Terminal value at expiry — scenario grid ===")
print(f"{'scenario':>10} {'MSTRx':>6} {'BMNRx':>6} | {'S2 blended':>11} {'ret':>6} | {'100%LEAP':>9} | {'100%MSTRspot':>12} | {'55/45 spot':>10}")
scen=[("深熊",0.5,0.4),("熊",0.8,0.7),("平",1.0,1.0),("小漲",1.3,1.35),("漲",1.5,1.6),("翻倍",2.0,2.2),("登月",3.0,3.5)]
for nm,mx,bx in scen:
    Sx=S0*mx; Bx=B0*bx
    # strat2
    cval=ncontracts*100*max(Sx-K,0); mval=mstr_sh*Sx; bval=bmnr_sh*Bx
    s2=cval+mval+bval; s2r=(s2/CAP-1)*100
    # 100% leap
    nA=int(CAP//(prem*100)); leaponly=nA*100*max(Sx-K,0); lr=(leaponly/(nA*prem*100)-1)*100
    # 100% mstr spot
    spotonly=(CAP/S0)*Sx; sr=(mx-1)*100
    # 55/45 spot
    blend=0.55*CAP*mx+0.45*CAP*bx; br=(blend/CAP-1)*100
    print(f"{nm:>9} {mx:>6.1f} {bx:>6.1f} | ${s2:>9,.0f} {s2r:>5.0f}% | {lr:>8.0f}% | {sr:>11.0f}% | {br:>9.0f}%")

print("\n=== If the DOUBLE happens EARLY (MSTR->$255) and you SELL the call (capture residual time value) ===")
for m,ivx in [(18,0.88),(12,0.75),(9,0.70),(6,0.65),(3,0.60),(0,0.88)]:
    Tt=m/12.0; cv=bs_call(255,K,Tt,r,ivx)
    ret=(cv/prem-1)*100
    tag="(at expiry, intrinsic only)" if m==0 else f"(IV assumed {ivx:.0%})"
    print(f"  months-to-expiry {m:>2}: call worth ${cv:>6.2f}  ret {ret:>+5.0f}%  {tag}")
print("=> selling into an EARLY double captures leftover time value -> bigger % than waiting to expiry.")
