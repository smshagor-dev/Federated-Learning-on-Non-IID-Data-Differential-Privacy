#!/usr/bin/env python3
"""Deterministic, sharded launcher for the 1680-cell IEEE TAI primary matrix.

This scheduler does not produce or imply results by itself. Execute only on a
provisioned workstation / HPC / GPU cluster with sufficient time, storage, and
official torchvision data. Each completed result must be audited separately.
"""
from __future__ import annotations
import argparse
import datetime
import hashlib
import json
import os
import pathlib
import shutil
import subprocess
import sys
from dataclasses import dataclass

# Direct script entrypoints put scripts/ at sys.path[0], not the repository root.
# Ensure the same audited package can be imported after completing real training.
PROJECT_ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

DATASETS=('MNIST','FASHIONMNIST','CIFAR10','CIFAR100')
CONDITIONS=('iid','dirichlet:1.0','dirichlet:0.3','dirichlet:0.1','dirichlet:0.05','pathological:2','quantity_skew:1.0')
METHODS=('fedavg','fedprox','scaffold','ditto','per_fedavg','fedsam')
SEEDS=(11,23,37,53,71,83,97,109,127,149)

@dataclass(frozen=True)
class Cell:
    dataset:str
    condition:str
    method:str
    seed:int

    def path(self,root:pathlib.Path)->pathlib.Path:
        return root/self.dataset.lower()/self.condition.replace(':','-')/str(self.seed)/self.method

    def to_dict(self):
        return {'dataset':self.dataset,'condition':self.condition,'method':self.method,'seed':self.seed,'rounds':50}


def required_cells()->list[Cell]:
    return [Cell(d,c,m,s) for d in DATASETS for c in CONDITIONS for m in METHODS for s in SEEDS]


def check_completed(path:pathlib.Path,cell:Cell,commit_sha:str):
    from tai_pfl.analysis import _check_cell
    obj=_check_cell(path/'result.json')
    q=obj['request']
    if (q['dataset'],q['partition'],q['method'],int(q['seed'])) != (cell.dataset,cell.condition,cell.method,cell.seed):
        raise RuntimeError('completed result belongs to a different cell')
    if q['source_commit_sha']!=commit_sha:
        raise RuntimeError('source version changed; refuse an unpaired result')
    if q['rounds']!=50 or q['training']['local_epochs']!=2 or q['training']['batch_size']!=64:
        raise RuntimeError('completed result has different training budget')
    if abs(float(q['training']['participation'])-.2)>1e-12:
        raise RuntimeError('completed result has different client participation')
    if int(obj['metrics']['client_count'])!=20:
        raise RuntimeError('not 20 clients')
    return obj


