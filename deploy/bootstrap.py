"""Initialize a fresh, pinned 3x-ui SQLite schema before starting its service."""
import argparse
import ipaddress
import json
import os
from pathlib import Path
import re
import secrets
import sqlite3
import subprocess


def domain(value):
    if not re.fullmatch(r'(?=.{1,253}$)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?', value):
        raise argparse.ArgumentTypeError('Expected a lowercase DNS hostname')
    return value


def xray_template(direct_domain, server_ip):
    blocked_ips = ['geoip:private', '100.64.0.0/10', '169.254.0.0/16', 'fe80::/10', 'fc00::/7']
    outbounds = [
        {'tag': 'direct', 'protocol': 'freedom', 'settings': {
            'domainStrategy': 'UseIP', 'finalRules': [{'action': 'block', 'ip': ['geoip:private']}, {'action': 'allow'}]}},
        {'tag': 'blocked', 'protocol': 'blackhole', 'settings': {}},
    ]
    rules = [{'type': 'field', 'inboundTag': ['api'], 'outboundTag': 'api'}]
    if server_ip:
        address = ipaddress.ip_address(server_ip)
        if not address.is_global:
            raise ValueError('The optional origin IP must be public')
        target = f'[{address}]:443' if address.version == 6 else f'{address}:443'
        origin_cidr = f'{address}/{address.max_prefixlen}'
        blocked_ips.append(origin_cidr)
        outbounds.append({'tag': 'site-https', 'protocol': 'freedom', 'settings': {'redirect': target}})
        rules.append({'type': 'field', 'inboundTag': ['in-17763-tcp'], 'network': 'tcp', 'port': '443',
                      'domain': ['domain:' + direct_domain], 'ip': [origin_cidr], 'outboundTag': 'site-https'})
    rules.extend([
        {'type': 'field', 'inboundTag': ['in-17763-tcp'], 'ip': blocked_ips, 'outboundTag': 'blocked'},
        {'type': 'field', 'ip': ['geoip:private'], 'outboundTag': 'blocked'},
        {'type': 'field', 'protocol': ['bittorrent'], 'outboundTag': 'blocked'},
    ])
    return {
        'api': {'tag': 'api', 'services': ['HandlerService', 'LoggerService', 'StatsService', 'RoutingService']},
        'inbounds': [{'tag': 'api', 'listen': '127.0.0.1', 'port': 62789, 'protocol': 'tunnel',
                      'settings': {'rewriteAddress': '127.0.0.1'}}],
        'log': {'access': 'none', 'loglevel': 'warning'},
        'metrics': {'tag': 'metrics_out', 'listen': '127.0.0.1:11111'},
        'outbounds': outbounds,
        'policy': {'levels': {'0': {'statsUserUplink': True, 'statsUserDownlink': True}},
                   'system': {'statsInboundUplink': True, 'statsInboundDownlink': True}},
        'routing': {'domainStrategy': 'IPOnDemand', 'rules': rules},
        'stats': {},
    }


def run_panel(executable, environment, *args):
    result = subprocess.run([str(executable), 'setting', *args], env=environment,
                            text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=60)
    if result.returncode:
        raise RuntimeError(f'3x-ui initialization failed (exit {result.returncode})')
    return result.stdout


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--host', required=True, type=domain)
    parser.add_argument('--direct-domain', type=domain)
    parser.add_argument('--origin-ip', type=ipaddress.ip_address)
    parser.add_argument('--admin', default='vpn-admin')
    parser.add_argument('--panel-root', required=True, type=Path)
    parser.add_argument('--binary', required=True, type=Path)
    parser.add_argument('--secrets-dir', required=True, type=Path)
    args = parser.parse_args()
    if not re.fullmatch(r'[a-z0-9][a-z0-9._@-]{2,63}', args.admin):
        parser.error('Admin name must be lowercase and 3–64 characters')
    direct = args.direct_domain or args.host
    if args.host != direct and not args.host.endswith('.' + direct):
        parser.error('Direct domain must contain the portal hostname')
    template = xray_template(direct, args.origin_ip)
    db_path = args.panel_root / 'db/x-ui.db'
    if db_path.exists():
        parser.error(f'Existing 3x-ui database: {db_path}')
    os.umask(0o077)
    args.secrets_dir.mkdir(mode=0o700, parents=True, exist_ok=False)
    paths = {kind: args.panel_root / kind for kind in ('db', 'bin', 'log')}
    for path in paths.values():
        path.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ, **{f'XUI_{kind.upper()}_FOLDER': str(path) for kind, path in paths.items()})
    password = secrets.token_urlsafe(32)
    run_panel(args.binary, env, '-username', args.admin, '-password', password,
              '-port', '17761', '-listenIP', '127.0.0.1', '-webBasePath', '/admin/')
    token_output = run_panel(args.binary, env, '-getApiToken', '-tokenName', 'ccvpn-portal', '-tokenScope', 'admin')
    token = re.search(r'(?m)^apiToken:\s*([A-Za-z0-9_-]{20,})\s*$', token_output)
    if not token:
        raise RuntimeError('3x-ui did not return the expected API token format')
    with sqlite3.connect(db_path) as db:
        user_id, = db.execute('select id from users where username=?', (args.admin,)).fetchone()
        for key, value in {
            'subEnable': 'false', 'subListen': '127.0.0.1', 'subPort': '17762',
            'trustedProxyCIDRs': '127.0.0.1/32,::1/128', 'timeLocation': 'Asia/Shanghai',
            'xrayTemplateConfig': json.dumps(template),
        }.items():
            if db.execute('update settings set value=? where key=?', (value, key)).rowcount == 0:
                db.execute('insert into settings(key,value) values(?,?)', (key, value))
        db.execute('''insert into inbounds
            (id,user_id,up,down,total,remark,enable,expiry_time,listen,port,protocol,settings,stream_settings,tag,sniffing)
            values (1,?,0,0,0,'Cheapcoding VPN',1,0,'127.0.0.1',17763,'vless',?,?,'in-17763-tcp',?)''',
                   (user_id, json.dumps({'clients': [], 'decryption': 'none'}),
                    json.dumps({'network': 'ws', 'security': 'none', 'wsSettings': {'path': '/vpn/connect', 'host': ''}}),
                    json.dumps({'enabled': True, 'destOverride': ['tls'], 'routeOnly': True})))
    (args.secrets_dir / 'panel-token').write_text(token[1] + '\n')
    (args.secrets_dir / 'admin-credentials').write_text(f'username={args.admin}\npassword={password}\n')
    (args.secrets_dir / 'portal.env').write_text(
        f'CCVPN_HOST={args.host}\nCCVPN_DIRECT_DOMAIN={direct}\nCCVPN_ADMIN_USERNAME={args.admin}\n')
    print('Initialized fresh 3x-ui database, VLESS inbound 1 and local admin token')


if __name__ == '__main__':
    main()
