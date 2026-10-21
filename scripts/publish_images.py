#!/usr/bin/env python3
"""Build a committed source tree and publish a digest-addressed OCI release."""
import argparse
import io
import json
from pathlib import Path
import re
import subprocess
import tarfile
import tempfile

ROOT=Path(__file__).resolve().parents[1]
APPLICATION='lassy'
IMAGES={'api': 'Dockerfile'}


def git(*args):
    return subprocess.check_output(['git','-C',str(ROOT),*args],text=True).strip()


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--registry',required=True,help='OCI registry hostname[:port] and optional namespace')
    parser.add_argument('--context',default='colima',help='Docker context used to build and push')
    parser.add_argument('--revision',default='HEAD',help='Committed Git revision to build; working-tree edits are excluded')
    parser.add_argument('--platform',default='linux/arm64')
    parser.add_argument('--output',type=Path,default=ROOT/'build/release.json')
    args=parser.parse_args()
    
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._:/-]*',args.registry) or '://' in args.registry:
        parser.error('--registry must be a registry/repository prefix without a URL scheme')
    
    commit=git('rev-parse',f'{args.revision}^{{commit}}')
    release={'application':APPLICATION,'platform':args.platform,
        'source':{'repository':f'https://github.com/jbromfld/{APPLICATION}.git','revision':commit},'images':{}}
    
    # Archive the exact commit: uncommitted files and local secrets never enter the build.
    archive=subprocess.check_output(['git','-C',str(ROOT),'archive',commit])
    
    with tempfile.TemporaryDirectory(prefix=APPLICATION+'-release-') as temporary:
        directory=Path(temporary)
        with tarfile.open(fileobj=io.BytesIO(archive)) as source:
            source.extractall(directory,filter='data')
    
        for component,dockerfile in IMAGES.items():
            image=f'{args.registry.rstrip("/")}/{APPLICATION}/{component}:{commit}'
            metadata=directory/f'.metadata-{component}.json'
    
            subprocess.run(['docker','--context',args.context,'buildx','build','--platform',args.platform,
                '--label',f'org.opencontainers.image.revision={commit}',
                '--label',f'org.opencontainers.image.source={release["source"]["repository"]}',
                '--push','--metadata-file',str(metadata),'-t',image,'-f',str(directory/dockerfile),str(directory)],check=True)
            
            digest=json.loads(metadata.read_text())['containerimage.digest']
            
            if not re.fullmatch(r'sha256:[a-f0-9]{64}',digest):
                raise RuntimeError('Builder did not produce a valid image digest')
            release['images'][component]=image.rsplit(':',1)[0]+'@'+digest
    
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(release,indent=2)+'\n')
    print(f'Release metadata: {args.output}')


if __name__=='__main__':
    main()
