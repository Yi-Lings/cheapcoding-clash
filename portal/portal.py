#!/usr/bin/python3
import argparse
import base64
import calendar
import datetime as dt
from decimal import Decimal, InvalidOperation
import hashlib
import html
import http.cookies
import http.server
import json
from http.cookiejar import CookieJar
import os
from pathlib import Path
import re
import secrets
import sqlite3
import sys
import threading
import time
import urllib.parse
import urllib.request
import uuid
from zoneinfo import ZoneInfo
import portal_traffic

HOST = os.environ.get('CCVPN_HOST', 'vpn.example.com').lower()
HOME = 'https://' + HOST
PANEL_BASE = 'http://127.0.0.1:' + os.environ.get('CCVPN_PANEL_PORT', '17761') + '/admin/'
INBOUND_ID = int(os.environ.get('CCVPN_INBOUND_ID', '1'))
SUBSCRIPTION_NAME = os.environ.get('CCVPN_SUBSCRIPTION_NAME', 'Cheapcoding')
PROXY_GROUP_NAME = os.environ.get('CCVPN_GROUP', '代理节点')
PROXY_NODE_NAME = os.environ.get('CCVPN_NODE', '主节点')
DIRECT_DOMAINS = (os.environ.get('CCVPN_DIRECT_DOMAIN', HOST),)
DIRECT_SUFFIXES = ('.' + DIRECT_DOMAINS[0], 'localhost', 'local', 'cn')
LAN_CIDRS = ('127.0.0.0/8', '10.0.0.0/8', '172.16.0.0/12', '192.168.0.0/16',
             '169.254.0.0/16', '::1/128', 'fc00::/7', 'fe80::/10')
HIDDIFY_GEO_BASE = 'https://raw.githubusercontent.com/hiddify/hiddify-geo/rule-set/country/'
# Hiddify exports its routing rules as Dart protobuf JSON, using numeric field tags.
HIDDIFY_RULES = json.dumps({'1': [
    {'1': 0, '2': True, '3': 'Cheapcoding 直连', '4': 1,
     '13': list(LAN_CIDRS), '15': list(DIRECT_DOMAINS), '16': list(DIRECT_SUFFIXES)},
    {'1': 1, '2': True, '3': '中国大陆直连', '4': 1,
     '5': [HIDDIFY_GEO_BASE + 'geosite-cn.srs', HIDDIFY_GEO_BASE + 'geoip-cn.srs']},
]}, ensure_ascii=False, separators=(',', ':'))
HIDDIFY_RULE_LINK = 'hiddify:///settings/routing-options?' + urllib.parse.urlencode({
    'routeRule': base64.b64encode(json.dumps(HIDDIFY_RULES).encode()).decode(),
})
STATE = Path(os.environ.get('CCVPN_STATE', '/var/lib/clash-portal'))
TZ = ZoneInfo('Asia/Shanghai')
SECRET = re.compile(r'[a-z0-9]{48,64}')
SUBSCRIPTION_KEY = re.compile(r'[A-Za-z0-9_-]{16,128}')
OP = threading.Lock()
GATE = threading.BoundedSemaphore(16)
MAX_BODY = 2048
DAY_MS = 86400000
GIB = 1024 ** 3
PANEL_CLIENTS_URL = '/admin/panel/clients'
TRAFFIC_CYCLES = {'never': '不重置', 'hourly': '每小时', 'daily': '每天', 'weekly': '每周', 'monthly': '每月'}
EXPIRY_MODES = {'fixed': '指定到期时间', 'delayed': '首次连接后计时', 'unlimited': '长期有效'}


BRAND_IMAGE = Path(__file__).with_name('cheapcoding-vpn.webp').read_bytes()
BRAND_IMAGE_VERSION = hashlib.sha256(BRAND_IMAGE).hexdigest()[:16]
BRAND_IMAGE_TAG = (
    f'<img class="brand-mark" src="/brand.webp?v={BRAND_IMAGE_VERSION}" '
    'width="31" height="31" alt="" aria-hidden="true">'
)

SEA_ART = Path(__file__).with_name('cheapcoding-starry-sea.webp').read_bytes()
SEA_ART_VERSION = hashlib.sha256(SEA_ART).hexdigest()[:16]

STATIC = Path(__file__).with_name('static')
PORTAL_STYLE = (f':root{{--sea-art:url("/starry-sea.webp?v={SEA_ART_VERSION}")}}\n' +
                (STATIC / 'portal.css').read_text(encoding='utf-8'))
PORTAL_SCRIPT = (STATIC / 'portal.js').read_text(encoding='utf-8')
ADMIN_LIVE_SCRIPT = (STATIC / 'admin-live.js').read_text(encoding='utf-8')
FRONTEND_ASSETS = {
    '/portal.css': (PORTAL_STYLE, 'text/css; charset=utf-8'),
    '/portal.js': (PORTAL_SCRIPT, 'application/javascript; charset=utf-8'),
    '/mountain-pass.webp': ((STATIC / 'mountain-pass.webp').read_bytes(), 'image/webp'),
    '/mountain-pass-mobile.webp': ((STATIC / 'mountain-pass-mobile.webp').read_bytes(), 'image/webp'),
}
FRONTEND_VERSIONS = {
    url: hashlib.sha256(body.encode() if isinstance(body, str) else body).hexdigest()[:16]
    for url, (body, _) in FRONTEND_ASSETS.items()
}
PORTAL_STYLE_TAG = f'<link rel="stylesheet" href="/portal.css?v={FRONTEND_VERSIONS["/portal.css"]}">'
PORTAL_SCRIPT_TAG = f'<script src="/portal.js?v={FRONTEND_VERSIONS["/portal.js"]}" defer></script>'

SKIP_LINK = '<a class="skip-link" href="#main-content">跳到正文</a>'


def page_head(title, metadata=''):
    return ('<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width, initial-scale=1">'
            f'{metadata}<title>{html.escape(title)}</title>{PORTAL_STYLE_TAG}</head>')


class PortalError(Exception):
    pass


def api(path, data=None):
    token = (STATE / 'panel-token').read_text().strip()
    request = urllib.request.Request(
        PANEL_BASE + 'panel/api/' + path,
        data=None if data is None else json.dumps(data).encode(),
        headers={'Authorization': 'Bearer ' + token, 'Content-Type': 'application/json'},
    )
    with urllib.request.urlopen(request, timeout=8) as response:
        result = json.load(response)
    if result.get('success') is not True:
        raise PortalError('panel operation failed')
    return result.get('obj')


def database():
    conn = sqlite3.connect(STATE / 'portal.db', timeout=12)
    conn.row_factory = sqlite3.Row
    return conn


def init():
    with database() as db:
        db.executescript("""
        CREATE TABLE IF NOT EXISTS accounts (
            name TEXT PRIMARY KEY, access_key TEXT UNIQUE NOT NULL,
            subscription TEXT UNIQUE NOT NULL, node_id TEXT UNIQUE NOT NULL
        );
        CREATE TABLE IF NOT EXISTS vouchers (
            digest TEXT PRIMARY KEY, name TEXT REFERENCES accounts(name),
            months INTEGER NOT NULL, valid_until INTEGER NOT NULL,
            claimed_by TEXT, target_ms INTEGER, applied_at INTEGER
        );
        CREATE TABLE IF NOT EXISTS credentials (
            name TEXT PRIMARY KEY REFERENCES accounts(name), salt TEXT NOT NULL, password_hash TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS sessions (
            token TEXT PRIMARY KEY, name TEXT NOT NULL REFERENCES accounts(name), expires INTEGER NOT NULL
        );
        CREATE INDEX IF NOT EXISTS sessions_name ON sessions(name);
        CREATE TABLE IF NOT EXISTS paused_accounts (name TEXT PRIMARY KEY REFERENCES accounts(name));
        CREATE INDEX IF NOT EXISTS vouchers_pending ON vouchers(claimed_by, applied_at);
        """)
        db.execute('begin immediate')
        if 'panel_id' not in {column[1] for column in db.execute('pragma table_info(accounts)')}:
            db.execute('alter table accounts add column panel_id integer')
        db.execute('create unique index if not exists accounts_panel_id on accounts(panel_id)')


def client_details(name):
    return api(client_path('get', name))


def client(name):
    return client_details(name)['client']


def client_path(action, name):
    return 'clients/' + action + '/' + urllib.parse.quote(name, safe='')


def register(name, expiry):
    if not USERNAME.fullmatch(name):
        raise PortalError('invalid account name')
    with database() as db:
        account = db.execute('select * from accounts where name=?', (name,)).fetchone()
    if account is not None:
        return account
    sub, node = secrets.token_hex(28), str(uuid.uuid4())
    api('clients/add', {'client': {
        'email': name, 'id': node, 'subId': sub,
        'enable': True, 'expiryTime': expiry, 'totalGB': 0, 'limitIp': 0,
    }, 'inboundIds': [INBOUND_ID]})
    current = client(name)
    if current['uuid'] != node or current['subId'] != sub:
        raise PortalError('account identifiers do not match panel')
    with database() as db:
        db.execute('insert or ignore into accounts(name,access_key,subscription,node_id,panel_id) values(?,?,?,?,?)',
                   (name, secrets.token_hex(28), sub, node, current['id']))
        return db.execute('select * from accounts where name=?', (name,)).fetchone()


def sync_accounts():
    with OP:
        response = api('clients/list')
        if not isinstance(response, list):
            raise PortalError('暂时无法读取节点账号，请稍后重试。')
        panel = {}
        subscriptions = set()
        nodes = set()
        for item in response:
            current = item.get('client', item)
            if INBOUND_ID not in current.get('inboundIds', []):
                continue
            name, sub, node = current['email'], current['subId'], current['uuid']
            if not USERNAME.fullmatch(name) or not SUBSCRIPTION_KEY.fullmatch(sub) or not node:
                continue
            panel_id = current['id']
            if panel_id in panel or sub in subscriptions or node in nodes:
                raise PortalError('3x-ui 中存在重复订阅凭据，请检查节点配置。')
            panel[panel_id] = current
            subscriptions.add(sub)
            nodes.add(node)
        with database() as db:
            db.execute('begin immediate')
            by_identity = {(row['subId'], row['uuid']): key for key, row in panel.items()}
            local = {}
            for row in db.execute('select * from accounts').fetchall():
                panel_id = row['panel_id']
                if panel_id is None:
                    panel_id = by_identity.get((row['subscription'], row['node_id']))
                if panel_id in panel:
                    local[panel_id] = row
                    continue
                old = row['name']
                for table in ('sessions', 'credentials', 'paused_accounts'):
                    db.execute(f'delete from {table} where name=?', (old,))
                db.execute('delete from vouchers where name=? or claimed_by=?', (old, old))
                db.execute('delete from accounts where name=?', (old,))
            staged = {}
            for panel_id, row in local.items():
                current = panel[panel_id]
                if (row['name'], row['subscription'], row['node_id']) != (
                        current['email'], current['subId'], current['uuid']):
                    marker = ':' + secrets.token_hex(28)
                    rename_account(db, row['name'], marker)
                    db.execute('update accounts set subscription=?, node_id=? where name=?',
                               (marker, marker, marker))
                    staged[panel_id] = marker
                elif row['panel_id'] is None:
                    db.execute('update accounts set panel_id=? where name=?', (panel_id, row['name']))
            for panel_id, current in panel.items():
                name, sub, node = current['email'], current['subId'], current['uuid']
                if panel_id in staged:
                    rename_account(db, staged[panel_id], name)
                    db.execute('update accounts set subscription=?, node_id=?, panel_id=? where name=?',
                               (sub, node, panel_id, name))
                elif panel_id not in local:
                    db.execute('insert into accounts(name,access_key,subscription,node_id,panel_id) values(?,?,?,?,?)',
                               (name, secrets.token_hex(28), sub, node, panel_id))
        return {current['email']: current for current in panel.values()}


