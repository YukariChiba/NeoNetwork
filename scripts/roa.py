#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from pathlib import Path
from ipaddress import IPv4Network, IPv6Network
from itertools import combinations

def keyVal(line):
    l = line.split('=')
    assert l[0].strip()
    if len(l) == 1:
        l.append('')
    repl_quotes = lambda t: t.replace('"', '').replace('\'', '')
    return [l[0].strip(), '='.join([repl_quotes(i).strip() for i in l[1:]])]

cwd = Path()
assert not [d for d in ("asn", "route", "route6", "node") if not (cwd / d).is_dir()]

def str2asn(s_asn):
    s_asn = s_asn.lower()
    if s_asn.startswith('as'):
        s_asn = s_asn[2:]
    return int(s_asn)

def get_asns():
    asns = list()
    for f in (cwd / "asn").iterdir():
        try:
            if not f.is_file():
                continue
            assert f.name.lower().startswith('as')
            asns.append(int(f.name[2:]))
        except Exception:
            print("[!] Error while processing file", f)
            raise
    return asns
ASNS = get_asns()

def shell2dict(shellscript):
    fc = dict()
    for line in shellscript.split('\n'):
        l = line.strip()
        if not l or l.startswith('#'):
            continue
        key, val = keyVal(l)
        fc[key.lower()] = val.lower()
    return fc

def node2asn():
    node_table = dict()
    for f in (cwd / "node").iterdir():
        try:
            if not f.is_file():
                continue
            fc = shell2dict(f.read_text())
            asn = str2asn(fc.get('asn'))
            node_table[f.name.lower()] = asn
        except Exception:
            print("[!] Error while processing file", f)
            raise
    return node_table
NODE_TABLE = node2asn()

def route2roa(dirname, is_ipv6=False):
    roa_entries = list()
    for f in (cwd / dirname).iterdir():
        try:
            if not f.is_file():
                continue
            fc = shell2dict(f.read_text())
            nettype = IPv6Network if is_ipv6 else IPv4Network
            get_supernet = lambda s_net: None if not s_net else nettype(s_net, strict=True)
            roa_entries_key = ("asn", "prefix", "supernet")
            if fc.get('type') in ('lo', 'subnet'):
                asn = str2asn(fc.get('as'))
                assert asn in ASNS
                route = f.name.replace(',', '/')
                supernet = get_supernet(fc.get('supernet'))
                roa_entries.append(dict(zip(roa_entries_key, [asn, nettype(route, strict=True), supernet])))
            elif fc.get('type').startswith('tun'):
                assert NODE_TABLE[fc.get('downstream')] # extra check for downstream
                asn = NODE_TABLE[fc.get('upstream')]
                assert asn in ASNS
                route = f.name.replace(',', '/')
                supernet = get_supernet(fc.get('supernet'))
                roa_entries.append(dict(zip(roa_entries_key, [asn, nettype(route, strict=True), supernet])))
            else:
                assert fc.get('type') in ('ptp',)
        except Exception:
            print("[!] Error while processing file", f)
            raise
    roa_entries.sort(key=lambda l: l['asn'])
    for _net1, _net2 in combinations(roa_entries, 2):
        net1, net2 = sorted([_net1, _net2], key=lambda net: net['prefix'].prefixlen)
        if net1['prefix'].overlaps(net2['prefix']):
            if net1['prefix'] != net2['prefix'] and net1['prefix'].supernet_of(net2['prefix']) \
                and net2['supernet'] == net1['prefix']:
                # This is allowed
                pass
            else:
                print("[!] Error: found", net2, "overlaps", net1)
                raise AssertionError
    return roa_entries

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description='NeoNetwork ROA tool')
    parser.add_argument('-m', '--max', type=int, default=29, help='set ipv4 max prefix length')
    parser.add_argument('-M', '--max6', type=int, default=64, help='set ipv6 max prefix length')
    parser.add_argument('-j', '--json', action='store_true', help='output json')
    parser.add_argument('-o', '--output', default='', help='write output to file')
    parser.add_argument('-4', '--ipv4', action='store_true', help='print ipv4 only')
    parser.add_argument('-6', '--ipv6', action='store_true', help='print ipv6 only')
    args = parser.parse_args()
    if args.max < 0 or args.max6 < 0 or args.max > IPv4Network(0).max_prefixlen or args.max6 > IPv6Network(0).max_prefixlen:
        parser.error('check your max prefix length')

    roa4 = roa6 = list()
    if args.ipv4:
        roa4 = route2roa('route')
    elif args.ipv6:
        roa6 = route2roa('route6', True)
    else:
        roa4 = route2roa('route')
        roa6 = route2roa('route6', True)

    roa4 = [r for r in roa4 if r['prefix'].prefixlen <= args.max or r['prefix'].prefixlen == IPv4Network(0).max_prefixlen]
    roa6 = [r for r in roa6 if r['prefix'].prefixlen <= args.max6]

    for r in roa4:
        if r['prefix'].prefixlen == IPv4Network(0).max_prefixlen:
            r['maxLength'] = IPv4Network(0).max_prefixlen
        else:
            r['maxLength'] = args.max
    for r in roa6:
        r['maxLength'] = args.max6
    for r in (*roa4, *roa6):
        r['prefix'] = r['prefix'].with_prefixlen


    output = ""
    VALID_KEYS = ('asn', 'prefix', 'maxLength')
    if args.json:
        import json, time
        current = int(time.time())
        d_output = {"metadata": {"counts": len(roa4)+len(roa6), "generated": current, "valid": current+14*86400}, "roas": list()}
        for r in (*roa4, *roa6):
            # some preprocessing
            r['asn'] = "AS%d" % r['asn']
        for r in (*roa4, *roa6):
            d_output['roas'].append({k:v for k, v in r.items() if k in VALID_KEYS})
        output = json.dumps(d_output, indent=2)
    else:
        output += "# NeoNetwork ROA tool\n"
        pattern = 'route %s max %d as %d;'
        l_output = list()
        rdict2list = lambda d: [d[k] for k in VALID_KEYS]
        for (asn, prefix, maxlen) in [rdict2list(r) for r in (*roa4, *roa6)]:
            l_output.append(pattern % (prefix, maxlen, asn))
        output += '\n'.join(l_output)
    if not args.output or args.output == '-':
        print(output)
    else:
        Path(args.output).write_text(output)
        print('written to', args.output)
