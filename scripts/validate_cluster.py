"""Explicit local M6 validation; retains evidence, never prints credentials."""
import argparse
import json
from pathlib import Path
import secrets
import threading

from evalnoise.cluster import Client, credential, server, worker_once
from evalnoise.coordinator import Control, ControlError, initialize
from evalnoise.docker import Docker
from evalnoise.storage import write_json


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--image',required=True)
    parser.add_argument('--output',type=Path,default=Path('runs'))
    parser.add_argument('--trust-config',action='store_true')
    args=parser.parse_args()
    if not args.trust_config:parser.error('Review the image, then pass --trust-config')
    backend=Docker();backend.doctor();image=backend.image(args.image)['id']
    config={'schema_version':1,'name':'m6-owned-validation','seed':31,'repeats':1,
            'tasks':[{'id':'cpu','image':image,'command':['python','/opt/evalnoise/workload.py','cpu']}],
            'profiles':[{'id':'standard','cpus':1,'memory_mb':128,'timeout_s':15}]}
    root=args.output/('cluster-state-'+secrets.token_hex(6))
    initialize(root,{'cpu':{'pool':'trusted-local','config':config}})
    control=Control(root);httpd=server(control,0);thread=threading.Thread(target=httpd.serve_forever);thread.start()
    url=f'http://127.0.0.1:{httpd.server_port}';records=[]
    try:
        for owner_name,worker_name in [('alice','worker'),('bob','worker-b')]:
            owner=Client(url,credential(root/f'{owner_name}.json')['token'])
            worker=credential(root/f'{worker_name}.json')
            job=owner.request('/submit',{'catalog':'cpu','request_key':'same-key'})['job']
            result=worker_once(Client(url,worker['token']),worker['images'],args.output)
            assert result['job']==job
            artifact=owner.request('/artifact/'+job)
            assert artifact['bundle']['trials'][0]['status']=='passed'
            other='bob' if owner_name=='alice' else 'alice'
            try:Client(url,credential(root/f'{other}.json')['token']).request('/artifact/'+job)
            except ControlError as error:assert error.status==404
            else:raise AssertionError('Cross-owner artifact leaked')
            records.append({'owner':owner_name,'worker':worker_name,'job':job,'run_directory':result['directory'],
                            'receipt':result['receipt'],'cross_owner_read':'refused'})
        audit=control.audit(credential(root/'admin.json')['token'])
        assert sum(e['kind']=='accepted' for e in audit['events'])==2
        evidence={'state_directory':str(root),'image_id':image,'jobs':records,'audit':audit,
                  'scope':'Two owners and two worker identities, sequential local Docker runs; not a multi-host isolation test'}
        write_json(root/'validation.json',evidence)
        print(json.dumps({'state_directory':str(root),'accepted_jobs':len(records),'audit_valid':audit['valid'],
                          'run_directories':[r['run_directory'] for r in records]},indent=2))
    finally:httpd.shutdown();thread.join();httpd.server_close()


if __name__=='__main__':main()