def rename_account(db, old, new):
    for table in ('accounts', 'credentials', 'sessions', 'paused_accounts', 'vouchers'):
        db.execute(f'update {table} set name=? where name=?', (new, old))
    db.execute('update vouchers set claimed_by=? where claimed_by=?', (new, old))


def digest(code):
    return hashlib.sha256(code.lower().encode()).hexdigest()


def voucher(name, months, valid_days):
    if not 1 <= months <= 12 or not 1 <= valid_days <= 365:
        raise PortalError('续期月数须为 1–12 个月，兑换码有效期须为 1–365 天。')
    code = secrets.token_hex(24)
    with database() as db:
        if name and not db.execute('select 1 from accounts where name=?', (name,)).fetchone():
            raise PortalError('account not found')
        db.execute('insert into vouchers(digest,name,months,valid_until) values(?,?,?,?)',
                   (digest(code), name, months, int(time.time()) + valid_days * 86400))
    return code


def add_months(ms, months):
    current = dt.datetime.fromtimestamp(ms / 1000, TZ)
    year, month = divmod(current.year * 12 + current.month - 1 + months, 12)
    month += 1
    day = min(current.day, calendar.monthrange(year, month)[1])
    return round(current.replace(year=year, month=month, day=day).timestamp() * 1000)


def redeem(account, code):
    if not re.fullmatch(r'[a-fA-F0-9]{48}', code):
        raise PortalError('兑换码格式有误。')
    name = account['name']
    key = digest(code)
    with OP:
        details = client_details(name)
        current = details['client']
        if not current['enable']:
            raise PortalError('订阅已暂停，请联系管理员后再兑换。')
        now = int(time.time() * 1000)
        with database() as db:
            db.execute('begin immediate')
            row = db.execute('select * from vouchers where digest=?', (key,)).fetchone()
            if not row or (row['name'] and row['name'] != name):
                raise PortalError('兑换码无效。')
            if row['claimed_by'] and row['claimed_by'] != name:
                raise PortalError('兑换码已被使用。')
            if row['applied_at']:
                return '这张兑换码已成功兑换。'
            if row['claimed_by']:
                target = row['target_ms']
            else:
                if row['valid_until'] <= now // 1000:
                    raise PortalError('兑换码已过期。')
                if db.execute('select 1 from vouchers where claimed_by=? and applied_at is null',
                              (name,)).fetchone():
                    raise PortalError('上一次兑换正在确认，请先用原兑换码重试。')
                if current['expiryTime'] <= 0:
                    raise PortalError('此账号无需兑换续期。')
                target = add_months(max(current['expiryTime'], now), row['months'])
                db.execute('update vouchers set claimed_by=?,target_ms=? where digest=?', (name, target, key))
        if current['expiryTime'] < target:
            update_client(details, target, current['enable'])
        with database() as db:
            db.execute('update vouchers set applied_at=? where digest=?', (int(time.time()), key))
        return '兑换成功。请在 Clash 中刷新订阅，更新显示的到期时间。'


def expiration(client_info):
    expiry = client_info['expiryTime']
    if expiry == 0:
        return '长期有效'
    if expiry < 0:
        return '首次连接后 ' + format(Decimal(-expiry) / DAY_MS, '.6g') + ' 天'
    return dt.datetime.fromtimestamp(expiry / 1000, TZ).strftime('%Y-%m-%d %H:%M:%S（北京时间）')


def page(title, body, wide=False, layout='', actions='', *, theme=''):
    main_class = 'admin' if wide else layout or 'narrow'
    body_class = ' class="auth-page"' if theme == 'auth' else ''
    nav = '<nav aria-label="页面操作">' + actions + '</nav>' if actions else ''
    heading = '' if layout == 'dashboard' else '<h1>' + html.escape(title) + '</h1>'
    if main_class == 'narrow':
        content = '<section class="auth-card">' + heading + body + '</section>'
    else:
        content = ('<div class="page-intro">' + heading + '</div>' if heading else '') + body
    return (page_head(title + ' · Cheapcoding') + '<body' + body_class + '>' + SKIP_LINK +
            '<header class="site-header"><div class="site-bar">'
            '<a class="brand" href="/">' + BRAND_IMAGE_TAG + 'Cheapcoding VPN</a>' +
            nav + '</div></header><main id="main-content" tabindex="-1" class="page-content ' + main_class + '">' +
            content + '</main>' + PORTAL_SCRIPT_TAG + '</body></html>')


CLIENT_GUIDE = '''<details class="client-guide surface">
  <summary>客户端下载与使用教程</summary>
  <p><strong>本站只提供 Clash 订阅链接，客户端可自行选择。</strong> 任何能导入 Clash 订阅且支持本站节点协议（VLESS + WebSocket + TLS）的开源客户端都可以使用。</p>
  <p><strong>推荐 Hiddify，是因为它支持主流手机和电脑平台。</strong> 安卓、iOS、Windows、macOS 和 Linux 用户可按设备下载安装。安装后，从「我的订阅」复制「Clash 订阅链接」，导入你选用的客户端。</p>
  <details open>
    <summary>Hiddify · 跨平台推荐</summary>
    <h3>按设备下载</h3>
    <dl class="client-platforms">
      <div><dt>安卓</dt><dd>从 <a href="https://play.google.com/store/apps/details?id=app.hiddify.com">Google Play</a> 安装。也可到 <a href="https://github.com/hiddify/hiddify-app/releases/latest">官方发布页</a> 下载通用 APK。</dd></div>
      <div><dt>iPhone / iPad</dt><dd>在 <a href="https://apps.apple.com/us/app/hiddify-proxy-vpn/id6596777532">App Store</a> 点「获取」。开发者为 Holistic Resilience，客户端下载免费。</dd></div>
      <div><dt>Windows</dt><dd>从 <a href="https://apps.microsoft.com/detail/9ndgwm32bnxk">Microsoft Store</a> 安装，或在 <a href="https://github.com/hiddify/hiddify-app/releases/latest">官方发布页</a> 下载 Windows 安装包。普通 Intel 或 AMD 电脑选 x64。</dd></div>
      <div><dt>macOS</dt><dd>从 <a href="https://github.com/hiddify/hiddify-app/releases/latest">官方发布页</a> 下载 macOS 的 <code>.dmg</code>，打开后把 Hiddify 拖到「应用程序」。Apple 芯片机型也可以从 App Store 安装。</dd></div>
      <div><dt>Linux</dt><dd>按 <a href="https://hiddify.com/app/How-to-install-Hiddify-app/#install-on-linux">官方安装说明</a> 选择适合发行版和 CPU 架构的安装包。</dd></div>
    </dl>
    <h3>导入并连接</h3>
    <ol>
      <li>复制本页上方的完整订阅链接。打开 Hiddify，在首页点 <strong>＋</strong>。</li>
      <li>选择「剪贴板（Clipboard）」。也可以选择「手动（Manually）」，名称填 <strong>Cheapcoding</strong>，在 URL 栏粘贴订阅链接后添加。</li>
      <li>在首页选中刚添加的配置，点击连接按钮。手机首次连接时，按系统提示允许添加 VPN。</li>
    </ol>
    <h3>服务模式：哪些应用的流量进入 Hiddify</h3>
    <p>在「首页 → 快捷设置 → 服务模式」选择，也可以从「设置 → 入站 → 服务模式」进入。桌面软件不走代理时，优先检查这里。</p>
    <dl class="guide-questions">
      <dt>代理（Proxy）</dt><dd>只在电脑上启动本地代理。需要在要使用的应用内手动填写 Hiddify 的本地代理地址和端口。</dd>
      <dt>系统代理（System proxy）</dt><dd>由 Hiddify 设置操作系统的代理。浏览器和遵循系统代理的应用会使用它，部分软件和游戏可能绕过它。</dd>
      <dt>VPN（TUN）</dt><dd>通过虚拟网卡接管系统流量，适合需要让桌面软件和游戏也走代理的用户。Windows 首次启动 VPN 时，按提示允许管理员权限。</dd>
    </dl>
    <h3>路由规则：流量走直连还是代理</h3>
    <p>服务模式和路由可以分别设置。日常可用「VPN（TUN）＋规则分流」。在系统代理下切为全局，仍只有使用系统代理的应用会被接管。</p>
    <p><strong>规则分流。</strong> Hiddify 导入订阅后，需要单独导入一次分流规则。规则开启后，配置的直连域名及其全部子域名、局域网和中国大陆流量直连，其余流量走代理。</p>
    <p class="guide-links"><a href="HIDDIFY_RULE_LINK_PLACEHOLDER">在 Hiddify 中导入分流规则</a><a href="/hiddify-routing.json" download>下载规则文件</a></p>
    <p>如果链接没有打开 App，下载文件后，在 Hiddify 的「设置 → 路由 → ⋯ → 从文件导入规则」中选择该文件，确认后断开并重连。导入会替换 App 里已有的自定义路由，已有自定义规则时可先导出保存。</p>
    <p>导入后确认「Cheapcoding 直连」和「中国大陆直连」已开启。曾导入旧规则的用户，请重新导入新版。</p>
    <p><strong>全局代理。</strong> 如需让整台电脑被接管，先选 VPN（TUN）。在「设置 → 路由」临时关闭启用的直连规则，包括上述两条自定义规则，以及内置的地区、局域网和系统服务直连。若开启了「按应用路由」，也将其关闭，避免应用因名单设置被直连或排除。选好代理节点后断开并重连。要恢复分流，重新打开原先的规则即可。</p>
    <p class="guide-links"><a href="https://hiddify.com/app/How-to-install-Hiddify-app/">Hiddify 官方安装说明</a><a href="https://hiddify.com/app/How-to-use-Hiddify-app/">Hiddify 官方使用说明</a></p>
  </details>
  <details>
    <summary>FlClash · 备选（安卓、Windows、macOS）</summary>
    <p class="guide-links"><a href="https://github.com/chen08209/FlClash/releases/latest">FlClash 官方下载</a></p>
    <dl class="client-platforms">
      <div><dt>安卓</dt><dd>多数手机选 <code>android-arm64-v8a.apk</code>。</dd></div>
      <div><dt>Windows</dt><dd>Intel 或 AMD 电脑通常选 <code>windows-amd64-setup.exe</code>，ARM 电脑选 <code>windows-arm64-setup.exe</code>。</dd></div>
      <div><dt>macOS</dt><dd>Apple M 系列选 <code>macos-arm64.dmg</code>，Intel Mac 选 <code>macos-amd64.dmg</code>。</dd></div>
    </dl>
    <ol>
      <li>打开 FlClash，进入「配置」，点 <strong>＋</strong> →「链接」，粘贴订阅地址并导入。</li>
      <li>选中刚添加的配置。安卓在「仪表盘」开启连接。电脑上浏览器可用「系统代理」，需要接管更多桌面应用时开启「虚拟网卡（TUN）」，并按提示授权。</li>
      <li>在「代理」页的 <strong>美国-新泽西</strong> 分组中，选择 <strong>美国-新泽西 01</strong>。</li>
      <li>「出站模式」默认是「规则」，按目的地分流。要让已接管的流量都走代理，切换到「全局」，再选 <strong>美国-新泽西 01</strong>。要接管其他桌面应用，还需开启 TUN。</li>
    </ol>
  </details>
  <details>
    <summary>续费后怎么更新？连不上怎么办？</summary>
    <dl class="guide-questions">
      <dt>续费后怎么更新？</dt><dd>Hiddify 在首页配置卡片上点刷新；FlClash 在「配置」页更新当前订阅。最新到期时间可在本页查看。</dd>
      <dt>导入失败怎么办？</dt><dd>重新复制本页的完整订阅地址，确认它以 <code>https://</code> 开头且包含 <code>/sub/</code>，并检查订阅状态。</dd>
      <dt>连接后没有效果？</dt><dd>确认已选中刚导入的配置。如果 WireGuard 或其他 VPN 正在运行，先断开，再连接当前客户端。</dd>
      <dt>Windows 的 Hiddify 启动 VPN 时提示 <code>set ipv6 address: Element not found</code>？</dt>
      <dd>
        <p>这个错误发生在 Hiddify 给本机 TUN 虚拟网卡配置 IPv6 地址时。可用同一个订阅临时切到「系统代理」，在浏览器打开一个会走代理的网站。如果可以打开，说明该订阅的节点可用，可继续排查本机 VPN 启动。</p>
        <ol>
          <li>断开 WireGuard 或 Clash 等其他隧道，退出其他代理客户端。从系统托盘彻底退出 Hiddify，再以管理员身份打开，选择 VPN 重试。</li>
          <li>如果「设置 → 路由 → IPv6 路由」已开启，先在 Hiddify 内把它设为「关闭」并重连。这是客户端内的排查项，有些版本即使关闭也可能尝试配置虚拟网卡的 IPv6 地址。</li>
          <li>如果仍报错，检查 Windows 是否通过系统设置或策略禁用了 IPv6。命令提示符中可运行下面的只读命令，将结果和 Hiddify 日志中的报错行发给支持人员。<a href="https://learn.microsoft.com/en-us/troubleshoot/windows-server/networking/configure-ipv6-in-windows">查看微软的 IPv6 配置说明</a>。</li>
        </ol>
        <p><code>reg query "HKLM\\SYSTEM\\CurrentControlSet\\Services\\Tcpip6\\Parameters" /v DisabledComponents</code></p>
        <p>排查期间，浏览器可以临时使用「系统代理」。需要让桌面软件也走代理，可用上面的 FlClash 导入同一订阅并尝试「虚拟网卡（TUN）」。</p>
      </dd>
    </dl>
  </details>
</details>'''.replace('HIDDIFY_RULE_LINK_PLACEHOLDER', html.escape(HIDDIFY_RULE_LINK, quote=True))


