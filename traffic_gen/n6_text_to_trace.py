import re,sys
sys.path.insert(0,'/tmp/lunabridge')
from gateway.priority_classifier import classify_packet
from gateway.telemetry import BundleRecord, TelemetryWriter
txt,outdir=sys.argv[1],sys.argv[2]
lines=open(txt).read().split('\n')
w=TelemetryWriter(outdir, config={'source':'real srsRAN N6 (ogstun) long capture','site':'nuwins jrtc-apps'})
seq=0;t0=None;per={}
for i,l in enumerate(lines):
    m=re.match(r'^\s*(\d+\.\d+) IP .*?tos (0x[0-9a-f]+).*?length (\d+)', l)
    if not m: continue
    ts=float(m.group(1));tos=int(m.group(2),16);length=int(m.group(3))
    nxt=lines[i+1] if i+1<len(lines) else ''
    sm=re.search(r'(\d+\.\d+\.\d+\.\d+) > (\d+\.\d+\.\d+\.\d+): (\w+)', l+" "+nxt)
    src,dst,proto=sm.groups() if sm else ('?','?','?')
    tc=classify_packet((tos>>2)&0x3f)
    if t0 is None:t0=ts
    seq+=1
    r=BundleRecord(str(seq),f"{src}-{dst}-{proto}",tc,length,ingress_ts=ts-t0);r.set_ttl();w.write_bundle(r)
    per[tc.value]=per.get(tc.value,0)+1
print(f"wrote {seq} REAL bundles -> {outdir}/bundles.jsonl ; per-class {per}")
