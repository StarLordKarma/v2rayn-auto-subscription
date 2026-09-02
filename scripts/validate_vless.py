#!/usr/bin/env python3
import argparse
import json
import os
import subprocess
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from urllib.parse import urlsplit, parse_qs, unquote

PRIORITY_FLAGS = {
    "🇺🇸": 0, "🇬🇧": 1, "🇩🇪": 2, "🇨🇭": 3, "🇳🇱": 4, "🇳🇴": 5,
}

def q1(q, name, default=""):
    values = q.get(name)
    return values[0] if values else default

def build_config(link, listen_port):
    u = urlsplit(link); q = parse_qs(u.query, keep_blank_values=True)
    host = u.hostname; port = u.port or 443; uuid = unquote(u.username or "")
    tag = unquote(u.fragment or host or "server"); network = q1(q, "type", "tcp")
    security = q1(q, "security", "none"); flow = q1(q, "flow", "")
    if not host or not uuid: raise ValueError("missing host or UUID")
    user = {"id": uuid, "encryption": q1(q, "encryption", "none")}
    if flow: user["flow"] = flow
    stream = {"network": network, "security": security}
    if security == "reality":
        stream["realitySettings"] = {"serverName": q1(q,"sni",""), "fingerprint": q1(q,"fp","chrome"), "publicKey": q1(q,"pbk",""), "shortId": q1(q,"sid",""), "spiderX": q1(q,"spx","")}
    if network == "ws":
        stream["wsSettings"] = {"path": q1(q,"path","/"), "headers": {"Host": q1(q,"host","")} if q1(q,"host","") else {}}
    elif network == "grpc":
        stream["grpcSettings"] = {"serviceName": q1(q,"serviceName",q1(q,"servicename","")), "multiMode": q1(q,"mode","") == "multi"}
    return {"log":{"loglevel":"warning"},"inbounds":[{"listen":"127.0.0.1","port":listen_port,"protocol":"http","settings":{}}],"outbounds":[{"tag":"test","protocol":"vless","settings":{"vnext":[{"address":host,"port":port,"users":[user]}]},"streamSettings":stream}]}, tag, host, port

def curl_probe(proxy, url, timeout):
    cp = subprocess.run(["curl","-sS","-L","--connect-timeout","4","--max-time",str(timeout),"-A","Mozilla/5.0 (Macintosh; Apple Silicon Mac OS X) AppleWebKit/537.36 Chrome/151 Safari/537.36","-x",proxy,"-o","/dev/null","-w","%{http_code}",url], text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    return cp.returncode, cp.stdout.strip()

def test_link(xray, link, index, timeout):
    listen_port=20000+index; proc=cfg_path=log_path=log=None
    try:
        config,tag,host,port=build_config(link,listen_port)
        with tempfile.NamedTemporaryFile("w",suffix=".json",delete=False) as cfg: json.dump(config,cfg); cfg_path=cfg.name
        fd,log_path=tempfile.mkstemp(prefix="xray-check-",suffix=".log"); os.close(fd); log=open(log_path,"w")
        proc=subprocess.Popen([xray,"run","-config",cfg_path],stdout=log,stderr=log); time.sleep(.45)
        if proc.poll() is not None: return index,False,False,False,link,tag,host,port
        proxy=f"http://127.0.0.1:{listen_port}"
        rc,code=curl_probe(proxy,"https://www.google.com/generate_204",timeout)
        internet_ok=rc==0 and code in {"200","204"}
        if not internet_ok: return index,False,False,False,link,tag,host,port
        grc,gcode=curl_probe(proxy,"https://gemini.google.com/",timeout)
        gemini_ok=grc==0 and gcode.isdigit() and 200<=int(gcode)<400
        irc,icode=curl_probe(proxy,"https://www.instagram.com/",timeout)
        instagram_ok=irc==0 and icode.isdigit() and 200<=int(icode)<400
        return index,True,gemini_ok,instagram_ok,link,tag,host,port
    except Exception:
        return index,False,False,False,link,"parse-error","",0
    finally:
        if proc is not None:
            try: proc.terminate(); proc.wait(timeout=1)
            except Exception:
                try: proc.kill()
                except Exception: pass
        if log is not None:
            try: log.close()
            except Exception: pass
        for p in (cfg_path,log_path):
            if p:
                try: os.unlink(p)
                except OSError: pass

def priority(tag):
    for flag,rank in PRIORITY_FLAGS.items():
        if flag in tag: return rank
    return 99

def speed_value(tag):
    import re
    m=re.search(r"(\d+(?:\.\d+)?)\s*(?:Mb|Mbps)",tag,re.I)
    return float(m.group(1)) if m else 0.0

def unique_by_endpoint(rows):
    seen=set(); out=[]
    for row in rows:
        u=urlsplit(row[4]); key=(u.hostname,u.port or 443)
        if key not in seen: seen.add(key); out.append(row)
    return out

def main():
    ap=argparse.ArgumentParser(); ap.add_argument("--input",default="speed_tested.txt"); ap.add_argument("--xray",default="./xray"); ap.add_argument("--working",default="working.txt"); ap.add_argument("--working-base64",default="working_base64.txt"); ap.add_argument("--report",default="report.tsv"); ap.add_argument("--timeout",type=int,default=10); ap.add_argument("--workers",type=int,default=20); args=ap.parse_args()
    links=[x.strip() for x in Path(args.input).read_text(errors="ignore").splitlines() if x.strip().startswith("vless://")]
    results=[]
    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        futs=[ex.submit(test_link,args.xray,link,i,args.timeout) for i,link in enumerate(links,1)]
        for fut in as_completed(futs):
            r=fut.result(); results.append(r); i,internet_ok,gemini_ok,instagram_ok,_,tag,host,port=r
            print(f"[{i:03d}/{len(links):03d}] net={internet_ok} gemini={gemini_ok} instagram={instagram_ok} {tag[:35]} {host}:{port}",flush=True)
    good=[r for r in results if r[1] and r[2] and r[3]]; good=unique_by_endpoint(good); good.sort(key=lambda r:(priority(r[5]),-speed_value(r[5]),r[0])); lines=[r[4] for r in good]
    Path(args.working).write_text("".join(x+"\n" for x in lines))
    import base64
    Path(args.working_base64).write_text(base64.b64encode(("".join(x+"\n" for x in lines)).encode()).decode())
    rows=["status\tgemini\tinstagram\ttag\thost\tport"]
    for _,internet_ok,gemini_ok,instagram_ok,_,tag,host,port in sorted(results,key=lambda r:r[0]): rows.append(f"{'WORKS' if internet_ok else 'FAIL'}\t{'YES' if gemini_ok else 'NO'}\t{'YES' if instagram_ok else 'NO'}\t{tag}\t{host}\t{port}")
    Path(args.report).write_text("\n".join(rows)+"\n")
    print(f"Published {len(lines)} servers passing internet + Gemini + Instagram")

if __name__=="__main__": main()