def copy_field(label, value, field_id):
    return ('<label for="' + field_id + '">' + html.escape(label) + '</label><div class="copy-line">'
            '<input id="' + field_id + '" type="text" readonly value="' + html.escape(value, quote=True) + '">'
            '<button type="button" class="secondary" data-copy aria-label="复制' + html.escape(label, quote=True) +
            '">复制</button></div><output class="copy-feedback" aria-live="polite"></output>')


def notice(message):
    return '<p class="message" role="status">' + html.escape(message) + '</p>' if message else ''


def subscription(account):
    # A fixed one-node config keeps the TLS endpoint independent of the panel's bind address.
    # Quoted scalars and the flow-style array use JSON syntax, which is valid YAML.
    return (
        'mode: rule\n'
        'proxies:\n'
        '  - name: ' + json.dumps(PROXY_NODE_NAME, ensure_ascii=False) + '\n'
        '    type: vless\n'
        '    server: ' + HOST + '\n'
        '    port: 443\n'
        '    uuid: ' + json.dumps(account['node_id']) + '\n'
        '    tls: true\n'
        '    servername: ' + HOST + '\n'
        '    network: ws\n'
        '    udp: true\n'
        '    packet-encoding: xudp\n'
        '    ws-opts:\n'
        '      path: /connect\n'
        '      headers:\n'
        '        Host: ' + HOST + '\n'
        'proxy-groups:\n'
        '  - name: ' + json.dumps(PROXY_GROUP_NAME, ensure_ascii=False) + '\n'
        '    type: select\n'
        '    proxies: ' + json.dumps([PROXY_NODE_NAME, 'DIRECT'], ensure_ascii=False) + '\n'
        'rules:\n' +
        ''.join(f'  - DOMAIN,{domain},DIRECT\n' for domain in DIRECT_DOMAINS) +
        ''.join(f'  - DOMAIN-SUFFIX,{suffix},DIRECT\n' for suffix in (item.lstrip('.') for item in DIRECT_SUFFIXES)) +
        ''.join(f'  - {"IP-CIDR6" if ":" in cidr else "IP-CIDR"},{cidr},DIRECT,no-resolve\n'
                for cidr in LAN_CIDRS) +
        '  - GEOSITE,CN,DIRECT\n'
        '  - GEOIP,CN,DIRECT,no-resolve\n'
        '  - ' + json.dumps('MATCH,' + PROXY_GROUP_NAME, ensure_ascii=False) + '\n'
    )


COOKIE = '__Host-ccvpn'
FORM_COOKIE = '__Host-ccvpn-form'
POST_FORM = re.compile(r'<form method="post"[^>]*>')
ADMIN_USERNAME = os.environ.get('CCVPN_ADMIN_USERNAME', 'vpn-admin').lower()
SESSION_SECONDS = 7 * 86400
USERNAME = re.compile(r'[^/\x5c\s\x00-\x1f\x7f:]{1,128}')
DUMMY_SALT = bytes.fromhex('c181a3981f8e644850ed17d94d8f83c7')
PASSWORD_WORK = threading.BoundedSemaphore(2)


def password_hash(password, salt):
    with PASSWORD_WORK:
        return hashlib.scrypt(password.encode(), salt=salt, n=32768, r=8, p=1, maxmem=64 * 1024 * 1024).hex()


def authenticate(name, password):
    if not USERNAME.fullmatch(name) or len(password) > 128:
        return None
    with database() as db:
        row = db.execute('select * from credentials where name=?', (name,)).fetchone()
    salt = bytes.fromhex(row['salt']) if row else DUMMY_SALT
    calculated = password_hash(password, salt)
    if row and secrets.compare_digest(calculated, row['password_hash']):
        return name
    return None


def change_password(name, session_token, form):
    current = form.get('current', '')
    new = form.get('password', '')
    if not 12 <= len(new) <= 128 or new != form.get('confirmation'):
        raise PortalError('两次新密码须一致，长度为 12–128 位。')
    if len(current) > 128:
        raise PortalError('当前密码错误。')
    with database() as db:
        record = db.execute('select salt,password_hash from credentials where name=?', (name,)).fetchone()
    if not record or not secrets.compare_digest(password_hash(current, bytes.fromhex(record['salt'])),
                                                record['password_hash']):
        raise PortalError('当前密码错误。')
    if secrets.compare_digest(current.encode(), new.encode()):
        raise PortalError('新密码需与当前密码不同。')
    salt = secrets.token_bytes(16)
    hashed = password_hash(new, salt)
    now = int(time.time())
    fresh = secrets.token_hex(32)
    with database() as db:
        db.execute('begin immediate')
        session = db.execute('select 1 from sessions where token=? and name=? and expires>?',
                             (digest(session_token), name, now)).fetchone()
        if not session:
            raise PortalError('登录已过期，请重新登录后修改密码。')
        result = db.execute('update credentials set salt=?,password_hash=? where name=? and salt=? and password_hash=?',
                            (salt.hex(), hashed, name, record['salt'], record['password_hash']))
        if result.rowcount != 1:
            raise PortalError('密码已变更，请重新登录。')
        db.execute('delete from sessions where name=?', (name,))
        db.execute('insert into sessions values(?,?,?)', (digest(fresh), name, now + SESSION_SECONDS))
    return fresh


def start_session(name, existing=None):
    token = secrets.token_hex(32)
    now = int(time.time())
    with database() as db:
        db.execute('delete from sessions where expires<=? or token=?', (now, digest(existing or '')))
        db.execute('insert into sessions values(?,?,?)', (digest(token), name, now + SESSION_SECONDS))
    return token


def cookie_header(token='', lifetime=0):
    return f'{COOKIE}={token}; Path=/; Max-Age={lifetime}; Secure; HttpOnly; SameSite=Lax'


def landing_page():
    steps = (
        ('登录账号', '首次使用时，先通过专属激活链接设置密码。'),
        ('复制订阅链接', '登录后进入「我的订阅」，复制完整的 Clash 订阅链接。'),
        ('导入客户端', '将链接导入兼容客户端，选中订阅并连接。'),
    )
    items = ''.join(f'<li><h3>{heading}</h3><p>{detail}</p></li>' for heading, detail in steps)
    desktop_art = '/mountain-pass.webp?v=' + FRONTEND_VERSIONS['/mountain-pass.webp']
    mobile_art = '/mountain-pass-mobile.webp?v=' + FRONTEND_VERSIONS['/mountain-pass-mobile.webp']
    metadata = '<meta name="description" content="Cheapcoding VPN 支持多端连接、规则分流和自助订阅管理。">'
    return (page_head('Cheapcoding VPN', metadata) + '<body class="landing-page">' + SKIP_LINK +
            '<header class="site-header"><div class="site-bar">'
            '<a class="brand" href="/">' + BRAND_IMAGE_TAG + 'Cheapcoding VPN</a>'
            '<nav aria-label="页面导航"><a class="landing-nav-guide" href="#getting-started">使用方法</a>'
            '<a class="button-link button-link-secondary" href="/login">登录</a></nav></div></header>'
            '<main class="landing-main" id="main-content" tabindex="-1">'
            '<section class="landing-hero" aria-labelledby="landing-title">'
            '<div class="landing-hero-inner"><h1 id="landing-title">你的 Clash 订阅</h1>'
            '<p>本站只提供 Clash 订阅链接。支持导入 Clash 订阅且兼容本站节点协议的开源客户端均可使用。</p>'
            '<div class="actions"><a class="button-link" href="/login">进入订阅面板<span aria-hidden="true"> →</span></a>'
            '<a class="landing-nav-guide" href="#getting-started">查看使用方法</a></div></div>'
            '<picture class="landing-art">'
            f'<source media="(max-width: 42rem)" srcset="{mobile_art}" width="690" height="960">'
            f'<img src="{desktop_art}" width="1536" height="1024" alt="" fetchpriority="high">'
            '</picture></section>'
            '<section class="landing-setup" id="getting-started" aria-labelledby="setup-title">'
            '<div><h2 id="setup-title">如何开始使用</h2>'
            '<p>首次安装推荐 Hiddify，它支持 Android、iOS、Windows、macOS 和 Linux。客户端下载和分流配置见面板内的「使用教程」。</p></div>'
            '<ol class="landing-steps">' + items + '</ol>' +
            '</section></main></body></html>')


