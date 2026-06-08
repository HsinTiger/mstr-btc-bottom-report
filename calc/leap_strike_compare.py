S0=127.5
# (strike, premium mid, intrinsic, delta, IV) from CBOE 271217
opts=[("深價內 ITM $100",100,65.60,0.801,0.91),
      ("價平 ATM $125",125,56.03,0.733,0.88),
      ("價外 OTM $185",185,40.20,0.601,0.86)]
print(f"{'option':>18} {'prem':>6} {'TV%':>6} {'lever(S/P)':>10} {'elastic':>8} {'breakeven':>11} {'@flat':>7} {'@+50%':>7} {'@2x':>7} {'@3x':>7}")
for nm,K,P,d,iv in opts:
    intr=max(S0-K,0); tv=P-intr; be=K+P
    lev=S0/P; el=d*S0/P
    def ret(mult):
        Sx=S0*mult; return (max(Sx-K,0)/P-1)*100
    print(f"{nm:>18} ${P:>5.1f} {tv/P*100:>5.0f}% {lev:>9.2f}x {el:>7.2f}x ${be:>6.0f}(+{(be/S0-1)*100:.0f}%) {ret(1.0):>6.0f}% {ret(1.5):>6.0f}% {ret(2.0):>6.0f}% {ret(3.0):>6.0f}%")
print("\nKey: for a *double* thesis, deep-ITM has lower breakeven + less time-value bleed + similar 2x payoff.")
print("OTM only wins on 3x+ moonshots; ATM is the worst of both for a 'just double' view (95% time value).")
