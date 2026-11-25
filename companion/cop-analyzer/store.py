"""Single writer SQLite archive. Only this new application's own data directory is writable."""
import sqlite3,json,zlib,time,math,csv,io,gzip,shutil
from pathlib import Path
from datetime import datetime,timedelta
from analysis import *

def pack(d):return zlib.compress(json.dumps(d,separators=(',',':'),allow_nan=False).encode(),6)
def unpack(d):return json.loads(zlib.decompress(d))

class Store:
    def __init__(self,path):
        self.path=Path(path);self.path.parent.mkdir(parents=True,exist_ok=True)
        self.db=sqlite3.connect(path);self.db.row_factory=sqlite3.Row
        self.db.executescript('PRAGMA journal_mode=WAL;PRAGMA synchronous=FULL;PRAGMA busy_timeout=5000;CREATE TABLE IF NOT EXISTS samples(slot INTEGER PRIMARY KEY,t REAL NOT NULL,data BLOB NOT NULL);CREATE INDEX IF NOT EXISTS samples_time ON samples(t);CREATE TABLE IF NOT EXISTS events(id INTEGER PRIMARY KEY,ts REAL NOT NULL,kind TEXT NOT NULL,topic TEXT,value TEXT,retained INTEGER DEFAULT 0);CREATE INDEX IF NOT EXISTS events_time ON events(ts);CREATE TABLE IF NOT EXISTS buckets(res INTEGER NOT NULL,t INTEGER NOT NULL,data TEXT NOT NULL,PRIMARY KEY(res,t));CREATE TABLE IF NOT EXISTS cycles(id INTEGER PRIMARY KEY,start REAL,end REAL,left_censored INTEGER,right_censored INTEGER DEFAULT 0,counter_matched INTEGER DEFAULT 0,data TEXT NOT NULL);CREATE INDEX IF NOT EXISTS cycle_start ON cycles(start);CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY,data TEXT);CREATE TABLE IF NOT EXISTS daily(day TEXT PRIMARY KEY,data TEXT);')
        latest=self.db.execute('SELECT data FROM samples ORDER BY slot DESC LIMIT 1').fetchone();self.previous=unpack(latest[0]) if latest else None
        self.detector=self.meta('detector',{'stable':None,'pending':None,'cycle':None,'last_stop':None,'counter':None,'counter_pending':[]});self.error=None
    def meta(self,key,default=None):
        row=self.db.execute('SELECT data FROM meta WHERE key=?',(key,)).fetchone();return json.loads(row[0]) if row else default
    def setmeta(self,key,data):self.db.execute('INSERT OR REPLACE INTO meta VALUES(?,?)',(key,json.dumps(data,separators=(',',':'))))
    def event(self,ts,kind,topic=None,value=None,retained=False):
        self.db.execute('INSERT INTO events(ts,kind,topic,value,retained) VALUES(?,?,?,?,?)',(ts,kind,topic,json.dumps(value,separators=(',',':')),int(retained)))
    def aggregate(self,start,end,res):
        a=blank()
        for r in self.db.execute('SELECT t,data FROM buckets WHERE res=? AND t<? AND t+?>? ORDER BY t',(res,end,res,start)):
            left=max(start,r['t']);right=min(end,r['t']+res)
            if left>r['t'] or right<r['t']+res:
                partial=self.db.execute('SELECT data FROM samples WHERE t>? AND t<=? ORDER BY t',(left,right+45)).fetchall()
                if partial:
                    for item in partial:
                        row=unpack(item[0]);iv=row['interval'];span=iv['end']-iv['start'];overlap=max(0,min(right,iv['end'])-max(left,iv['start']))
                        if span>0 and overlap>0:add(a,row,overlap/span)
                else:merge(a,json.loads(r['data']),(right-left)/res)
            else:merge(a,json.loads(r['data']))
        return finish(a,max(0,end-start))
    def ingest(self,row):
        if self.db.execute('SELECT 1 FROM samples WHERE slot=?',(int(row['t']//15)*15,)).fetchone():return False
        previous=self.previous;row['interval']=interval(previous,row)
        with self.db:
            self.db.execute('INSERT INTO samples VALUES(?,?,?)',(int(row['t']//15)*15,row['t'],pack(row)))
            iv=row['interval']
            if iv['seconds']>0:
                for res in (60,300,900,3600):
                    start=int(iv['start']//res)*res
                    for t in range(start,int(iv['end']//res)*res+1,res):
                        overlap=max(0,min(t+res,iv['end'])-max(t,iv['start']))
                        if overlap<=0:continue
                        old=self.db.execute('SELECT data FROM buckets WHERE res=? AND t=?',(res,t)).fetchone();a=json.loads(old[0]) if old else blank();add(a,row,overlap/iv['seconds'])
                        self.db.execute('INSERT OR REPLACE INTO buckets VALUES(?,?,?)',(res,t,json.dumps(a,separators=(',',':'))))
            self.cycles(row,previous)
            for s in SERVICES:
                current=row.get('totals',{}).get(s);old=previous.get('totals',{}).get(s) if previous else None
                if current is not None and old is not None and current<old:self.event(row['t'],'energy_counter_reset',s,{'before':old,'after':current})
            self.setmeta('detector',self.detector)
        self.previous=row;return True
    def close_cycle(self,end,row,right=False):
        d=self.detector;cid=d.get('cycle')
        if cid is None:return
        record=self.db.execute('SELECT * FROM cycles WHERE id=?',(cid,)).fetchone();info=json.loads(record['data']);duration=end-record['start']
        a=blank()
        for item in self.db.execute('SELECT data FROM samples WHERE t>? AND t<=? ORDER BY t',(record['start'],end+45)):
            r=unpack(item[0]);iv=r['interval'];dt=iv['end']-iv['start'];overlap=max(0,min(end,iv['end'])-max(record['start'],iv['start']))
            if dt>0 and overlap>0:add(a,r,overlap/dt)
        stats=finish(a,max(0,duration));info.update(stats=stats,duration_seconds=duration,last={**{k:row.get(k) for k in (*ALIASES,'demand')},'pump2':row.get('pumps',{}).get('zone2'),'mixer':None},cause={'confidence':'non determinabile','label':'Interruzione acquisizione: fine ciclo non osservata'} if right else stop_cause(self.previous or row,row),end_observed=not right)
        self.db.execute('UPDATE cycles SET end=?,right_censored=?,data=? WHERE id=?',(end,int(right),json.dumps(info),cid));d['cycle']=None;d['last_stop']=end if not right else None
    def cycles(self,row,previous):
        d=self.detector;t=row['t'];running=row['freq']>0 if row.get('freq') is not None else None
        if previous and t-previous['t']>45:
            self.close_cycle(previous['t'],previous,True);d.update(stable=None,pending=None);self.event(t,'acquisition_gap',value={'seconds':t-previous['t']})
        if running is None:
            if d['cycle'] is not None:self.close_cycle((previous or row)['t'],previous or row,True)
            d.update(stable=None,pending=None)
        elif d['stable'] is None:
            d['stable']=running
            if running:
                info={'first':{**{k:row.get(k) for k in (*ALIASES,'demand')},'pump2':row.get('pumps',{}).get('zone2'), 'mixer':None},'previous_pause_seconds':None,'start_observed':False}
                d['cycle']=self.db.execute('INSERT INTO cycles(start,left_censored,data) VALUES(?,?,?)',(t,1,json.dumps(info))).lastrowid
        elif running!=d['stable']:
            p=d['pending']
            if not p or p['running']!=running:d['pending']={'running':running,'at':row.get('freq_at') or t,'first_t':t,'receipt':row.get('freq_at')}
            elif t-p['first_t']>=2 and row.get('freq_at')!=p['receipt']:
                stamp=max(p['at'],(previous or row)['t']-45);d['stable']=running;d['pending']=None
                if running:
                    info={'first':{k:row.get(k) for k in (*ALIASES,'demand')},'previous_pause_seconds':stamp-d['last_stop'] if d['last_stop'] is not None else None,'start_observed':True}
                    d['cycle']=self.db.execute('INSERT INTO cycles(start,left_censored,data) VALUES(?,?,?)',(stamp,0,json.dumps(info))).lastrowid;self.event(stamp,'compressor_start',value={'cycle_id':d['cycle']})
                else:self.close_cycle(stamp,row);self.event(stamp,'compressor_stop')
        else:d['pending']=None
        counter=row.get('counter')
        if counter is not None:
            if d['counter'] is not None:
                delta=int(counter-d['counter'])
                if delta>0:
                    if d.get('cycle') is not None and d.get('stable') is True and not d.get('pending') and previous and (previous.get('freq') or 0)>0 and (row.get('freq') or 0)>0:
                        current=self.db.execute('SELECT start FROM cycles WHERE id=?',(d['cycle'],)).fetchone()
                        if current and t-current['start']>45:
                            self.close_cycle(previous['t'],previous,True)
                            info={'first':{**{k:row.get(k) for k in (*ALIASES,'demand')},'pump2':row.get('pumps',{}).get('zone2')},'previous_pause_seconds':None,'start_observed':False,'note':'Contatore incrementato durante apparente continuità: segmento parziale, durata ciclo reale non ricostruita'}
                            d['cycle']=self.db.execute('INSERT INTO cycles(start,left_censored,data) VALUES(?,?,?)',(t,1,json.dumps(info))).lastrowid
                    d['counter_pending'].append({'t':t,'delta':delta});self.event(t,'counter_increment',value={'count':delta})
                elif delta<0:self.event(t,'operations_counter_reset',value={'before':d['counter'],'after':counter})
            d['counter']=counter
        pending=[]
        for item in d['counter_pending']:
            if t-item['t']<45:pending.append(item);continue
            matches=self.db.execute('SELECT id FROM cycles WHERE left_censored=0 AND counter_matched=0 AND start BETWEEN ? AND ? ORDER BY start LIMIT ?',(item['t']-45,item['t']+45,item['delta'])).fetchall()
            for match in matches:self.db.execute('UPDATE cycles SET counter_matched=1 WHERE id=?',(match[0],))
            missing=item['delta']-len(matches)
            if missing:self.event(item['t'],'unreconstructed_start',value={'count':missing,'reason':'Contatore incrementato senza transizione completa osservabile'})
        d['counter_pending']=pending
    def rows(self,start,end,limit=6000):
        count=self.db.execute('SELECT count(*) FROM samples WHERE t BETWEEN ? AND ?',(start,end)).fetchone()[0];stride=max(1,math.ceil(count/limit))
        return [unpack(r[0]) for r in self.db.execute('SELECT data FROM (SELECT data,row_number() OVER(ORDER BY t) AS n FROM samples WHERE t BETWEEN ? AND ?) WHERE n % ?=0',(start,end,stride))]
    def events(self,start,end,limit=1500):
        return [dict(r) for r in self.db.execute("SELECT ts,kind,topic,value FROM events WHERE ts BETWEEN ? AND ? AND kind!='mqtt' ORDER BY ts DESC LIMIT ?",(start,end,limit))]
    def cycle_list(self,start,end):
        out=[{**dict(r),'data':json.loads(r['data'])} for r in self.db.execute('SELECT * FROM cycles WHERE start<=? AND (end>=? OR end IS NULL) ORDER BY start DESC LIMIT 2000',(end,start))]
        for i,c in enumerate(out):
            c['following_pause_seconds']=out[i-1]['start']-c['end'] if i>0 and c['end'] is not None else None
            if c['end'] is None and self.previous:
                stop=self.previous['t'];c['data']['duration_seconds']=stop-c['start'];c['data']['stats']=self.aggregate(c['start'],stop,60);c['data']['last']={**{k:self.previous.get(k) for k in (*ALIASES,'demand')},'pump2':self.previous.get('pumps',{}).get('zone2')}
        return out
    def csv(self,start,end):
        fields=LEGACY+['utc','freq','counter','class','cop_instant','water_delta','flow_error','target','z2','z2_target','lpm','demand','heat_coverage_seconds','dhw_coverage_seconds','quality_json','rooms_json','metrics_json']
        output=io.StringIO();writer=csv.DictWriter(output,fields);writer.writeheader();yield output.getvalue();output.seek(0);output.truncate(0)
        for item in self.db.execute('SELECT data FROM samples WHERE t BETWEEN ? AND ? ORDER BY t',(start,end)):
            r=unpack(item[0]);iv=r['interval'];v={k:r.get(k) for k in fields};v.update(time=round(r['t']*1000),minutes=iv['seconds']/60,utc=datetime.fromtimestamp(r['t'],timezone.utc).isoformat(),heatQ=iv['energy']['heat']['q'] if iv['energy']['heat']['seconds'] else None,heatE=iv['energy']['heat']['e'] if iv['energy']['heat']['seconds'] else None,dhwQ=iv['energy']['dhw']['q'] if iv['energy']['dhw']['seconds'] else None,dhwE=iv['energy']['dhw']['e'] if iv['energy']['dhw']['seconds'] else None,heat_coverage_seconds=iv['energy']['heat']['seconds'],dhw_coverage_seconds=iv['energy']['dhw']['seconds'],quality_json=json.dumps({k:{a:b for a,b in m.items() if a!='value'} for k,m in r['metrics'].items()},separators=(',',':')),rooms_json=json.dumps(r['rooms'],separators=(',',':')),metrics_json=json.dumps(r['metrics'],separators=(',',':')))
            writer.writerow(v);yield output.getvalue();output.seek(0);output.truncate(0)
    def maintain(self,now):
        with self.db:
            self.db.execute('DELETE FROM events WHERE ts<?',(now-7*86400,));self.db.execute('DELETE FROM samples WHERE t<?',(now-30*86400,));self.db.execute('DELETE FROM buckets WHERE t<?',(now-730*86400,));self.db.execute('DELETE FROM cycles WHERE end<?',(now-730*86400,))
            day=datetime.fromtimestamp(now,ROME).date()
            for n in range(3):
                date=day-timedelta(days=n);start=datetime.combine(date,datetime.min.time(),ROME).timestamp();end=min(now,datetime.combine(date+timedelta(days=1),datetime.min.time(),ROME).timestamp());self.db.execute('INSERT OR REPLACE INTO daily VALUES(?,?)',(date.isoformat(),json.dumps(self.aggregate(start,end,60))))
        self.db.execute('PRAGMA wal_checkpoint(PASSIVE)')
        root=self.path.parent;backups=root/'backups';exports=root/'exports';backups.mkdir(exist_ok=True);exports.mkdir(exist_ok=True)
        day=datetime.fromtimestamp(now,ROME).date();yesterday=day-timedelta(days=1);start=datetime.combine(yesterday,datetime.min.time(),ROME).timestamp();end=datetime.combine(day,datetime.min.time(),ROME).timestamp()
        export=exports/(str(yesterday)+'.csv.gz')
        if not export.exists() and self.db.execute('SELECT 1 FROM samples WHERE t BETWEEN ? AND ? LIMIT 1',(start,end)).fetchone():
            with gzip.open(export,'wt',encoding='utf-8',newline='') as f:
                for chunk in self.csv(start,end):f.write(chunk)
        backup=backups/(str(day)+'.sqlite.gz')
        if (not backup.exists() or now-backup.stat().st_mtime>=3600) and shutil.disk_usage(root).free>1024**3:
            temp=backups/'snapshot.tmp';target=sqlite3.connect(temp);self.db.backup(target);target.close()
            compressed=backups/'snapshot.gz.tmp'
            with temp.open('rb') as src,gzip.open(compressed,'wb') as dst:shutil.copyfileobj(src,dst)
            compressed.replace(backup);temp.unlink()
        for f in sorted(backups.glob('*.sqlite.gz'))[:-3]:f.unlink()
        for f in exports.glob('*.csv.gz'):
            if f.name[:10]<(day-timedelta(days=90)).isoformat():f.unlink()
        size=self.db.execute('PRAGMA page_count').fetchone()[0]*self.db.execute('PRAGMA page_size').fetchone()[0]
        used=size-self.db.execute('PRAGMA freelist_count').fetchone()[0]*self.db.execute('PRAGMA page_size').fetchone()[0]
        self.error='Limite archivio 1 GiB raggiunto: acquisizione sospesa, dati esistenti conservati' if used>1024**3 else None
        return {'database_bytes':size,'free_bytes':shutil.disk_usage(root).free,'retention_days':{'samples':30,'raw_mqtt':7,'aggregates_cycles':730},'backup_files':len(list(backups.glob('*.sqlite.gz'))),'error':self.error}