def login_page(message='', name='', two_factor=False):
    message_html = notice(message)
    code = ('<label>动态验证码<input name="code" inputmode="numeric" pattern="[0-9]{6}" '
            'maxlength="6" autocomplete="one-time-code" required></label>') if two_factor else ''
    return page('登录订阅平台', message_html +
                '<form method="post" action="/login"><label>用户名'
                '<input name="username" value="' + html.escape(name, quote=True) +
                '" autocomplete="username" required maxlength="128"></label>'
                '<label>密码<input name="password" type="password" autocomplete="current-password" required maxlength="128"></label>'
                + code + '<button>登录</button></form>'
                '<p class="auth-note">首次使用请打开专属激活链接设置密码。忘记密码可联系管理员。</p>',
                theme='auth', actions='<a class="auth-back" href="/">返回首页</a>')


def panel_login(password, code, remote_ip):
    if not password or len(password) > 128 or (code and not re.fullmatch(r'[0-9]{6}', code)):
        return '', False
    jar = CookieJar()
    http = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
    base = PANEL_BASE
    headers = {
        'Host': HOST, 'Origin': HOME, 'X-Forwarded-Proto': 'https',
        'X-Forwarded-For': remote_ip, 'X-Real-IP': remote_ip,
        'X-Requested-With': 'XMLHttpRequest', 'Content-Type': 'application/json',
    }
    with http.open(urllib.request.Request(base + 'csrf-token', headers=headers), timeout=5) as reply:
        headers['X-CSRF-Token'] = json.load(reply)['obj']
    def post(path, values):
        request = urllib.request.Request(base + path, data=json.dumps(values).encode(), headers=headers)
        with http.open(request, timeout=5) as reply:
            return json.load(reply)
    if not code and post('getTwoFactorEnable', {}).get('obj') is True:
        return '', True
    result = post('login', {'username': ADMIN_USERNAME, 'password': password, 'twoFactorCode': code})
    if result.get('success') is not True:
        return '', bool(code)
    for item in jar:
        if item.name == '3x-ui' and item.path == '/admin/':
            return f'3x-ui={item.value}; Path=/admin/; Secure; HttpOnly; SameSite=Lax', False
    raise RuntimeError('panel session was not issued')


def activation_page(name, message=''):
    notice = '<p class="message">' + html.escape(message) + '</p>' if message else ''
    return page('设置登录密码', notice + '<p>用户名 <strong>' + html.escape(name) +
                '</strong></p><form method="post"><label>新密码'
                '<input name="password" type="password" minlength="12" maxlength="128" autocomplete="new-password" required></label>'
                '<label>再次输入密码<input name="confirmation" type="password" minlength="12" maxlength="128" autocomplete="new-password" required></label>'
                '<p><small>密码至少 12 位。设置后可在登录页使用。</small></p><button>设置密码并登录</button></form>', theme='auth')


def readable_traffic(size):
    for unit in ('B', 'KiB', 'MiB', 'GiB', 'TiB'):
        if size < 1024 or unit == 'TiB':
            return f'{size:.1f} {unit}' if unit != 'B' else f'{size} B'
        size /= 1024


def client_status(current, traffic):
    if not current['enable']:
        return '已停用', 'status inactive'
    expiry = current['expiryTime']
    if 0 < expiry <= int(time.time() * 1000):
        return '已到期', 'status inactive'
    total = current['totalGB']
    if total and int(traffic.get('up') or 0) + int(traffic.get('down') or 0) >= total:
        return '流量已用完', 'status inactive'
    if traffic.get('enable') is False:
        return '流量状态停用', 'status inactive'
    return ('待首次连接' if expiry < 0 else '有效'), 'status'


def subscription_table(accounts, panel_clients, empty_message="暂无账号"):
    rows = []
    for account in accounts:
        name = html.escape(account['name'])
        current = panel_clients.get(account['name'])
        if not current or current['uuid'] != account['node_id'] or current['subId'] != account['subscription']:
            continue
        traffic = current.get('traffic') or {}
        status, state_class = client_status(current, traffic)
        up, down = int(traffic.get('up') or 0), int(traffic.get('down') or 0)
        quota = current['totalGB']
        item_id = str(account['panel_id'])
        activation = ''
        if not account['activated']:
            activation = copy_field('首次激活链接', HOME + '/account/' + account['access_key'], 'activate-' + item_id)
        rows.append(
            '<tr role="row" data-account-id="' + item_id + '"><td role="cell" class="account-name">' + name +
            '</td><td role="cell"><span class="' + state_class + '">' + status + '</span></td>'
            '<td role="cell"><span class="mobile-cell-label" aria-hidden="true">到期</span>' +
            expiration(current) + '</td><td role="cell" class="usage"><span class="mobile-cell-label" aria-hidden="true">上传</span>' +
            readable_traffic(up) + '</td><td role="cell" class="usage"><span class="mobile-cell-label" aria-hidden="true">下载</span>' +
            readable_traffic(down) + '</td><td role="cell" class="usage"><span class="mobile-cell-label" aria-hidden="true">已用 / 限额</span>' +
            readable_traffic(up+down) + ' / ' + (readable_traffic(quota) if quota else '不限额') +
            '</td><td role="cell" class="row-actions"><a class="edit-link" href="/admin/operations/?client=' + item_id +
            '">编辑</a><details><summary>链接</summary>' +
            copy_field('Clash 订阅链接', HOME + '/sub/' + account['subscription'], 'subscribe-' + item_id) +
            activation + '</details></td></tr>')
    body = ''.join(rows) if rows else f'<tr role="row"><td role="cell" colspan="7">{empty_message}</td></tr>'
    return ('<section class="surface table-panel"><div class="table-title"><h2>账号列表（' + str(len(rows)) +
            '）</h2></div><div class="table-scroll" role="region" aria-label="账号和流量" tabindex="0">'
            '<table class="account-table" role="table"><thead role="rowgroup"><tr role="row"><th scope="col">用户名</th><th scope="col">状态</th>'
            '<th scope="col">到期时间</th><th scope="col">上传</th><th scope="col">下载</th>'
            '<th scope="col">已用 / 限额</th><th scope="col">操作</th></tr></thead><tbody role="rowgroup">' +
            body + '</tbody></table></div></section>')


def admin_subscriptions(panel_id=None):
    panel_clients = sync_accounts()
    with database() as db:
        accounts = db.execute(
            'select a.*, c.name is not null as activated '
            'from accounts a left join credentials c on a.name=c.name order by lower(a.name)'
        ).fetchall()
    known = {current['id']: name for name, current in panel_clients.items()}
    shown = accounts if panel_id is None else [row for row in accounts if row['panel_id'] == panel_id]
    empty = '暂无账号' if panel_id is None else '该账号已删除或不再属于此节点。'
    return subscription_table(shown, panel_clients, empty), [row['name'] for row in accounts], known


ADMIN_PAGES = {
    'traffic': ('流量仪表盘', '/admin/operations/traffic'),
    'accounts': ('账号管理', '/admin/operations/'),
    'create': ('开通账号', '/admin/operations/create'),
    'vouchers': ('生成兑换码', '/admin/operations/vouchers'),
}
USER_PAGES = {
    'traffic': ('流量仪表盘', '/dashboard'),
    'subscription': ('我的订阅', '/subscription'),
    'redeem': ('兑换续期', '/redeem'),
    'password': ('修改密码', '/password'),
    'guide': ('使用教程', '/guide'),
}


PORTAL_ICONS = {
    'traffic': 'M3 3v18h18M8 16v-4m5 4V7m5 9v-7',
    'accounts': 'M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2m14-14a4 4 0 0 1 0 8m6 6v-2a4 4 0 0 0-3-4M13 7a4 4 0 1 1-8 0 4 4 0 0 1 8 0Z',
    'create': 'M15 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2m17-13v8m-4-4h8M12 7a4 4 0 1 1-8 0 4 4 0 0 1 8 0Z',
    'vouchers': 'M3 6h18v4a2 2 0 1 0 0 4v4H3v-4a2 2 0 1 0 0-4zm12 0v2m0 3v2m0 3v2',
    'subscription': 'M10 13a4 4 0 0 0 6 0l4-4a4 4 0 0 0-6-6l-2 2m2 6a4 4 0 0 0-6 0l-4 4a4 4 0 0 0 6 6l2-2',
    'redeem': 'M3 8h18v4H3zm2 4v9h14v-9m-7-4v13m0-13H8a2 2 0 1 1 2-3zm0 0h4a2 2 0 1 0-2-3z',
    'password': 'M6 10h12v11H6zm2 0V7a4 4 0 0 1 8 0v3m-4 5v2',
    'guide': 'M12 5v16M3 4h5a4 4 0 0 1 4 4 4 4 0 0 1 4-4h5v15h-5a4 4 0 0 0-4 2 4 4 0 0 0-4-2H3z',
    'menu': 'M4 6h16M4 12h16M4 18h16',
}


def portal_icon(key):
    return ('<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" '
            'stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false">'
            f'<path d="{PORTAL_ICONS[key]}"></path></svg>')


def portal_app(title, body, section, admin=False, username=''):
    pages = ADMIN_PAGES if admin else USER_PAGES
    links = ''.join(
        f'<a href="{url}"' + (' aria-current="page"' if key == section else '') +
        '>' + portal_icon(key) + f'<span>{label}</span></a>'
        for key, (label, url) in pages.items())
    if admin:
        actions = admin_navigation()
    else:
        actions = ('<span class="muted">' + html.escape(username) + '</span>'
                   '<form method="post" action="/logout"><button class="secondary">退出登录</button></form>')
    navigation = ('<div class="portal-sidebar-head"><a class="brand" href="' + pages['traffic'][1] +
                  '">' + BRAND_IMAGE_TAG + 'Cheapcoding VPN</a></div>'
                  '<nav class="portal-menu" aria-label="功能菜单">' + links + '</nav>')
    return (page_head(title + ' · Cheapcoding') + '<body>' + SKIP_LINK + '<div class="portal-shell">'
            '<aside class="portal-sidebar">' + navigation + '</aside>'
            '<dialog class="portal-drawer" id="portal-drawer" aria-label="功能菜单" closedby="any">' +
            navigation + '</dialog>'
            '<main class="portal-main" id="main-content" tabindex="-1">'
            '<header class="portal-top"><div class="portal-heading">'
            '<button type="button" class="portal-menu-open secondary" aria-controls="portal-drawer" '
            'aria-haspopup="dialog" aria-expanded="false" aria-label="打开导航">' + portal_icon('menu') + '</button>'
            '<h1>' + html.escape(title) + '</h1></div><div class="actions">' + actions + '</div></header>'
            '<div class="portal-content">' + body + '</div></main></div>' + PORTAL_SCRIPT_TAG + '</body></html>')


def traffic_presets(today):
    recent = lambda days: today - dt.timedelta(days=days-1)
    month_start = today.replace(day=1)
    previous_end = month_start - dt.timedelta(days=1)
    return (
        ('今天', today, today),
        ('昨天', recent(2), recent(2)),
        ('最近 7 天', recent(7), today),
        ('最近 14 天', recent(14), today),
        ('最近 30 天', recent(30), today),
        ('本月', month_start, today),
        ('上月', previous_end.replace(day=1), previous_end),
    )


def traffic_dates(query, today):
    default = int(query.get('days', ['7'])[0]) if query.get('days', ['7'])[0] in ('7', '14', '30') else 7
    fallback = (today - dt.timedelta(days=default-1), today)
    if 'start_date' not in query and 'end_date' not in query:
        return fallback, ''
    start, end = (query.get(key, [''])[0] for key in ('start_date', 'end_date'))
    if all(re.fullmatch(r'\d{4}-\d{2}-\d{2}', value) for value in (start, end)):
        try:
            first, last = dt.date.fromisoformat(start), dt.date.fromisoformat(end)
            if first <= last <= today:
                return (first, last), ''
        except ValueError:
            pass
    return fallback, '请选择有效的起止日期，结束日期须在开始日期当天或之后，且不能晚于今天。'


