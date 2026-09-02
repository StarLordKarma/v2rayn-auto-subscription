#!/usr/bin/env python3
import argparse
import base64
import json
import os
import re
import subprocess
import tempfile
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlsplit, urlunsplit

PRIORITY_COUNTRIES = {"US": 0, "GB": 1, "DE": 2, "CH": 3, "NL": 4, "NO": 5}
COUNTRY_FLAGS = {
    "US": "🇺🇸", "GB": "🇬🇧", "DE": "🇩🇪", "CH": "🇨🇭", "NL": "🇳🇱", "NO": "🇳🇴",
    "FR": "🇫🇷", "FI": "🇫🇮", "LT": "🇱🇹", "PL": "🇵🇱", "IE": "🇮🇪", "IT": "🇮🇹",
    "SE": "🇸🇪", "SG": "🇸🇬", "CO": "🇨🇴", "HK": "🇭🇰", "RU": "🇷🇺", "MD": "🇲🇩",
    "RO": "🇷🇴", "BR": "🇧🇷", "EE": "🇪🇪", "UA": "🇺🇦", "JP": "🇯🇵",
}


def q1(q, name, default=""):
    values = q.get(name)
    return values[0] if values else default


def build_config(link, listen_port):
    u = urlsplit(link)
    q = parse_qs(u.query, keep_blank_values=True)
    host = u.hostname
    port = u.port or 443
    uuid = unquote(u.username or "")
    tag = unquote(u.fragment or host or "server")
    network = q1(q, "type", "tcp")
    security = q1(q, "security", "none")
    flow = q1(q, "flow", "")
    if not host or not uuid:
        raise ValueError("missing host or UUID")

    user = {"id": uuid, "encryption": q1(q, "encryption", "none")}
    if flow:
        user["flow"] = flow

    stream = {"network": network, "security": security}
    if security == "reality":
        stream["realitySettings"] = {
            "serverName": q1(q, "sni", ""),
            "fingerprint": q1(q, "fp", "chrome"),
            "publicKey": q1(q, "pbk", ""),
            "shortId": q1(q, "sid", ""),
            "spiderX": q1(q, "spx", ""),
        }
    if network == "ws":
        stream["wsSettings"] = {
            "path": q1(q, "path", "/"),
            "headers": {"Host": q1(q, "host", "")} if q1(q, "host", "") else {},
        }
    elif network == "grpc":
        stream["grpcSettings"] = {
            "serviceName": q1(q, "serviceName", q1(q, "servicename", "")),
            "multiMode": q1(q, "mode", "") == "multi",
        }

    return {
        "log": {"loglevel": "warning"},
        "inbounds": [{"listen": "127.0.0.1", "port": listen_port, "protocol": "http", "settings": {}}],
        "outbounds": [{
            "tag": "test",
            "protocol": "vless",
            "settings": {"vnext": [{"address": host, "port": port, "users": [user]}]},
            "streamSettings": stream,
        }],
    }, tag, host, port


