#!/usr/bin/env python3
import argparse
import json
from pathlib import Path
import tempfile
import time

ROOT=Path(__file__).resolve().parent
BASE=ROOT.parents[1]

def save(path,value):
    path.write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n')

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('action',choices=['analyze','reference','plan','verify','rtl'])
    parser.add_argument('--competition',type=Path,default=BASE/'competition')
    args=parser.parse_args()
    work=ROOT/'work';work.mkdir(exist_ok=True)
    case=Path(tempfile.mkdtemp(prefix=args.action+'.',dir=work))
    print('RESULT='+str(case),flush=True)
    summary={'status':'RUNNING','action':args.action,'started_at':time.strftime('%Y-%m-%dT%H:%M:%S%z')}
    try:
        if args.action=='analyze':
            from swin.model import Model
            data=Model(args.competition).analysis()
            save(case/'analysis.json',data)
            summary.update(status='PASS',model=data['model'],model_sha256=data['sha256'],
                           nodes=data['nodes'],MAC_nodes=data['MAC_nodes'],MACs=data['MACs'],
                           all_MACs_mapped=data['all_MACs_mapped'])
        elif args.action=='reference':
            from swin.reference import run
            summary.update(run(args.competition,case))
        elif args.action=='plan':
            from swin.model import Model
            from swin.plan import build
            plan=build(Model(args.competition),case)
            from swin.compile import emit
            compiled=emit(plan,case)
            summary.update(status='PASS',model_sha256=plan['model_sha256'],
                           all_nodes_scheduled=plan['all_nodes_scheduled'],coverage=plan['coverage'],
                           memory=plan['memory'],pe_schedule=plan['pe_schedule'],compiled=compiled)
        elif args.action=='rtl':
            from swin.rtl import run
            summary.update(run(args.competition,case))
        elif args.action=='verify':
            from swin.verify import run
            summary.update(run(args.competition,case))
        else:
            raise NotImplementedError('This stage is still being implemented: '+args.action)
        if summary['status']!='PASS':raise RuntimeError('Stage did not pass; inspect summary')
        print('SWIN_'+args.action.upper()+'_PASS',flush=True)
    except BaseException as error:
        if (case/'summary.json').is_file():
            summary.update(json.loads((case/'summary.json').read_text()))
        summary.update(status='FAIL',error=str(error))
        raise
    finally:
        save(case/'summary.json',summary)
    save(work/('latest_'+args.action+'.json'),{'status':summary['status'],'path':str(case)})

if __name__=='__main__':main()