def traffic_date_picker(first, last, today, presets, invalid=False):
    title = str(first) if first == last else f'{first} 至 {last}'
    buttons = ''.join(
        f'<button type="button" data-start="{start}" data-end="{end}" '
        f'aria-pressed="{str((start, end) == (first, last)).lower()}">{label}</button>'
        for label, start, end in presets)
    opened = ' open' if invalid else ''
    return (
        '<div class="range-control"><span class="range-caption">日期范围</span>'
        f'<details class="range-picker"{opened}><summary>{title}</summary><div class="range-popover">'
        '<div class="range-presets" hidden>' + buttons + '</div><div class="range-dates">'
        f'<label>开始日期<input type="date" name="start_date" value="{first}" max="{today}" required></label>'
        f'<label>结束日期<input type="date" name="end_date" value="{last}" max="{today}" required></label>'
        '</div><button type="submit">应用</button></div></details></div>')


def traffic_pagination(first, last, panel_id, admin, page_index, pages, shown):
    if pages <= 1:
        return ''
    params = {'start_date': str(first), 'end_date': str(last)}
    if admin and panel_id is not None:
        params['client'] = panel_id
    def link(label, index):
        url = html.escape('?' + urllib.parse.urlencode({**params, 'page': index}), quote=True)
        return f'<a href="{url}">{label}</a>'
    newer = link('较新的日期', page_index-1) if page_index > 1 else ''
    older = link('较早的日期', page_index+1) if page_index < pages else ''
    return (f'<nav class="traffic-pages" aria-label="流量明细分页">{newer}{older}'
            f'<span class="muted">{shown[0]} 至 {shown[1]} · 第 {page_index} / {pages} 页</span></nav>')


def user_filter_link(params, client_id=None, key='client'):
    if client_id is not None:
        params = {**params, key: client_id}
    return html.escape('?' + urllib.parse.urlencode(params), quote=True) if params else '?'


def account_filter_link(client_id=None):
    return user_filter_link({}, client_id, key='filter_client')


def account_filter_id(query):
    value = query.get('filter_client', [''])[0]
    return int(value) if re.fullmatch(r'[1-9][0-9]{0,17}', value) else None


def user_filter(known, active, selected, link):
    current = html.escape(known.get(selected, f'#{selected}') if selected is not None else '', quote=True)
    clear = (f'<a class="traffic-user-clear" href="{link()}" aria-label="清除用户筛选" title="清除用户筛选">×</a>'
             if selected is not None else '')
    field = (
        '<div class="traffic-user-field"><label for="traffic-user-search">用户</label>'
        '<div class="traffic-user-input"><input type="search" id="traffic-user-search" '
        f'value="{current}" placeholder="全部用户 · 搜索用户名或 ID" autocomplete="off" '
        'aria-controls="traffic-user-results" spellcheck="false">' + clear +
        '<button type="button" class="traffic-user-toggle" aria-label="选择用户" '
        'aria-controls="traffic-user-results" aria-expanded="false" hidden></button></div></div>')
    results = ('<ul id="traffic-user-results" class="traffic-user-results" aria-label="用户列表" hidden>'
               + user_filter_options(known, active, selected, link) + '</ul>')
    return field, results


def user_filter_options(known, active, selected, link):
    all_selected = ' aria-current="true"' if selected is None else ''
    rows = [f'<li><a href="{link()}" data-all-users{all_selected}>全部用户</a></li>']
    for key, name in sorted(known.items(), key=lambda item: (item[0] not in active, item[1].casefold())):
        suffix = ' · 历史账号' if key not in active else ''
        current_option = ' aria-current="true"' if key == selected else ''
        rows.append(f'<li hidden><a href="{link(key)}" data-client="{key}"{current_option}><span>{html.escape(name)}</span>'
                    f'<small>#{key}{suffix}</small></a></li>')
    return ''.join(rows) + '<li class="traffic-user-empty" role="status" hidden>没有匹配的用户</li>'


def traffic_dashboard(query, username='', panel_id=None, admin=False):
    today = portal_traffic.calendar_day(time.time())
    (first, last), error = traffic_dates(query, today)
    user_field = user_results = ''
    if admin:
        clients = sync_accounts()
        with portal_traffic.database(STATE) as db:
            known = {row['id']: row['name'] for row in db.execute('SELECT id,name FROM clients')}
        active = {client['id']: client['email'] for client in clients.values()}
        known.update(active)
        selected = query.get('client', [''])[0]
        if selected:
            if not re.fullmatch(r'[1-9][0-9]{0,17}', selected) or int(selected) not in known:
                raise PortalError('请选择有效的账号。')
            panel_id = int(selected)
        params = {'start_date': first, 'end_date': last}
        user_field, user_results = user_filter(known, active, panel_id, lambda value=None: user_filter_link(params, value))
    span = (last-first).days + 1
    per_page = 31
    pages = (span + per_page - 1) // per_page
    raw_page = query.get('page', ['1'])[0]
    page_index = min(int(raw_page), pages) if re.fullmatch(r'[1-9][0-9]{0,6}', raw_page) else 1
    shown_last = last - dt.timedelta(days=(page_index-1)*per_page)
    shown_first = shown_last - dt.timedelta(days=min(per_page-1, (shown_last-first).days))
    start, end, totals, rows = portal_traffic.report(
        STATE, first, last, panel_id, (shown_first, shown_last))
    has_data = totals is not None
    totals = totals or (0, 0)
    selected_client = (f'<input type="hidden" name="client" value="{panel_id}">'
                       if admin and panel_id is not None else '')
    date_form = ('<form class="traffic-filters" method="get">' + selected_client +
                 traffic_date_picker(first, last, today, traffic_presets(today), invalid=bool(error)) + '</form>')
    filters = '<div class="traffic-toolbar">' + user_field + date_form + user_results + '</div>'
    if error:
        filters = '<p class="range-warning" role="alert">' + html.escape(error) + '</p>' + filters
    metrics = ''.join(
        '<div class="surface metric"><dt>' + label + '</dt><dd>' +
        (readable_traffic(value) if has_data else '—') + '</dd></div>'
        for label, value in zip(('上传', '下载', '总流量'), [*totals, sum(totals)]))
    caption = '北京时间'
    if start is not None:
        time_format = '%m-%d %H:%M'
        start_text, end_text = [dt.datetime.fromtimestamp(value, TZ).strftime(time_format) for value in (start, end)]
        caption += f' · 从 {start_text} 开始采集 · 最近更新 {end_text}'
    if start is None:
        caption += ' · 等待首次采集'
    shortcuts = ('' if admin else
                 '<div class="actions"><a class="button-link" href="/subscription">我的订阅<span aria-hidden="true"> →</span></a>'
                 '<a class="button-link button-link-secondary" href="/guide">使用教程</a></div>')
    body = ('<section class="surface traffic-overview" aria-labelledby="traffic-overview-title">'
            '<div class="overview-heading"><h2 id="traffic-overview-title">流量概览</h2>' + shortcuts +
            '</div>' + filters + '<dl class="metric-grid traffic-metrics">' + metrics + '</dl></section>')
    body += '<section class="surface traffic-card"><h2>每日上下行</h2><p class="traffic-caption">' + caption + '</p>'
    body += ('<div class="traffic-legend"><span><i class="traffic-key"></i>上传</span>'
             '<span><i class="traffic-key down"></i>下载</span></div>' + traffic_bars(rows) + '</section>')
    pagination = traffic_pagination(first, last, panel_id, admin, page_index, pages, (shown_first, shown_last))
    body += '<section class="surface traffic-card"><h2>每日明细</h2>' + traffic_details(rows) + pagination + '</section>'
    return portal_app('流量仪表盘', body, 'traffic', admin=admin, username=username)


def traffic_bars(rows):
    ceiling = max((max(up or 0, down or 0) for _, up, down, _ in rows), default=0) or 1
    bars = []
    for day, up, down, status in rows:
        values = (('—',) * 3 if up is None else
                  (readable_traffic(up), readable_traffic(down), readable_traffic(up + down)))
        meta = {'date': day, 'up': values[0], 'down': values[1], 'total': values[2], 'status': status}
        attributes = ' '.join(f'data-{key}="{html.escape(value, quote=True)}"' for key, value in meta.items())
        label = f'{day}，上传 {values[0]}，下载 {values[1]}，合计 {values[2]}，{status}'
        strokes = ''.join(
            f'<span class="traffic-bar {direction}" style="--bar-height:{max(1, amount * 100 / ceiling):.3f}%"></span>'
            for direction, amount in (('up', up), ('down', down)) if amount)
        style = ' unobserved' if up is None else ''
        bars.append(f'<button type="button" class="traffic-day{style}" {attributes} '
                    f'aria-label="{html.escape(label, quote=True)}"><span class="traffic-bars" aria-hidden="true">{strokes}</span>'
                    f'<time datetime="{day}" aria-hidden="true">{day[5:]}</time></button>')
    return ('<div class="traffic-interactive"><div class="traffic-scroll" aria-label="每日上下行柱状图">'
            f'<div class="traffic-chart" style="--chart-min:{len(rows)*40}px">' +
            ''.join(bars) + '</div></div>'
            '<div class="traffic-tooltip" aria-hidden="true" hidden>'
            '<strong data-field="date"></strong><dl>'
            '<div><dt><i class="traffic-key"></i>上传</dt><dd data-field="up"></dd></div>'
            '<div><dt><i class="traffic-key down"></i>下载</dt><dd data-field="down"></dd></div>'
            '<div class="traffic-tooltip-total"><dt>合计</dt><dd data-field="total"></dd></div>'
            '</dl><span class="traffic-tooltip-status" data-field="status"></span></div></div>')


def traffic_details(rows):
    entries = []
    for day, up, down, status in reversed(rows):
        values = (readable_traffic(up), readable_traffic(down)) if up is not None else ('—', '—')
        entries.append(f'<tr><td>{day}</td><td>{values[0]}</td><td>{values[1]}</td><td>{status}</td></tr>')
    return ('<table class="traffic-data"><thead><tr><th scope="col">日期</th><th scope="col">上传</th>'
            '<th scope="col">下载</th><th scope="col">采集状态</th></tr></thead><tbody>' +
            ''.join(entries) + '</tbody></table>')