def curl_probe(proxy, url, timeout, want_body=False):
    fmt = "%{http_code}\t%{time_total}"
    cmd = [
        "curl", "-sS", "-L", "--connect-timeout", "4", "--max-time", str(timeout),
        "-A", "Mozilla/5.0 (Macintosh; Apple Silicon Mac OS X) AppleWebKit/537.36 Chrome/151 Safari/537.36",
        "-x", proxy,
    ]
    if want_body:
        cmd += ["-w", "\n" + fmt, url]
    else:
        cmd += ["-o", "/dev/null", "-w", fmt, url]
    cp = subprocess.run(cmd, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    out = cp.stdout.strip()
    if want_body:
        parts = out.rsplit("\n", 1)
        body = parts[0].strip() if len(parts) == 2 else ""
        meta = parts[-1]
    else:
        body = ""
        meta = out
    fields = meta.split("\t")
    code = fields[0] if fields else ""
    try:
        elapsed = float(fields[1]) if len(fields) > 1 else 999.0
    except ValueError:
        elapsed = 999.0
    return cp.returncode, code, elapsed, body


def test_link(xray, link, index, timeout):
    listen_port = 20000 + index
    proc = cfg_path = log_path = log = None
    tag = "parse-error"; host = ""; port = 0
    try:
        config, tag, host, port = build_config(link, listen_port)
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as cfg:
            json.dump(config, cfg)
            cfg_path = cfg.name
        fd, log_path = tempfile.mkstemp(prefix="xray-check-", suffix=".log")
        os.close(fd)
        log = open(log_path, "w")
        proc = subprocess.Popen([xray, "run", "-config", cfg_path], stdout=log, stderr=log)
        time.sleep(0.45)
        if proc.poll() is not None:
            return (index, False, False, False, "", 999.0, link, tag, host, port)

        proxy = f"http://127.0.0.1:{listen_port}"
        rc, code, latency, _ = curl_probe(proxy, "https://www.google.com/generate_204", timeout)
        internet_ok = rc == 0 and code in {"200", "204"}
        if not internet_ok:
            return (index, False, False, False, "", latency, link, tag, host, port)

        grc, gcode, _, _ = curl_probe(proxy, "https://gemini.google.com/", timeout)
        gemini_ok = grc == 0 and gcode.isdigit() and 200 <= int(gcode) < 400

        irc, icode, _, _ = curl_probe(proxy, "https://www.instagram.com/", timeout)
        instagram_ok = irc == 0 and icode.isdigit() and 200 <= int(icode) < 400

        egress_ip = ""
        if gemini_ok and instagram_ok:
            prc, pcode, _, body = curl_probe(proxy, "https://api.ipify.org/", timeout, want_body=True)
            if prc == 0 and pcode == "200":
                egress_ip = body.strip().splitlines()[0] if body.strip() else ""

        return (index, True, gemini_ok, instagram_ok, egress_ip, latency, link, tag, host, port)
    except Exception:
        return (index, False, False, False, "", 999.0, link, tag, host, port)
    finally:
        if proc is not None:
            try:
                proc.terminate(); proc.wait(timeout=1)
            except Exception:
                try: proc.kill()
                except Exception: pass
        if log is not None:
            try: log.close()
            except Exception: pass
        for p in (cfg_path, log_path):
            if p:
                try: os.unlink(p)
                except OSError: pass


def geolocate_ips(ips):
    unique = sorted({ip for ip in ips if ip})
    result = {}
    for start in range(0, len(unique), 100):
        chunk = unique[start:start + 100]
        payload = json.dumps([{"query": ip, "fields": "status,countryCode,query"} for ip in chunk]).encode()
        req = urllib.request.Request(
            "http://ip-api.com/batch?fields=status,countryCode,query",
            data=payload,
            headers={"Content-Type": "application/json", "User-Agent": "v2rayn-auto-subscription/1.0"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                rows = json.loads(resp.read().decode())
            for row in rows:
                if row.get("status") == "success":
                    result[row.get("query", "")] = row.get("countryCode", "")
        except Exception as exc:
            print(f"Geolocation batch failed: {exc}", flush=True)
        if start + 100 < len(unique):
            time.sleep(1.2)
    return result


def speed_value(tag):
    m = re.search(r"(\d+(?:\.\d+)?)\s*(?:Mb|Mbps)", tag, re.I)
    return float(m.group(1)) if m else 0.0


def unique_by_endpoint(rows):
    seen = set(); out = []
    for row in rows:
        u = urlsplit(row[6])
        key = (u.hostname, u.port or 443)
        if key not in seen:
            seen.add(key); out.append(row)
    return out


def retag_link(link, country, latency, original_tag):
    u = urlsplit(link)
    flag = COUNTRY_FLAGS.get(country, "🌐")
    speed = speed_value(original_tag)
    speed_text = f"{int(speed) if speed.is_integer() else speed:g}Mb" if speed else "?Mb"
    clean_tag = re.sub(r"^[^|]*\|\s*", "", original_tag).strip() or (u.hostname or "server")
    new_tag = f"{flag}{country or '??'} | {speed_text} | {int(round(latency * 1000))}ms | {clean_tag}"
    return urlunsplit((u.scheme, u.netloc, u.path, u.query, new_tag))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", default="speed_tested.txt")
    ap.add_argument("--xray", default="./xray")
    ap.add_argument("--working", default="working.txt")
    ap.add_argument("--working-base64", default="working_base64.txt")
    ap.add_argument("--report", default="report.tsv")
    ap.add_argument("--timeout", type=int, default=10)
    ap.add_argument("--workers", type=int, default=20)
    args = ap.parse_args()

    links = [x.strip() for x in Path(args.input).read_text(errors="ignore").splitlines() if x.strip().startswith("vless://")]
    results = []
    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        futs = [ex.submit(test_link, args.xray, link, i, args.timeout) for i, link in enumerate(links, 1)]
        for fut in as_completed(futs):
            r = fut.result(); results.append(r)
            i, internet_ok, gemini_ok, instagram_ok, egress_ip, latency, _, tag, host, port = r
            print(f"[{i:03d}/{len(links):03d}] net={internet_ok} gemini={gemini_ok} instagram={instagram_ok} ip={egress_ip or '-'} latency={latency:.3f}s {tag[:28]} {host}:{port}", flush=True)

    good = [r for r in results if r[1] and r[2] and r[3] and r[4]]
    geo = geolocate_ips([r[4] for r in good])
    good = [r for r in good if geo.get(r[4])]
    good = unique_by_endpoint(good)
    good.sort(key=lambda r: (PRIORITY_COUNTRIES.get(geo.get(r[4], ""), 99), r[5], -speed_value(r[7]), r[0]))

    output_links = [retag_link(r[6], geo.get(r[4], ""), r[5], r[7]) for r in good]
    raw = "".join(x + "\n" for x in output_links)
    Path(args.working).write_text(raw)
    Path(args.working_base64).write_text(base64.b64encode(raw.encode()).decode())

    rank_by_index = {r[0]: rank for rank, r in enumerate(good, 1)}
    rows = ["status\tgemini\tinstagram\tegress_ip\tcountry\tlatency_ms\tupstream_speed_mbps\trank\ttag\thost\tport"]
    for r in sorted(results, key=lambda x: x[0]):
        idx, internet_ok, gemini_ok, instagram_ok, egress_ip, latency, _, tag, host, port = r
        rows.append(
            f"{'WORKS' if internet_ok else 'FAIL'}\t{'YES' if gemini_ok else 'NO'}\t{'YES' if instagram_ok else 'NO'}\t"
            f"{egress_ip}\t{geo.get(egress_ip, '')}\t{int(round(latency*1000)) if latency < 900 else ''}\t"
            f"{speed_value(tag):g}\t{rank_by_index.get(idx, '')}\t{tag}\t{host}\t{port}"
        )
    Path(args.report).write_text("\n".join(rows) + "\n")

    by_country = {}
    for r in good:
        cc = geo.get(r[4], "??")
        by_country[cc] = by_country.get(cc, 0) + 1
    print(f"Published {len(output_links)} servers passing internet + Gemini + Instagram with verified exit country")
    print("Country distribution:", ", ".join(f"{k}={v}" for k, v in sorted(by_country.items())))
    if good:
        best = good[0]
        print(f"Top ranked: {geo.get(best[4])} {best[4]} {int(round(best[5]*1000))}ms {best[7]}")


if __name__ == "__main__":
    main()
