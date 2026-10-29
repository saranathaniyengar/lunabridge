# SPDX-License-Identifier: GPL-3.0
"""Sweep: closed-form P(L,R) vs offline scheduler (one constant-rate class, unbounded buffer), 20-60 Mb/s, L 1-72 h. Paper Sec. IV quotes the max |difference|."""
import sys, os; sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from analysis.fig_admissibility import load_plan, plan_stats, run_class, closed_form_P
from gateway.traffic import TrafficClass
plan, rows = load_plan(50e6); st=plan_stats(plan, rows)
mx=0
for mbps in [20,30,40,42,44,45,46,48,50,55,60]:
    for Lh in [1,2,5.56,8,24,72]:
        L=Lh*3600
        sim=run_class(plan, TrafficClass.MEDIA, L, st['span'], 300.0, rate_override=mbps*1e6/8)[0]
        cf=closed_form_P(plan, st, L, mbps*1e6); d=abs(cf-sim); mx=max(mx,d)
        if d>0.0025: print(mbps,Lh,round(cf,4),round(sim,4),round(d,4))
print('max',mx)
