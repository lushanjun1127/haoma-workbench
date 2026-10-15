import json, os, tempfile, time, tracemalloc
from pathlib import Path
import customer_number_processor_single as m
report={}
with tempfile.TemporaryDirectory() as directory:
    root=Path(directory); source=root/'million.txt'
    with source.open('w',encoding='ascii') as stream:
        block='12125551234\n'*10000
        for _ in range(100): stream.write(block)
    t=time.perf_counter(); values=m.parse_source_fast(str(source)); report['parse_seconds']=round(time.perf_counter()-t,3)
    assert len(values)==1000000
    t=time.perf_counter(); counts=m.filter_arrays(values,{12125551234},str(root/'kept.txt'),str(root/'removed.txt'))
    report['filter_seconds']=round(time.perf_counter()-t,3); assert counts[:3]==(1000000,0,1000000)
    tracemalloc.start(); t=time.perf_counter(); result=m.DataValidator().check_file_quality(str(source)); _,peak=tracemalloc.get_traced_memory(); tracemalloc.stop()
    report['quality_seconds']=round(time.perf_counter()-t,3); report['quality_python_peak_bytes']=peak
    assert result['statistics']['duplicates']==999999
    report['rows']=1000000; report['unique_numbers']=1
    report['python']=os.sys.version; report['numpy']=m.np.__version__
Path('benchmark-results.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
print(report)