def user_page(section, name, account=None, current=None, message=''):
    forms = {
        'redeem': '<form method="post" action="/redeem"><label>兑换码<input name="code" required maxlength="48" '
                  'autocomplete="off" spellcheck="false" autocapitalize="none"></label><button>兑换续期</button></form>',
        'password': '<form method="post" action="/password">'
                    '<label>当前密码<input type="password" name="current" autocomplete="current-password" maxlength="128" required></label>'
                    '<label>新密码<input type="password" name="password" autocomplete="new-password" minlength="12" maxlength="128" required></label>'
                    '<label>确认新密码<input type="password" name="confirmation" autocomplete="new-password" minlength="12" maxlength="128" required></label>'
                    '<button>保存密码</button></form>',
    }
    if section == 'subscription':
        traffic = current.get('traffic') or {}
        status, state_class = client_status(current, traffic)
        total = int(traffic.get('up') or 0) + int(traffic.get('down') or 0)
        limit = current['totalGB']
        quota = readable_traffic(limit) if limit else '不限额'
        meter = (f'<meter min="0" max="{limit}" value="{min(total, limit)}" aria-label="已用流量占额度比例"></meter>'
                 if limit else '')
        overview = ('<section class="surface subscription-overview"><div class="overview-heading">'
                    '<h2>订阅概览</h2><span class="' + state_class + '">' + status + '</span></div>'
                    '<dl class="subscription-facts"><div><dt>到期时间</dt><dd>' + expiration(current) + '</dd></div>'
                    '<div><dt>累计使用</dt><dd>' + readable_traffic(total) + ' / ' + quota + '</dd>' + meter +
                    '</div></dl><a class="button-link button-link-secondary" href="/redeem">兑换续期</a></section>')
        connection = ('<section class="surface subscription-connection"><h2>导入客户端</h2>'
                      '<p>复制下方链接，粘贴到客户端的订阅地址栏。</p>' +
                      copy_field('订阅链接', HOME + '/sub/' + account['subscription'], 'subscription-url') +
                      '<a href="/guide">查看客户端下载和配置教程<span aria-hidden="true"> →</span></a></section>')
        return portal_app(USER_PAGES[section][0], notice(message) + '<div class="subscription-layout">' +
                          overview + connection + '</div>', section, username=name)
    elif section == 'guide':
        guide = CLIENT_GUIDE.replace('class="client-guide surface">', 'class="client-guide surface" open>', 1)
        guide = guide.replace('从本页上方复制「Clash 订阅链接」', '前往<a href="/subscription">我的订阅</a>复制链接')
        return portal_app(USER_PAGES[section][0], guide, section, username=name)
    else:
        content = forms[section]
    return portal_app(USER_PAGES[section][0], notice(message) + '<section class="surface panel-form">' + content +
                      '</section>', section, username=name)


def portal_admin_page(result='', code='', section='accounts', panel_id=None):
    months = '<label>续期月数<input type="number" name="months" min="1" max="12" value="1" required></label>'
    if section == 'create':
        body = ('<section class="surface panel-form"><form method="post" action="/admin/operations/create">'
                '<input type="hidden" name="action" value="create">'
                '<label>用户名<input name="name" maxlength="128" required></label>'
                '<label>首次有效月数<input type="number" name="months" min="1" max="12" value="1" required></label>'
                '<button>开通账号</button></form></section>')
    elif section == 'vouchers':
        issued = ('<div class="voucher-output">' + copy_field('通用一次性兑换码', code, 'issued-code') +
                  '<p class="muted">请复制保存。离开本页后无法再次查看原码。</p></div>') if code else ''
        body = ('<section class="surface panel-form">' + issued +
                '<form method="post" action="/admin/operations/vouchers"><input type="hidden" name="action" value="voucher">'
                '<div class="field-row">' + months +
                '<label>有效天数<input type="number" name="days" min="1" max="365" value="30" required></label></div>'
                '<button>生成一次性兑换码</button></form></section>')
    else:
        cards, names, known = admin_subscriptions(panel_id)
        field, results = user_filter(known, known, panel_id, account_filter_link)
        options = ''.join(
            '<option' + (' selected' if name == known.get(panel_id) else '') + '>' + html.escape(name) + '</option>'
            for name in names)
        if panel_id is not None and panel_id not in known:
            options = '<option value="" selected disabled>请选择账号</option>' + options
        body = ('<div class="traffic-toolbar">' + field + results + '</div>'
                '<div id="live-subscriptions">' + cards + '</div>'
                '<details class="surface panel-form"><summary>续期与停用</summary>'
                '<form method="post" action="/admin/operations/' + (account_filter_link(panel_id) if panel_id else '') + '">'
                '<div class="field-row"><label>账号<select name="name" required>' + options + '</select></label>' + months +
                '</div><div class="actions"><button name="action" value="extend">续期</button>'
                '<button class="danger" name="action" value="pause">停用</button>'
                '<button class="secondary" name="action" value="resume">恢复</button></div></form></details>'
                '<script src="/admin/operations/live.js" defer></script>')
    return portal_app(ADMIN_PAGES[section][0], notice(result) + body, section, admin=True)


def admin_navigation():
    return '<a href="' + PANEL_CLIENTS_URL + '" target="_blank" rel="noopener">打开 3x-ui ↗</a>'


def find_panel_client(panel_clients, panel_id):
    for current in panel_clients.values():
        if current['id'] == panel_id:
            return current
    raise PortalError('该客户端已删除或不再属于此订阅节点。')


def client_revision(current):
    fields = {key: value for key, value in current.items() if key != 'traffic'}
    return hashlib.sha256(json.dumps(fields, sort_keys=True).encode()).hexdigest()


def editor_input(label, field, value, attributes=''):
    return (f'<label>{label}<input name="{field}" value="{html.escape(str(value), quote=True)}" '
            f'{attributes}></label>')


def editor_select(label, field, choices, selected):
    options = ''.join(
        f'<option value="{key}"' + (' selected' if key == selected else '') + f'>{value}</option>'
        for key, value in choices.items())
    return f'<label>{label}<select name="{field}">{options}</select></label>'


def client_editor_page(current, message=''):
    expiry = current['expiryTime']
    expiry_mode = 'fixed' if expiry > 0 else 'delayed' if expiry < 0 else 'unlimited'
    date_value = (dt.datetime.fromtimestamp(expiry / 1000, TZ).isoformat(timespec='milliseconds')[:-6]
                  if expiry > 0 else '')
    days = format(Decimal(-expiry) / DAY_MS, 'f') if expiry < 0 else ''
    quota = format(Decimal(current['totalGB']) / GIB, 'f')
    fields = [
        (editor_input('用户名', 'name', current['email'], 'maxlength="128" required'),
         editor_select('账号状态', 'enable', {'1': '启用', '0': '停用'}, '1' if current['enable'] else '0')),
        (editor_select('有效期类型', 'expiry_mode', EXPIRY_MODES, expiry_mode),
         editor_input('到期时间（北京时间）', 'expires_at', date_value, 'type="datetime-local" step="0.001"')),
        (editor_input('首次连接后有效天数', 'delayed_days', days, 'inputmode="decimal"'),
         editor_input('流量限额（GiB，0 为不限）', 'quota', quota, 'inputmode="decimal" required')),
        (editor_input('IP 数量上限（0 为不限）', 'limitIp', current['limitIp'], 'type="number" min="0" required'),
         editor_input('分组', 'group', current['group'], 'maxlength="128"')),
    ]
    advanced = [
        (editor_input('备注', 'comment', current['comment'], 'maxlength="512"'),
         editor_input('Telegram ID（0 为未设置）', 'tgId', current['tgId'], 'inputmode="numeric" required')),
        (editor_input('设备数上限（0 为不限）', 'limitHwid', current['limitHwid'], 'type="number" min="0" required'),
         editor_select('流量重置周期', 'trafficReset', TRAFFIC_CYCLES, current['trafficReset'] or 'never')),
        (editor_input('流量重置日期（每月几号）', 'trafficResetDay', current['trafficResetDay'] or 1,
                      'type="number" min="1" max="31" required'),
         editor_input('自动续期周期（天，0 为关闭）', 'reset', current['reset'], 'type="number" min="0" required')),
        (editor_input('每月续期日（0 为关闭）', 'resetDay', current['resetDay'],
                      'type="number" min="0" max="31" required'),
         editor_select('每周续期日', 'resetWeekday',
                       dict(enumerate(('关闭', '星期一', '星期二', '星期三', '星期四', '星期五', '星期六', '星期日'))),
                       current['resetWeekday'])),
        (editor_input('自动续期次数上限（0 为不限）', 'resetMax', current['resetMax'],
                      'type="number" min="0" required'), ''),
    ]
    common = (f'<input type="hidden" name="client_id" value="{current["id"]}">'
              f'<input type="hidden" name="revision" value="{client_revision(current)}">')
    primary_rows = ''.join('<div class="field-row">' + left + right + '</div>' for left, right in fields)
    advanced_rows = ''.join('<div class="field-row">' + left + right + '</div>' for left, right in advanced)
    name = html.escape(current['email'])
    return portal_app(
        '编辑客户端',
        '<div class="client-editor">' + notice(message) +
        '<section class="surface"><h2>' + name + '</h2><form method="post" action="/admin/operations/">' +
        common + '<input type="hidden" name="action" value="edit_client">' + primary_rows +
        '<details><summary>高级设置</summary>' + advanced_rows + '</details>'
        '<div class="actions"><button>保存到 3x-ui</button>'
        '<a class="edit-link" href="/admin/operations/">返回账号列表</a></div></form></section>'
        '<details class="surface"><summary>重置流量与删除</summary><div class="editor-ops">'
        '<form method="post" action="/admin/operations/">' + common +
        '<label><input class="inline" type="checkbox" name="confirm" value="yes" required>确认清零已用流量</label>'
        '<button class="secondary" name="action" value="reset_traffic">重置流量</button></form>'
        '<form method="post" action="/admin/operations/">' + common +
        '<label><input class="inline" type="checkbox" name="confirm" value="yes" required>确认从 3x-ui 删除客户端和订阅</label>'
        '<button class="danger" name="action" value="delete_client">删除客户端</button></form>'
        '</div></details></div>', 'accounts', admin=True)


def editor_number(form, field, low, high):
    value = form.get(field, '')
    if re.fullmatch(r'-?\d{1,19}', value):
        number = int(value)
        if low <= number <= high:
            return number
    raise PortalError('请检查高级设置或 IP 数量上限的数值范围。')


def editor_decimal(form, field, scale, low, high, message):
    raw = form.get(field, '')
    if len(raw) <= 40:
        try:
            value = Decimal(raw)
            if value.is_finite() and low <= value <= Decimal(high) / scale:
                return round(value * scale)
        except InvalidOperation:
            pass
    raise PortalError(message)


def editor_changes(current, form):
    name = form.get('name', '').strip()
    if not USERNAME.fullmatch(name) or name.lower() == ADMIN_USERNAME:
        raise PortalError('请输入有效用户名，最多 128 位，且须与管理员账号不同。')
    if form.get('enable') not in ('0', '1'):
        raise PortalError('请选择账号状态。')
    expiry_mode = form.get('expiry_mode')
    if expiry_mode == 'unlimited':
        expiry = 0
    elif expiry_mode == 'fixed':
        value = form.get('expires_at', '')
        try:
            stamp = dt.datetime.fromisoformat(value)
            if stamp.tzinfo is not None or 'T' not in value or stamp.year < 1971:
                raise ValueError
            expiry = round(stamp.replace(tzinfo=TZ).timestamp() * 1000)
        except (ValueError, OverflowError):
            raise PortalError('请输入有效的到期时间。') from None
    elif expiry_mode == 'delayed':
        expiry = -editor_decimal(form, 'delayed_days', DAY_MS, Decimal('0.000001'), 36500 * DAY_MS,
                                 '首次连接后的有效期须大于 0 天且不超过 36500 天。')
    else:
        raise PortalError('请选择有效期类型。')

    maximum = (1 << 63) - 1
    quota = editor_decimal(form, 'quota', GIB, 0, maximum, '请输入有效的流量限额。')
    cycle = form.get('trafficReset')
    if cycle not in TRAFFIC_CYCLES:
        raise PortalError('请选择流量重置周期。')
    group, comment = form.get('group', ''), form.get('comment', '')
    if len(group) > 128 or len(comment) > 512:
        raise PortalError('分组最多 128 位，备注最多 512 位。')
    changes = {
        'email': name, 'enable': form['enable'] == '1', 'expiryTime': expiry,
        'totalGB': quota, 'group': group, 'comment': comment, 'trafficReset': cycle,
        'limitIp': editor_number(form, 'limitIp', 0, (1 << 31) - 1),
        'limitHwid': editor_number(form, 'limitHwid', 0, (1 << 31) - 1),
        'tgId': editor_number(form, 'tgId', -maximum, maximum),
        'trafficResetDay': editor_number(form, 'trafficResetDay', 1, 31),
        'reset': editor_number(form, 'reset', 0, (1 << 31) - 1),
        'resetDay': editor_number(form, 'resetDay', 0, 31),
        'resetWeekday': editor_number(form, 'resetWeekday', 0, 7),
        'resetMax': editor_number(form, 'resetMax', 0, (1 << 31) - 1),
    }
    if changes['resetWeekday'] and (changes['reset'] or changes['resetDay']):
        raise PortalError('每周自动续期不能与按天或每月续期同时启用。')
    return {key: value for key, value in changes.items() if value != current.get(key)}


