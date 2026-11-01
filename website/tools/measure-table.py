import json,sys,glob
# Usage: python tools/measure-table.py <prefix> ; prints one compact row per run from tmp/measure/<prefix>-*.json
for f in sorted(glob.glob(f'tmp/measure/{sys.argv[1]}-*.json')):
    d=json.load(open(f)); t=d['trace']; fr=d['frames']; lt=d['longTasks']
    print(f"{d['label']:<22} fps {fr['fps']:>5} >33/50/100 {fr['over33']}/{fr['over50']}/{fr['over100']} max {fr['max']} LT {lt['n']}/{lt['totalMs']}ms CLS {d['cls']} logo {d['logo']['visibleShare']} ULT {t.get('UpdateLayoutTree')} Paint {t.get('Paint')} Raster {t.get('RasterTask')} Fn {t.get('FunctionCall')} forced {t.get('forcedLayouts')} LCP {d['lcp']['t'] if d['lcp'] else None}")
