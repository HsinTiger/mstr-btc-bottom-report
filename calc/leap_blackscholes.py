import math
def N(x): return 0.5*(1+math.erf(x/math.sqrt(2)))
def bs_call(S,K,T,r,sig):
    if T<=0: return max(S-K,0)
    d1=(math.log(S/K)+(r+sig*sig/2)*T)/(sig*math.sqrt(T)); d2=d1-sig*math.sqrt(T)
    return S*N(d1)-K*math.exp(-r*T)*N(d2)
def delta_call(S,K,T,r,sig):
    if T<=0: return 1.0 if S>K else 0.0
    d1=(math.log(S/K)+(r+sig*sig/2)*T)/(sig*math.sqrt(T)); return N(d1)

# ===== Real anchors (CBOE, 2026-06-09) =====
S0=127.50; K=125.0; T=1.52; r=0.043; IV=0.88
prem=bs_call(S0,K,T,r,IV)
print(f"=== ATM Dec-2027 $125 call: model premium ${prem:.2f} (CBOE mid $56.03) ===")
prem=56.03  # use real market mid
intrinsic=max(S0-K,0); tv=prem-intrinsic
d0=delta_call(S0,K,T,r,IV)
print(f"spot ${S0}  premium ${prem}  intrinsic ${intrinsic:.2f}  time-value ${tv:.2f} ({tv/prem*100:.1f}% of premium)")
print(f"delta {d0:.3f}")
print(f"notional leverage (spot/prem) = {S0/prem:.2f}x")
print(f"elasticity (delta*S/prem)     = {d0*S0/prem:.2f}x")
be=K+prem; print(f"breakeven at expiry = ${be:.2f} ({(be/S0-1)*100:+.0f}% from spot)")

print("\n=== STRATEGY 1: 100% in ATM LEAP — payoff AT EXPIRY (Dec 2027) ===")
print(f"{'MSTR@exp':>10} {'%move':>7} | {'100% spot':>10} | {'100% LEAP call':>15} | {'2x-ETF approx*':>14}")
for mult in [0.4,0.6,0.8,1.0,1.2,1.42,1.6,2.0,2.5,3.0,4.0]:
    Sx=S0*mult; mv=(mult-1)*100
    spot_ret=(mult-1)*100
    call_val=max(Sx-K,0); call_ret=(call_val/prem-1)*100
    # crude 2x daily ETF: approximate with vol drag over ~1.5y; path-agnostic terminal approx = (mult^2)*exp(-sig^2*T) style is messy; show naive 2x return for ref
    etf_ret=2*spot_ret
    print(f"${Sx:>8.0f} {mv:>6.0f}% | {spot_ret:>9.0f}% | {call_ret:>14.0f}% | {etf_ret:>13.0f}%")
print("*2x-ETF column is NAIVE 2x (no decay) for reference only; real MSTU suffers heavy vol drag — see agent research.")

print("\n=== Leverage realized on different up-moves (held to expiry) ===")
for mult in [1.2,1.5,2.0,2.5,3.0]:
    Sx=S0*mult; spot_ret=(mult-1)*100; call_ret=(max(Sx-K,0)/prem-1)*100
    print(f"  MSTR {mult:.1f}x: spot {spot_ret:+.0f}% -> call {call_ret:+.0f}%  (realized leverage {call_ret/spot_ret:.2f}x)")

print("\n=== TIME-VALUE / THETA DECAY PATH (if MSTR stays FLAT at $127.5) ===")
print(f"{'months left':>11} {'T(yr)':>6} {'call value':>11} {'TV left':>9} {'cum decay vs $56':>16}")
for m in [18,15,12,9,6,3,1,0]:
    Tt=m/12.0; cv=bs_call(S0,K,Tt,r,IV); tvv=cv-max(S0-K,0)
    print(f"{m:>11} {Tt:>6.2f} ${cv:>9.2f} ${tvv:>7.2f} {(cv/prem-1)*100:>14.0f}%")