def change_panel_client(panel_id, action, form):
    snapshot = find_panel_client(sync_accounts(), panel_id)
    with OP:
        details = client_details(snapshot['email'])
        current = details['client']
        if current['id'] != panel_id or INBOUND_ID not in details['inboundIds']:
            raise PortalError('该客户端已删除或不再属于此订阅节点。')
        # The list and detail endpoints expose the same client record.
        revision = client_revision({**current, 'inboundIds': details['inboundIds']})
        if not secrets.compare_digest(revision, form.get('revision', '')):
            raise PortalError('客户端资料已在其他页面更新。请核对下方最新资料后再提交。')
        name = current['email']
        if action == 'edit_client':
            changes = editor_changes(current, form)
            if not changes:
                return '客户端资料已是最新。'
            save_client(details, changes)
            result = '客户端资料已保存到 3x-ui。'
        elif form.get('confirm') != 'yes':
            raise PortalError('请勾选对应的确认选项后再提交。')
        elif action == 'reset_traffic':
            portal_traffic.around_counter_change(STATE, api, lambda: api(client_path('resetTraffic', name), {}))
            result = '已重置该客户端在 3x-ui 的流量。'
        else:
            portal_traffic.around_counter_change(STATE, api, lambda: api(client_path('del', name), {}))
            result = '已从 3x-ui 删除客户端和订阅。'
    sync_accounts()
    return result


def is_panel_admin(raw_cookie):
    if not raw_cookie or len(raw_cookie) > 4096:
        return False
    request = urllib.request.Request(PANEL_BASE + 'panel/api/inbounds/list/slim',
                                     headers={'Cookie': raw_cookie, 'Accept': 'application/json'})
    try:
        with urllib.request.urlopen(request, timeout=5) as response:
            if response.headers.get_content_type() != 'application/json':
                return False
            return json.load(response).get('success') is True
    except (ValueError, urllib.error.HTTPError):
        return False

def save_client(details, changes):
    current = details['client']
    # GET returns the DB id and string-encoded tunnel fields. POST expects
    # the protocol UUID as id and array-encoded tunnel fields.
    settings = {**current, 'id': current['uuid'], **changes}
    allowed = settings.get('allowedIPs')
    if isinstance(allowed, str):
        settings['allowedIPs'] = [part.strip() for part in allowed.split(',') if part.strip()]
    per_inbound = details.get('tunnelAllowedIPs') or {}
    if per_inbound:
        settings['allowedIPsByInbound'] = {key: value.split(',') for key, value in per_inbound.items() if value}
    api(client_path('update', current['email']), settings)
    result = client(settings['email'])
    if result['id'] != current['id'] or any(result.get(key) != value for key, value in changes.items()):
        raise PortalError('3x-ui 返回的资料与提交内容不同。请核对当前资料。')


def update_client(details, target, enable):
    save_client(details, {'expiryTime': target, 'enable': enable})


def change_service(name, action, months=0):
    with OP:
        with database() as db:
            if not db.execute('select 1 from accounts where name=?', (name,)).fetchone():
                raise PortalError('账号不存在。')
        details = client_details(name)
        current = details['client']
        expiry = current['expiryTime']
        now = int(time.time() * 1000)
        if action == 'extend':
            if not 1 <= months <= 12 or expiry <= 0:
                raise PortalError('请选择 1–12 个月。长期账号无需续期。')
            target = add_months(max(expiry, now), months)
            update_client(details, target, current['enable'])
            return '已续期至 ' + expiration({'expiryTime': target})
        if action == 'pause':
            update_client(details, expiry, False)
            return '已暂停 ' + name + ' 的订阅。'
        if 0 < expiry <= now:
            raise PortalError('请先给已到期的账号续期。')
        update_client(details, expiry, True)
        return '已恢复 ' + name + ' 的订阅。'