def main(argv=None)->int:
    parser=argparse.ArgumentParser(description='Plan or execute *real* 1680-cell PFL study; no synthesis')
    parser.add_argument('--root',default='benchmarks/tai-pfl')
    parser.add_argument('--data-root',default='data_raw')
    parser.add_argument('--shard-count',type=int,default=1)
    parser.add_argument('--shard-index',type=int,default=0)
    parser.add_argument('--max-cells',type=int,default=None,help='bounded debugging, not an end-to-end study')
    parser.add_argument('--execute',action='store_true',help='explicitly permit actual dataset download and training')
    parser.add_argument('--continue-on-error',action='store_true')
    parser.add_argument('--archive-incomplete',action='store_true',help='preserve failed/partial run before rerun')
    parser.add_argument('--skip-complete',action='store_true',help='resume only if complete result is verified')
    parser.add_argument('--python',default=sys.executable)
    parser.add_argument('--cuda-device',default=None,help='optional CUDA_VISIBLE_DEVICES value')
    args=parser.parse_args(argv)
    if args.shard_count<=0 or not 0<=args.shard_index<args.shard_count:
        parser.error('shard index must be in 0..shard_count-1')
    if args.max_cells is not None and args.max_cells<=0:
        parser.error('--max-cells must be positive')
    all_cells=required_cells()
    assert len(all_cells)==1680 and len(set(all_cells))==1680
    owned=[c for index,c in enumerate(all_cells) if index%args.shard_count==args.shard_index]
    if args.max_cells is not None:owned=owned[:args.max_cells]
    root=pathlib.Path(args.root)
    record={'status':'PLANNED_NOT_EXECUTED' if not args.execute else 'EXECUTION_STARTED',
            'expected_full_matrix':len(all_cells), 'assigned_shard_cells':len(owned),
            'shard_index':args.shard_index,'shard_count':args.shard_count,
            'exact_expected_cells':[c.to_dict() for c in owned]}
    if not args.execute:
        print(json.dumps(record,indent=2))
        return 0
    git=subprocess.run(['git','rev-parse','HEAD'],text=True,capture_output=True,check=True)
    sha=git.stdout.strip()
    if len(sha)!=40:raise RuntimeError('a full source SHA is required')
    if not pathlib.Path('tai_pfl/study.py').exists():
        raise RuntimeError('run from repository root containing tai_pfl/study.py')
    status_dir=root/'_orchestration'
    status_dir.mkdir(parents=True,exist_ok=True)
    status_file=status_dir/f'shard-{args.shard_index}-of-{args.shard_count}.jsonl'
    env=os.environ.copy()
    # Keep nested -m study and the post-training verification on one import path.
    existing_path = env.get('PYTHONPATH', '')
    env['PYTHONPATH'] = str(PROJECT_ROOT) + (os.pathsep + existing_path if existing_path else '')
    if args.cuda_device is not None:env['CUDA_VISIBLE_DEVICES']=args.cuda_device
    env['PYTHONUNBUFFERED']='1'
    errors=0
    with status_file.open('a',encoding='utf-8') as journal:
        for index,cell in enumerate(owned):
            location=cell.path(root)
            identity=cell.to_dict()
            identity['source_commit_sha']=sha
            if location.exists() and any(location.iterdir()):
                if (location/'result.json').exists() and args.skip_complete:
                    try:
                        check_completed(location,cell,sha)
                        journal.write(json.dumps({'event':'verified_skip','cell':identity})+'\n');journal.flush()
                        print(f'[{index+1}/{len(owned)}] VERIFIABLY COMPLETE {identity}',flush=True)
                        continue
                    except Exception as exc:
                        raise RuntimeError(f'refusing stale/corrupted completed data: {location}: {exc}') from exc
                if args.archive_incomplete and not (location/'result.json').exists():
                    stamp=datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%SZ')
                    archive=root/'_failed_attempts'/f'shard-{args.shard_index}'/stamp/cell.dataset/cell.condition.replace(':','-')/str(cell.seed)/cell.method
                    archive.parent.mkdir(parents=True,exist_ok=True)
                    if archive.exists():raise FileExistsError(f'archive exists: {archive}')
                    shutil.move(str(location),str(archive))
                    journal.write(json.dumps({'event':'archived_unfinished','cell':identity,'archive':str(archive)})+'\n');journal.flush()
                else:
                    raise FileExistsError(f'run exists: {location}; use --skip-complete or explicitly --archive-incomplete')
            cmd=[args.python,'-m','tai_pfl.study', '--dataset',cell.dataset,'--condition',cell.condition,
                 '--seed',str(cell.seed),'--methods',cell.method,'--rounds','50','--clients','20',
                 '--minimum-train','10','--batch-size','64','--local-epochs','2',
                 '--participation','0.2','--output-dir',str(root),'--data-root',args.data_root]
            print(f'[{index+1}/{len(owned)}] ACTUAL TRAINING: {identity}',flush=True)
            journal.write(json.dumps({'event':'started','cell':identity,'command':cmd})+'\n');journal.flush()
            proc=subprocess.run(cmd,env=env,check=False)
            try:
                if proc.returncode:raise RuntimeError(f'training exited {proc.returncode}')
                check_completed(location,cell,sha)
                journal.write(json.dumps({'event':'verified_complete','cell':identity})+'\n');journal.flush()
            except Exception as exc:
                errors+=1
                journal.write(json.dumps({'event':'failed','cell':identity,'reason':str(exc)})+'\n');journal.flush()
                if not args.continue_on_error:raise
    print(f'Finished assigned shard, cells={len(owned)}, failures={errors}',flush=True)
    return 1 if errors else 0

if __name__=='__main__':raise SystemExit(main())