"""Package only Git-tracked public sources; never private state or the Git directory."""
import argparse,hashlib,subprocess,zipfile
from pathlib import Path
root=Path(__file__).resolve().parents[1]
p=argparse.ArgumentParser();p.add_argument('output',type=Path);args=p.parse_args();args.output.parent.mkdir(parents=True,exist_ok=True)
names=subprocess.check_output(['git','ls-files','-z'],cwd=root).decode().split('\0')
with zipfile.ZipFile(args.output,'w',zipfile.ZIP_DEFLATED) as z:
 for name in names:
  if not name:continue
  path=root/name
  if path.is_file():z.write(path,'aquarea-clima/'+name)
print(hashlib.sha256(args.output.read_bytes()).hexdigest()+'  '+args.output.name)