class Handler(http.server.BaseHTTPRequestHandler):
    def setup(self):
        super().setup()
        self.connection.settimeout(15)

    def log_message(self, *_):
        pass

    def send(self, status, data, media='text/html; charset=utf-8', headers=None, *, cache_control='private, no-store'):
        form_cookie = ''
        if media.startswith('text/html') and POST_FORM.search(data):
            token = self.cookie(FORM_COOKIE)
            if not token:
                token = secrets.token_hex(32)
                form_cookie = f'{FORM_COOKIE}={token}; Path=/; Secure; HttpOnly; SameSite=Lax'
            field = f'<input type="hidden" name="_form_token" value="{token}">'
            data = POST_FORM.sub(lambda match: match[0] + field, data)
        body = data if isinstance(data, bytes) else data.encode()
        self.send_response(status)
        self.send_header('Content-Type', media)
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', cache_control)
        self.send_header('Referrer-Policy', 'no-referrer')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('X-Robots-Tag', 'noindex, nofollow')
        self.send_header('Content-Security-Policy',
                         "default-src 'none'; style-src 'self'; style-src-attr 'unsafe-inline'; script-src 'self'; img-src 'self'; form-action 'self'; frame-ancestors 'none'; base-uri 'none'" +
                         ("; connect-src 'self'" if urllib.parse.urlsplit(self.path).path == '/admin/operations/' else ''))
        for key, value in (headers or {}).items():
            self.send_header(key, str(value))
        if form_cookie:
            self.send_header('Set-Cookie', form_cookie)
        self.end_headers()
        if self.command != 'HEAD':
            self.wfile.write(body)

    def do_HEAD(self):
        self.do_GET()

    def do_GET(self):
        self.route()

    def do_POST(self):
        self.route()

    def route(self):
        if not GATE.acquire(blocking=False):
            self.send(503, page('请稍后重试', '服务繁忙。'))
            return
        try:
            self.dispatch()
        except (BrokenPipeError, ConnectionResetError):
            pass
        except PortalError as exc:
            self.send(400, page('请重新提交', '<p>' + html.escape(str(exc)) +
                                '</p><p><a href="/">返回首页</a></p>'))
        except ValueError:
            self.send(400, page('请求无效', '<p>请返回原页面后重新提交。</p>'))
        except Exception as exc:
            print('Portal request failed:', type(exc).__name__, file=sys.stderr)
            self.send(503, page('请稍后重试', '暂时无法确认状态。若正在兑换，请稍后用同一兑换码重试。'))
        finally:
            GATE.release()

    def redirect(self, path, cookie=None):
        headers = {'Location': path}
        if cookie is not None:
            headers['Set-Cookie'] = cookie
        self.send(303, '', headers=headers)

    def form(self):
        if self.headers.get('Origin') not in (None, 'null', HOME) or self.headers.get('Sec-Fetch-Site') == 'cross-site':
            raise PortalError('请在本站打开页面后重新提交。')
        length = int(self.headers.get('Content-Length', '0'))
        admin = urllib.parse.urlsplit(self.path).path == '/admin/operations/'
        max_body = 16384 if admin else MAX_BODY
        if not 0 < length <= max_body or self.headers.get_content_type() != 'application/x-www-form-urlencoded':
            raise PortalError('表单内容无效。')
        values = urllib.parse.parse_qs(self.rfile.read(length).decode(), max_num_fields=28 if admin else 9)
        submitted = values.pop('_form_token', [])
        token = self.cookie(FORM_COOKIE)
        if len(submitted) != 1 or not token or not re.fullmatch(r'[a-f0-9]{64}', submitted[0]) or not secrets.compare_digest(token, submitted[0]):
            raise PortalError('表单校验已失效，请刷新原页面后再提交。')
        return {key: entries[0] for key, entries in values.items()}

    def cookie(self, name):
        jar = http.cookies.SimpleCookie()
        try:
            jar.load(self.headers.get('Cookie', ''))
        except http.cookies.CookieError:
            return ''
        item = jar.get(name)
        value = item.value if item else ''
        return value if re.fullmatch(r'[a-f0-9]{64}', value) else ''

    def get_session(self):
        token = self.cookie(COOKIE)
        if not token:
            return '', None
        with database() as db:
            row = db.execute('select name from sessions where token=? and expires>?',
                             (digest(token), int(time.time()))).fetchone()
        return token, row['name'] if row else None

    def admin_operations(self):
        if not is_panel_admin(self.headers.get('Cookie', '')):
            self.redirect('/admin/')
            return
        parts = urllib.parse.urlsplit(self.path)
        section = next(key for key, (_, url) in ADMIN_PAGES.items() if url == parts.path)
        query = urllib.parse.parse_qs(parts.query, keep_blank_values=True, max_num_fields=12)
        if self.command != 'POST':
            if section == 'traffic':
                self.send(200, traffic_dashboard(query, admin=True))
            elif section == 'accounts' and query.get('client'):
                self.show_client_editor(int(query['client'][0]))
            else:
                self.send(200, portal_admin_page(section=section, panel_id=account_filter_id(query)))
            return
        if section == 'traffic':
            self.send(405, '')
            return
        try:
            form = self.form()
            action = form.get('action')
            if action == 'voucher':
                if section != 'vouchers':
                    raise PortalError('请在兑换码页面提交。')
                months, days = int(form.get('months', '0')), int(form.get('days', '0'))
                code = voucher(None, months, days)
                self.send(200, portal_admin_page(f'已生成续期 {months} 个月的通用兑换码，{days} 天内有效。', code, section))
                return
            if action in ('edit_client', 'reset_traffic', 'delete_client'):
                panel_id = int(form.get('client_id', '0'))
                try:
                    message = change_panel_client(panel_id, action, form)
                except PortalError as exc:
                    self.show_client_editor(panel_id, str(exc), 400)
                    return
                if action == 'delete_client':
                    self.send(200, portal_admin_page(message))
                else:
                    self.show_client_editor(panel_id, message)
                return
            sync_accounts()
            name = form.get('name', '')
            if not USERNAME.fullmatch(name):
                raise PortalError('请输入 3x-ui 支持的用户名，最多 128 位，不能包含空格、斜杠或冒号。')
            if action == 'create':
                if name.lower() == ADMIN_USERNAME:
                    raise PortalError('该用户名已保留给管理员。')
                months = int(form.get('months', '0'))
                if not 1 <= months <= 12:
                    raise PortalError('有效期须为 1–12 个月。')
                with OP:
                    with database() as db:
                        if db.execute('select 1 from accounts where name=?', (name,)).fetchone():
                            raise PortalError('该用户名已经存在。')
                    account = register(name, add_months(int(time.time() * 1000), months))
                message = '已开通 ' + name + '。'
                activation = copy_field('首次激活链接', HOME + '/account/' + account['access_key'], 'new-activation')
                self.send(200, portal_app('开通账号', notice(message) + '<section class="surface panel-form">' +
                                          activation + '<p><a href="/admin/operations/create">继续开通</a></p></section>',
                                          'create', admin=True))
                return
            elif action in ('extend', 'pause', 'resume'):
                message = change_service(name, action, int(form.get('months', '0')) if action == 'extend' else 0)
            elif action == 'reset' and form.get('confirm') == 'yes':
                key = secrets.token_hex(28)
                with database() as db:
                    updated = db.execute('update accounts set access_key=? where name=?', (key, name))
                    if updated.rowcount != 1:
                        raise PortalError('账号不存在。')
                    db.execute('delete from credentials where name=?', (name,))
                    db.execute('delete from sessions where name=?', (name,))
                message = name + ' 的新激活链接：' + HOME + '/account/' + key
            else:
                raise PortalError('操作无效。')
            self.send(200, portal_admin_page(message, panel_id=account_filter_id(query)))
        except (PortalError, ValueError) as exc:
            self.send(400, portal_admin_page(str(exc) if isinstance(exc, PortalError) else '请输入有效数字。',
                                             section=section, panel_id=account_filter_id(query)))

    def show_client_editor(self, panel_id, message='', status=200):
        try:
            current = find_panel_client(sync_accounts(), panel_id)
        except PortalError as exc:
            self.send(404, portal_admin_page(str(exc)))
            return
        self.send(status, client_editor_page(current, message))

    def dispatch(self):
        path = urllib.parse.urlsplit(self.path).path
        if path == '/' and self.command in ('GET', 'HEAD'):
            self.send(200, landing_page())
            return
        if path in ('/brand.webp', '/starry-sea.webp') and self.command in ('GET', 'HEAD'):
            asset, version = ((BRAND_IMAGE, BRAND_IMAGE_VERSION) if path == '/brand.webp' else (SEA_ART, SEA_ART_VERSION))
            versioned = urllib.parse.urlsplit(self.path).query == 'v=' + version
            self.send(200, asset, 'image/webp',
                      cache_control='public, max-age=31536000, immutable' if versioned else 'no-store')
            return
        if path in FRONTEND_ASSETS and self.command in ('GET', 'HEAD'):
            asset, media = FRONTEND_ASSETS[path]
            versioned = urllib.parse.urlsplit(self.path).query == 'v=' + FRONTEND_VERSIONS[path]
            self.send(200, asset, media,
                      cache_control='public, max-age=31536000, immutable' if versioned else 'no-store')
            return
        if path == '/hiddify-routing.json' and self.command in ('GET', 'HEAD'):
            self.send(200, HIDDIFY_RULES, 'application/json; charset=utf-8',
                      {'Content-Disposition': 'attachment; filename="Cheapcoding-Hiddify-rules.json"'})
            return
        if path in {url for _, url in ADMIN_PAGES.values()}:
            self.admin_operations()
            return
        if path in ('/admin/operations/live.js', '/admin/operations/accounts.json'):
            if self.command not in ('GET', 'HEAD') or not is_panel_admin(self.headers.get('Cookie', '')):
                self.send(403, '')
                return
            if path.endswith('.js'):
                self.send(200, ADMIN_LIVE_SCRIPT, 'application/javascript; charset=utf-8')
            else:
                query = urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query, max_num_fields=12)
                panel_id = account_filter_id(query)
                cards, names, known = admin_subscriptions(panel_id)
                data = {'cards': cards, 'names': names,
                        'options': user_filter_options(known, known, panel_id, account_filter_link),
                        'selected_name': known.get(panel_id, f'#{panel_id}') if panel_id is not None else ''}
                self.send(200, json.dumps(data), 'application/json; charset=utf-8')
            return
        if path in ('/login', '/logout') or path in {url for _, url in USER_PAGES.values()}:
            if path != '/login' or self.cookie(COOKIE):
                sync_accounts()
            token, name = self.get_session()
            if path == '/login' and self.command in ('GET', 'HEAD'):
                if name:
                    self.redirect('/dashboard')
                else:
                    self.send(200, login_page())
                return
            if path == '/login' and self.command == 'POST':
                form = self.form()
                username = form.get('username', '').strip()
                password = form.get('password', '')
                if username.lower() == ADMIN_USERNAME:
                    cookie, two_factor = panel_login(password, form.get('code', '').strip(),
                                                     self.headers.get('X-Real-IP', '127.0.0.1'))
                    if cookie:
                        self.redirect('/admin/operations/traffic', cookie)
                    else:
                        self.send(401, login_page('请输入密码和动态验证码。' if two_factor else '用户名或密码错误。',
                                                  username, two_factor))
                    return
                name = authenticate(username, password)
                if not name:
                    self.send(401, login_page('用户名或密码错误。首次使用请先设置密码。'))
                    return
                self.redirect('/dashboard', cookie_header(start_session(name, token), SESSION_SECONDS))
                return
            if not name:
                self.redirect('/login')
                return
            if self.command in ('GET', 'HEAD') and path in {url for _, url in USER_PAGES.values()}:
                section = next(key for key, (_, url) in USER_PAGES.items() if url == path)
                if section in ('traffic', 'subscription'):
                    with database() as db:
                        account = db.execute('select * from accounts where name=?', (name,)).fetchone()
                    if not account or account['panel_id'] is None:
                        self.redirect('/login')
                        return
                    if section == 'traffic':
                        query = urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query, keep_blank_values=True, max_num_fields=12)
                        self.send(200, traffic_dashboard(query, name, account['panel_id']))
                    else:
                        current = client_details(name)
                        record = current['client']
                        record['traffic'] = api(client_path('traffic', name))
                        self.send(200, user_page(section, name, account, record))
                else:
                    self.send(200, user_page(section, name))
                return
            if path == '/logout' and self.command == 'POST':
                self.form()
                with database() as db:
                    db.execute('delete from sessions where token=?', (digest(token),))
                self.redirect('/', cookie_header())
                return
            if path == '/password' and self.command == 'POST':
                form = self.form()
                try:
                    fresh = change_password(name, token, form)
                except PortalError as exc:
                    self.send(400, user_page('password', name, message=str(exc)))
                    return
                self.send(200, user_page('password', name, message='密码已更新。'),
                          headers={'Set-Cookie': cookie_header(fresh, SESSION_SECONDS)})
                return
            if path == '/redeem' and self.command == 'POST':
                form = self.form()
                with database() as db:
                    account = db.execute('select * from accounts where name=?', (name,)).fetchone()
                try:
                    message = redeem(account, form.get('code', '').strip())
                    self.send(200, user_page('redeem', name, message=message))
                except PortalError as exc:
                    self.send(400, user_page('redeem', name, message=str(exc)))
                return
        parts = path.split('/')
        if len(parts) != 3 or parts[1] not in ('account', 'sub'):
            self.send(404, page('页面不存在', '<p><a href="/login">返回登录</a></p>'))
            return
        section, key = parts[1:]
        pattern = SECRET if section == 'account' else SUBSCRIPTION_KEY
        if not pattern.fullmatch(key):
            self.send(404, page('链接无效', '<p>请确认链接，或联系管理员。</p>'))
            return
        field = 'access_key' if section == 'account' else 'subscription'
        panel_clients = sync_accounts()
        with database() as db:
            account = db.execute(f'select * from accounts where {field}=?', (key,)).fetchone()
        if account is None:
            self.send(404, page('链接无效', '<p>请确认链接，或联系管理员。</p>'))
            return
        if section == 'account':
            with database() as db:
                exists = db.execute('select 1 from credentials where name=?', (account['name'],)).fetchone()
            if exists:
                self.redirect('/login')
                return
            if self.command != 'POST':
                self.send(200, activation_page(account['name']))
                return
            form = self.form()
            password = form.get('password', '')
            if not 12 <= len(password) <= 128 or password != form.get('confirmation'):
                self.send(400, activation_page(account['name'], '请输入相同的两次密码，长度 12–128 位。'))
                return
            salt = secrets.token_bytes(16)
            hashed = password_hash(password, salt)
            with database() as db:
                db.execute('begin immediate')
                current = db.execute('select access_key from accounts where name=?', (account['name'],)).fetchone()
                if not current or not secrets.compare_digest(current['access_key'], key):
                    self.send(404, page('链接失效', '<p>请联系管理员获取激活链接。</p>'))
                    return
                inserted = db.execute('insert or ignore into credentials values(?,?,?)',
                                      (account['name'], salt.hex(), hashed))
                activated = inserted.rowcount == 1
            if not activated:
                self.redirect('/login')
                return
            self.redirect('/dashboard', cookie_header(start_session(account['name']), SESSION_SECONDS))
            return
        if self.command == 'POST':
            self.send(405, '')
            return
        current = panel_clients[account['name']]
        expires = current['expiryTime']
        traffic = current.get('traffic') or {}
        used_up = int(traffic.get('up') or 0)
        used_down = int(traffic.get('down') or 0)
        quota = current['totalGB']
        userinfo = f'upload={used_up}; download={used_down}; total={quota}'
        if expires > 0:
            userinfo += f'; expire={expires // 1000}'
        stats = {
            'Subscription-Userinfo': userinfo,
            'Profile-Web-Page-Url': HOME + '/',
            'Profile-Update-Interval': '6',
            'Profile-Title': SUBSCRIPTION_NAME,
            'Content-Disposition': f'attachment; filename="{SUBSCRIPTION_NAME}.yaml"',
        }
        if not current['enable'] or (0 < expires <= int(time.time() * 1000)):
            self.send(403, 'Subscription expired or disabled.\n', 'text/plain; charset=utf-8', stats)
            return
        self.send(200, subscription(account), 'application/yaml; charset=utf-8', stats)


def main():
    os.umask(0o077)
    parser = argparse.ArgumentParser()
    commands = parser.add_subparsers(dest='command', required=True)
    commands.add_parser('serve')
    commands.add_parser('collect-traffic')
    add = commands.add_parser('account')
    add.add_argument('name')
    add.add_argument('expiry', type=int, help='Unix seconds')
    show = commands.add_parser('link')
    show.add_argument('name')
    issue = commands.add_parser('voucher')
    issue.add_argument('--account')
    issue.add_argument('--months', type=int, default=1)
    issue.add_argument('--valid-days', type=int, default=30)
    args = parser.parse_args()
    init()
    if args.command in ('serve', 'collect-traffic'):
        portal_traffic.initialize(STATE)
    if args.command == 'collect-traffic':
        portal_traffic.collect(STATE, api)
    elif args.command == 'serve':
        http.server.ThreadingHTTPServer(('127.0.0.1', int(os.environ.get('CCVPN_PORTAL_PORT', '17764'))), Handler).serve_forever()
    elif args.command == 'voucher':
        sync_accounts()
        print(voucher(args.account, args.months, args.valid_days))
    elif args.command == 'account':
        sync_accounts()
        register(args.name, args.expiry * 1000)
        print('Account ready:', args.name)
    else:
        sync_accounts()
        with database() as db:
            row = db.execute('select access_key from accounts where name=?', (args.name,)).fetchone()
        if row is None:
            raise PortalError('account not found')
        print(HOME + '/account/' + row['access_key'])


if __name__ == '__main__':
    try:
        main()
    except PortalError as exc:
        sys.exit(str(exc))
