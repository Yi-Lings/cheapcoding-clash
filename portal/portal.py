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

CSS = '<style>\n*{box-sizing:border-box}\n:root{color-scheme:light;--ink:#172733;--muted:#506370;--line:#dce6ea;--page:#eef3f5;--accent:#076855;--accent-dark:#075445;--soft:#e7f4ee}\nbody{margin:0;background:var(--page);color:var(--ink);font:16px/1.55 Inter,system-ui,-apple-system,"Microsoft YaHei",sans-serif}\na{color:var(--accent);text-underline-offset:3px;overflow-wrap:anywhere}\nbutton,input,select{font:inherit}\nbutton,a,input,select,summary{touch-action:manipulation}\n:focus-visible{outline:3px solid #2465cf;outline-offset:3px}\n.site-header{background:#102b32;color:#fff}\n.site-bar{width:min(1180px,100% - 40px);min-height:67px;margin:auto;display:flex;align-items:center;justify-content:space-between;gap:14px}\n.brand{display:inline-flex;align-items:center;gap:10px;color:#fff;text-decoration:none;font-size:19px;font-weight:760;letter-spacing:-.02em}\n.brand-mark{display:block;flex:none;width:31px;height:31px;object-fit:contain}\n.site-bar nav{display:flex;gap:14px;align-items:center;flex-wrap:wrap;justify-content:flex-end;font-size:14px}\n.site-bar nav a,.site-bar nav button{color:#dcf0ec;background:transparent;border:1px solid #476065;border-radius:8px;padding:7px 11px;text-decoration:none;min-height:38px;font-size:14px}\n.site-bar nav form{margin:0}\nmain{width:min(100% - 40px,1120px);margin:36px auto 60px}\nmain.narrow{width:min(100% - 40px,470px);margin-top:clamp(38px,9vh,112px)}\nh1,h2,h3,p{margin:0}h1{font-size:clamp(30px,5vw,47px);line-height:1.18;letter-spacing:-.038em}\nh2{font-size:22px;line-height:1.25;letter-spacing:-.025em}\nh3{font-size:17px;line-height:1.35}p+ p{margin-top:10px}\nsmall,.muted{color:var(--muted)}\n.page-intro{margin-bottom:24px}.page-intro .muted{margin-top:8px}\n.surface,.account,.auth-card{background:#fff;border:1px solid var(--line);border-radius:16px;padding:clamp(17px,3vw,26px);min-width:0;box-shadow:0 5px 16px #152d3910}\n.auth-card{padding:clamp(23px,5vw,38px);border-top:4px solid var(--accent);box-shadow:0 14px 38px #152d3914}\n.auth-card form{margin-top:21px}.auth-card button{width:100%}.auth-note{margin-top:18px;font-size:14px;color:var(--muted)}\n.hero-row{display:flex;align-items:flex-start;justify-content:space-between;gap:12px;flex-wrap:wrap}\n.status{display:inline-flex;align-items:center;border-radius:999px;padding:4px 10px;font-size:13px;font-weight:700;background:#ddf4e9;color:#095742;white-space:nowrap}\n.status.inactive{background:#ffead7;color:#784011}\n.dashboard-grid,.operations-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:16px}\n.surface h2{margin-bottom:9px}.surface p{margin-bottom:12px}.surface p:last-child{margin-bottom:0}\n.message{background:#e9f3ee;border:1px solid #cde6da;color:#194f40;border-radius:10px;padding:12px 14px;margin:0 0 16px;overflow-wrap:anywhere}\nlabel{display:block;font-size:14px;font-weight:640;margin:0 0 13px}\ninput,select{display:block;width:100%;min-width:0;min-height:44px;border:1px solid #bccdd1;border-radius:9px;background:#fff;color:var(--ink);padding:9px 11px;margin-top:5px}\ninput[readonly]{background:#f3f7fc;font-weight:400;font-family:ui-monospace,monospace;font-size:13px}\ninput.inline{display:inline;width:auto;min-height:0;margin:0 5px 0 0;accent-color:var(--accent)}\nbutton{min-height:44px;padding:9px 15px;background:var(--accent);border:1px solid transparent;border-radius:9px;color:#fff;font-weight:660;cursor:pointer}\nbutton:hover{background:var(--accent-dark)}button.secondary{background:#fff;color:var(--accent);border-color:#bdd0eb}\nbutton.secondary:hover{background:var(--soft)}button.danger{background:#fff;color:#963a2f;border-color:#e5c0ba}\n.actions,.guide-links{display:flex;align-items:center;flex-wrap:wrap;gap:9px 13px}\n.copy-line{display:grid;grid-template-columns:minmax(0,1fr) auto;gap:8px;align-items:center}\n.copy-line input{margin:0}.copy-line button{white-space:nowrap}\n.copy-feedback{display:block;font-size:13px;color:var(--muted)}.copy-feedback:not(:empty){margin-top:6px}\n.field-row{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:12px}\n.client-guide summary{cursor:pointer;color:var(--accent);font-weight:680}\n.client-guide{background:#fff;border:1px solid var(--line);border-radius:16px;padding:clamp(17px,3vw,26px);margin:17px 0 0}\n.client-guide>p{margin:7px 0 13px;color:var(--muted)}\n.client-guide details{border-top:1px solid var(--line);padding:13px 0}\n.client-guide details:last-child{padding-bottom:0}\n.client-guide h3{margin:17px 0 8px}.client-guide ol{padding-left:1.45rem}\n.client-guide li+li{margin-top:.35rem}\n.client-platforms{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(100%,210px),1fr));gap:9px;margin:0}\n.client-platforms div{background:#f1f6f5;padding:10px 12px;border-radius:8px}\n.client-guide dt{font-weight:680}.client-guide dd{margin:4px 0 0;color:var(--muted)}\n.guide-links{margin:11px 0}.guide-questions dt:not(:first-child){margin-top:12px}\n.voucher-output{background:var(--soft);border:1px solid #b9dbcc;border-radius:12px;padding:14px;margin-bottom:17px}\n.voucher-output label{margin-bottom:5px}.voucher-output .muted{margin-top:5px;font-size:13px}\n@media(max-width:650px){.site-bar,main,main.narrow{width:calc(100% - 28px)}.site-bar{min-height:61px}\nmain{margin-top:22px}.dashboard-grid,.operations-grid{grid-template-columns:1fr}\n.hero-meta{gap:12px 25px}.site-bar nav a,.site-bar nav button{font-size:13px}.brand{font-size:17px}}\n@media(max-width:360px){.field-row{grid-template-columns:1fr;gap:0}.hero-meta{display:grid}.site-bar nav{gap:5px}}\n\n\n:root{--ink:#192332;--muted:#526176;--line:#e2e8f0;--page:#f3f6fa;--accent:#2465cf;--accent-dark:#194fa9;--soft:#ebf3ff}\nbody{font-family:Inter,system-ui,-apple-system,"Microsoft YaHei",sans-serif}\n.site-header{background:#192536}.site-bar{width:min(1260px,100% - 36px);min-height:60px}\n.site-bar nav button{border-color:#526275;color:#f1f5fc}\nmain{width:min(1240px,100% - 36px);margin:26px auto 46px}\nh1{font-size:clamp(27px,3vw,34px);letter-spacing:-.03em}h2{font-size:19px}h3{font-size:16px}\n.page-intro{margin-bottom:16px}.surface,.auth-card,.client-guide{border-radius:12px;box-shadow:0 2px 7px #24355008}\n.auth-card{border-top:1px solid var(--line)}.auth-card form{margin-top:17px}\n.surface{padding:17px}.surface h2{margin-bottom:11px}\n.dashboard-grid,.operations-grid{gap:12px}.dashboard-grid{align-items:start}\n.metric-grid{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:12px;margin:0 0 14px}\n.metric{margin:0}.metric dt{font-size:13px;color:var(--muted)}.metric dd{margin:5px 0 0;font-size:clamp(16px,2vw,22px);font-weight:720;overflow-wrap:anywhere}\n.metric dd .status{margin-top:3px}.metric dt,.metric dd{line-height:1.35}\n.table-panel{padding:0;overflow:hidden;margin-bottom:12px}.table-title{padding:14px 16px;border-bottom:1px solid var(--line)}\n.table-title h2{margin:0}.table-scroll{overflow:auto}\n.account-table{border-collapse:collapse;width:100%;font-size:14px}\n.account-table th,.account-table td{padding:11px 12px;text-align:left;border-bottom:1px solid var(--line);vertical-align:middle}\n.account-table th{background:#f7f9fc;color:#41536a;font-size:13px;white-space:nowrap}\n.account-table .usage{white-space:nowrap;font-variant-numeric:tabular-nums}.account-table tbody:last-child tr:last-child td{border-bottom:0}\n.account-table tbody tr:hover td{background:#f7faff}.account-table .account-name{font-weight:720;overflow-wrap:anywhere}\n.account-table details summary{cursor:pointer;color:var(--accent);font-weight:650;white-space:nowrap}\n.account-table details[open]{min-width:190px}.account-table details label{margin-top:10px}\n.account-table details .copy-line input{min-height:38px}.account-table details .copy-line button{min-height:38px;padding:5px 8px}\ndetails.surface>summary{cursor:pointer;font-weight:690;color:var(--ink)}\ndetails.surface[open]>summary{margin-bottom:13px}\n.account-settings{margin-top:12px}.account-settings form{max-width:490px}\n.operations-grid{grid-template-columns:repeat(3,minmax(0,1fr));margin:0 0 12px;align-items:start}\ndetails.surface[open]>form{margin-top:8px}.operations-grid .field-row{gap:8px}.operations-grid .actions{gap:5px}\n.operations-grid .actions button{font-size:13px;padding:7px 8px}\n.voucher-output{background:var(--soft);border-color:#bfd5f4}\n.message{background:#edf4ff;border-color:#c9daf8;color:#234f88}\n.client-guide{margin-top:12px}\n@media(max-width:850px){.operations-grid{grid-template-columns:repeat(2,minmax(0,1fr))}}\n@media(max-width:650px){.site-bar,main,main.narrow{width:calc(100% - 24px)}.metric-grid{grid-template-columns:repeat(2,minmax(0,1fr));gap:8px}.metric{padding:12px}.dashboard-grid,.operations-grid{grid-template-columns:1fr}.account-table{min-width:690px}main{margin-top:18px}.surface{padding:14px}.table-panel{padding:0}}\n\n\n@media(max-width:650px){\n  .account-table{min-width:0}.account-table thead{position:absolute;width:1px;height:1px;overflow:hidden;clip-path:inset(50%)}\n  .account-table,.account-table tbody{display:block}.account-table tr{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:4px 10px;padding:12px 14px;border-bottom:1px solid var(--line)}\n  .account-table tbody tr:last-child{border-bottom:0}.account-table td{padding:0;border:0;min-width:0;white-space:normal}\n  .account-table td:nth-child(2){text-align:right}\n  .account-table td:nth-child(3),.account-table td:last-child{grid-column:1/-1}\n  .account-table td:nth-child(3)::before{content:"到期 ";color:var(--muted)}\n  .account-table td:nth-child(4)::before{content:"上传 ";color:var(--muted)}\n  .account-table td:nth-child(5)::before{content:"下载 ";color:var(--muted)}\n  .account-table td:nth-child(6)::before{content:"已用 / 限额 ";color:var(--muted)}\n  .account-table td:nth-child(6){grid-column:1/-1}\n  .account-table td[colspan]{grid-column:1/-1}\n}\n</style>'
PORTAL_SCRIPT = "document.addEventListener('click', async event => {\n    const button = event.target.closest('[data-copy]');\n    if (!button) return;\n    const container = button.closest('.copy-line');\n    const input = container.querySelector('input');\n    const message = container.nextElementSibling;\n    try {\n        await navigator.clipboard.writeText(input.value);\n        message.textContent = '已复制';\n    } catch {\n        input.focus();\n        input.select();\n        message.textContent = '已选中，请使用设备的复制功能。';\n    }\n});\n\n" + '(() => {\n    const picker = document.querySelector(\'.range-picker\');\n    if (!picker) return;\n    const start = picker.querySelector(\'input[name="start_date"]\');\n    const end = picker.querySelector(\'input[name="end_date"]\');\n    const presets = picker.querySelector(\'.range-presets\');\n    const buttons = presets.querySelectorAll(\'button\');\n    const summary = picker.querySelector(\'summary\');\n    const update = () => {\n        end.setCustomValidity(end.value && start.value && end.value < start.value\n            ? \'结束日期不能早于开始日期。\' : \'\');\n        buttons.forEach(button => {\n            button.setAttribute(\'aria-pressed\', String(button.dataset.start === start.value &&\n                button.dataset.end === end.value));\n        });\n    };\n    presets.hidden = false;\n    presets.addEventListener(\'click\', event => {\n        const button = event.target.closest(\'button[data-start]\');\n        if (!button) return;\n        start.value = button.dataset.start;\n        end.value = button.dataset.end;\n        update();\n    });\n    start.addEventListener(\'input\', update);\n    end.addEventListener(\'input\', update);\n    picker.addEventListener(\'toggle\', () => {\n        if (!picker.open) {\n            start.value = start.defaultValue;\n            end.value = end.defaultValue;\n            update();\n        }\n    });\n    document.addEventListener(\'click\', event => {\n        if (picker.open && !picker.contains(event.target)) picker.open = false;\n    });\n    document.addEventListener(\'keydown\', event => {\n        if (picker.open && event.key === \'Escape\') {\n            picker.open = false;\n            summary.focus();\n        }\n    });\n})();\n' + "(() => {\n    const search = document.getElementById('traffic-user-search');\n    if (!search) return;\n    const field = search.closest('.traffic-user-field');\n    const toggle = field.querySelector('.traffic-user-toggle');\n    const results = document.getElementById('traffic-user-results');\n    function setExpanded(expanded) {\n        results.hidden = !expanded;\n        toggle.setAttribute('aria-expanded', String(expanded));\n    }\n\n    function close() {\n        search.value = search.defaultValue;\n        setExpanded(false);\n    }\n\n    function show(query) {\n        const all = results.querySelector('[data-all-users]').closest('li');\n        const empty = results.querySelector('.traffic-user-empty');\n        const entries = Array.from(results.querySelectorAll('[data-client]'), link => ({\n            link,\n            row: link.closest('li'),\n            name: link.querySelector('span').textContent.toLowerCase(),\n        }));\n\n        const id = query.replace(/^#/, '') || query;\n        all.hidden = Boolean(query);\n        for (const {link, row, name} of entries) {\n            row.hidden = Boolean(query) && !name.includes(query) && !link.dataset.client.includes(id);\n        }\n        empty.hidden = !query || entries.some(({row}) => !row.hidden);\n        setExpanded(true);\n    }\n\n    function visibleLinks() {\n        return Array.from(results.querySelectorAll('li:not([hidden]) > a'));\n    }\n\n    function showAll() {\n        search.value = search.defaultValue;\n        show('');\n    }\n\n    toggle.hidden = false;\n    toggle.addEventListener('click', () => {\n        if (results.hidden) showAll();\n        else close();\n    });\n    search.addEventListener('input', () => show(search.value.trim().toLowerCase()));\n    field.addEventListener('keydown', event => {\n        if (event.isComposing) return;\n        if (event.key === 'Escape' && !results.hidden) {\n            event.preventDefault();\n            close();\n        } else if (event.key === 'ArrowDown') {\n            event.preventDefault();\n            if (results.hidden) showAll();\n            visibleLinks()[0]?.focus();\n        } else if (event.key === 'Enter' && event.target === search && !results.hidden) {\n            visibleLinks()[0]?.click();\n        }\n    });\n    results.addEventListener('keydown', event => {\n        if (event.key === 'Escape') {\n            event.preventDefault();\n            close();\n            search.focus();\n        } else if (event.key === 'ArrowUp' || event.key === 'ArrowDown') {\n            event.preventDefault();\n            const links = visibleLinks();\n            const index = links.indexOf(document.activeElement);\n            if (event.key === 'ArrowUp' && index <= 0) search.focus();\n            else links[index + (event.key === 'ArrowDown' ? 1 : -1)]?.focus();\n        }\n    });\n    document.addEventListener('pointerdown', event => {\n        if (!results.hidden && !field.contains(event.target) && !results.contains(event.target)) close();\n    });\n})();\n" + '(() => {\n    const chart = document.querySelector(\'.traffic-interactive\');\n    if (!chart) return;\n    const scroll = chart.querySelector(\'.traffic-scroll\');\n    const tooltip = chart.querySelector(\'.traffic-tooltip\');\n    const fields = [\'date\', \'up\', \'down\', \'total\', \'status\'].map(key =>\n        [key, tooltip.querySelector(\'[data-field="\' + key + \'"]\')]);\n    let current = null;\n    let pinned = false;\n    let pendingTouch = null;\n    let timer = 0;\n\n    function show(day, keep = false) {\n        if (current !== day) {\n            current?.classList.remove(\'is-current\');\n            day.classList.add(\'is-current\');\n            fields.forEach(([key, field]) => { field.textContent = day.dataset[key]; });\n            current = day;\n        }\n        pinned = keep;\n        tooltip.hidden = false;\n        const bounds = chart.getBoundingClientRect();\n        const column = day.getBoundingClientRect();\n        const width = tooltip.offsetWidth;\n        const middle = column.left - bounds.left + column.width / 2;\n        const side = middle + 10 + width <= bounds.width ? middle + 10 : middle - width - 10;\n        tooltip.style.left = Math.max(0, Math.min(side, bounds.width - width)) + \'px\';\n    }\n\n    function hide() {\n        current?.classList.remove(\'is-current\');\n        current = null;\n        pinned = false;\n        tooltip.hidden = true;\n    }\n\n    function stopPress() {\n        window.clearTimeout(timer);\n        pendingTouch = null;\n    }\n\n    function column(event) {\n        return event.target.closest(\'.traffic-day\');\n    }\n\n    scroll.addEventListener(\'pointerover\', event => {\n        const day = column(event);\n        if (event.pointerType === \'mouse\' && day) show(day);\n    });\n    scroll.addEventListener(\'pointerleave\', () => {\n        if (!pinned && !scroll.contains(document.activeElement)) hide();\n    });\n    scroll.addEventListener(\'focusin\', event => {\n        const day = column(event);\n        if (day && !pendingTouch) show(day, current === day && pinned);\n    });\n    scroll.addEventListener(\'focusout\', event => {\n        if (!scroll.contains(event.relatedTarget) && !pinned) hide();\n    });\n    scroll.addEventListener(\'click\', event => {\n        const day = column(event);\n        if (day) show(day, true);\n    });\n    scroll.addEventListener(\'pointerdown\', event => {\n        const day = column(event);\n        if (!day || ![\'touch\', \'pen\'].includes(event.pointerType)) return;\n        stopPress();\n        pendingTouch = {id: event.pointerId, x: event.clientX, y: event.clientY};\n        timer = window.setTimeout(() => {\n            if (pendingTouch) show(day, true);\n        }, 430);\n    });\n    scroll.addEventListener(\'pointermove\', event => {\n        if (pendingTouch?.id === event.pointerId &&\n            Math.hypot(event.clientX - pendingTouch.x, event.clientY - pendingTouch.y) > 10) stopPress();\n    });\n    scroll.addEventListener(\'pointerup\', stopPress);\n    scroll.addEventListener(\'pointercancel\', stopPress);\n    scroll.addEventListener(\'scroll\', () => { stopPress(); hide(); }, {passive: true});\n    scroll.addEventListener(\'contextmenu\', event => {\n        if (pendingTouch || (pinned && event.pointerType !== \'mouse\')) event.preventDefault();\n    });\n    document.addEventListener(\'pointerdown\', event => {\n        if (!chart.contains(event.target)) hide();\n    });\n    document.addEventListener(\'keydown\', event => {\n        if (event.key === \'Escape\' && current) {\n            stopPress();\n            hide();\n        }\n    });\n    window.addEventListener(\'resize\', () => {\n        if (current) show(current, pinned);\n    });\n})();\n' + "(() => {\n    const shell = document.querySelector('.portal-shell');\n    if (!shell) return;\n    const sidebar = shell.querySelector('.portal-sidebar');\n    const main = shell.querySelector('.portal-main');\n    const opener = shell.querySelector('.portal-menu-open');\n    const backdrop = shell.querySelector('.portal-backdrop');\n    const mobile = window.matchMedia('(max-width: 850px)');\n    let open = false;\n\n    function render() {\n        const shown = mobile.matches && open;\n        sidebar.inert = mobile.matches && !shown;\n        main.inert = shown;\n        opener.setAttribute('aria-expanded', String(shown));\n        shell.classList.toggle('menu-open', shown);\n    }\n\n    function close() {\n        if (!open) return;\n        open = false;\n        render();\n        if (mobile.matches) opener.focus({preventScroll: true});\n    }\n\n    opener.addEventListener('click', () => {\n        if (!mobile.matches) return;\n        open = true;\n        render();\n        (sidebar.querySelector('a[aria-current]') ?? sidebar.querySelector('.brand')).focus({preventScroll: true});\n    });\n    backdrop.addEventListener('click', () => close());\n    sidebar.addEventListener('keydown', event => {\n        if (!open) return;\n        if (event.key === 'Escape') {\n            event.preventDefault();\n            close();\n        } else if (event.key === 'Tab') {\n            const links = sidebar.querySelectorAll('a[href]');\n            const first = links[0];\n            const last = links[links.length - 1];\n            if (event.shiftKey && document.activeElement === first) {\n                event.preventDefault();\n                last.focus();\n            } else if (!event.shiftKey && document.activeElement === last) {\n                event.preventDefault();\n                first.focus();\n            }\n        }\n    });\n    mobile.addEventListener('change', () => {\n        const hadSidebarFocus = sidebar.contains(document.activeElement);\n        open = false;\n        render();\n        if (mobile.matches && hadSidebarFocus) opener.focus({preventScroll: true});\n    });\n    window.addEventListener('pageshow', () => close());\n    render();\n})();\n"

BRAND_IMAGE = Path(__file__).with_name('cheapcoding-vpn.webp').read_bytes()
BRAND_IMAGE_VERSION = hashlib.sha256(BRAND_IMAGE).hexdigest()[:16]
BRAND_IMAGE_TAG = (
    f'<img class="brand-mark" src="/brand.webp?v={BRAND_IMAGE_VERSION}" '
    'width="31" height="31" alt="" aria-hidden="true">'
)

SEA_ART = Path(__file__).with_name('cheapcoding-starry-sea.webp').read_bytes()
SEA_ART_VERSION = hashlib.sha256(SEA_ART).hexdigest()[:16]
SEA_THEME_CSS = (
    f':root{{--sea-art:url("/starry-sea.webp?v={SEA_ART_VERSION}")}}\n'
    + ':root{color-scheme:dark;--ink:#edf5ff;--muted:#a6bbd6;--line:#253c5b;--page:#081326;--accent:#62d9ff;--accent-dark:#a0ebff;--soft:#122e49;--surface:#101e35;--control:#0b182c;--on-accent:#051524}\nbody{min-height:100vh;background:radial-gradient(ellipse at 88% 0%,#102e50 0,transparent 44rem),var(--page)}\n:focus-visible{outline-color:var(--accent)}\n.site-header{background:#081326;border-bottom:1px solid #18314e}\n.site-bar nav a,.site-bar nav button{color:var(--ink);border-color:var(--line)}\n.surface,.account,.auth-card,.client-guide{background:var(--surface);box-shadow:0 8px 26px #02091230}\ninput,select,.range-picker summary{background:var(--control);color:var(--ink);border-color:#365271}\ninput[readonly]{background:#0c1b30}\nbutton{color:var(--on-accent)}\nbutton.secondary,button.danger{background:var(--control)}\nbutton.secondary{border-color:#365271}\nbutton.danger{color:#ffb5be;border-color:#764152}\nbutton.danger:hover{background:#3b2335}\n.status{background:#133d42;color:#8cebdc}\n.status.inactive,.range-warning{background:#362921;color:#ffcf96;border-color:#775238}\n.message,.voucher-output{background:#112d46;border-color:#2d5573;color:#c9efff}\n.client-platforms div{background:var(--control)}\n.portal-sidebar{background:#0b172b}\n.portal-menu a,.account-table th{color:var(--muted)}\n.portal-top .actions a{font-weight:650}\n.portal-backdrop{background:#020815b5}\n.account-table th,.account-table tbody tr:hover td{background:#12253e}\n.traffic-user-results,.range-popover,.traffic-tooltip{background:#14263e;border-color:#365271;box-shadow:0 10px 24px #02081380}\n.traffic-user-results li:not([hidden])+li{border-color:var(--line)}\n.traffic-user-toggle{background:transparent;color:var(--muted)}\n.range-presets button{background:var(--control)}\n.traffic-key,.traffic-bar{background:#34c6ef}\n.traffic-key.down,.traffic-bar.down{background:#829aff}\n.traffic-bars{background:repeating-linear-gradient(to top,#243955 0 1px,transparent 1px 36px)}\n.traffic-chart .traffic-day.is-current{background:#19344f}\n.traffic-chart .traffic-day{color:var(--muted)}\n.traffic-chart .traffic-day.is-current time{color:var(--ink)}\n.auth-page,.landing-page{background:#040d21}\n.auth-page .site-header,.landing-page .site-header{background:#061025}\n.auth-page .site-bar,.landing-page .site-bar{width:min(1200px,calc(100% - 48px));min-height:72px}\n.auth-page main.narrow{position:relative;width:min(1200px,calc(100% - 48px));margin:0 auto;\n  display:flex;justify-content:flex-end;align-items:center;min-height:calc(100svh - 73px);padding:48px 0}\n.auth-page::before{content:"";position:absolute;inset:73px 0 0;pointer-events:none;\n  transform:scaleX(-1);background:linear-gradient(270deg,#040d2110,#040d21dd 84%),var(--sea-art) center/cover no-repeat}\n.auth-card{width:min(100%,445px);padding:clamp(23px,4vw,40px);border-radius:20px;border-color:#2e4c6c;\n  background:#0d1b31f7;box-shadow:0 20px 58px #010714a0}\n.auth-card h1{font-size:clamp(27px,3vw,34px)}\n.auth-card form{margin-top:22px}\n.auth-card form input{min-height:48px}\n.auth-card button{min-height:48px;margin-top:4px}\n.auth-note{line-height:1.65}\n.auth-back{font-size:14px;font-weight:650}\n.landing-page .landing-main{width:100%;max-width:none;margin:0}\n.landing-hero{background:#040d21;position:relative;isolation:isolate;min-height:clamp(530px,70svh,780px);display:grid;align-items:center}\n.landing-hero::before{content:"";position:absolute;inset:0;z-index:-1;\n  background:linear-gradient(90deg,#040b20d9 0%,#040b2078 36%,transparent 70%),\n  linear-gradient(0deg,#040d21 0%,transparent 25%),var(--sea-art) center 55%/cover no-repeat}\n.landing-hero-inner,.landing-features{width:min(1200px,calc(100% - 48px));margin-inline:auto}\n.landing-hero-inner{padding-block:68px}\n.landing-hero h1{font-size:clamp(52px,7.7vw,112px);line-height:1.05;letter-spacing:-.065em;\n  max-width:8em;color:#f1f8ff;text-wrap:balance}\n.landing-hero h1 span{display:block;color:#8ceaff}\n.landing-hero p{max-width:24em;margin:22px 0 29px;color:#c1d7f0;font-size:clamp(16px,1.65vw,20px)}\n.landing-connect{display:inline-flex;align-items:center;gap:17px;padding:12px 21px;min-height:50px;\n  border-radius:10px;background:#82e3ff;color:#061a30;font-weight:760;text-decoration:none;box-shadow:0 7px 25px #32cfff26}\n.landing-connect:hover{background:#b5efff}\n.landing-connect span{font-size:21px;line-height:1}\n.landing-features{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:0;\n  border-block:1px solid var(--line);margin-bottom:42px}\n.landing-feature{padding:22px 24px 24px;border-right:1px solid var(--line)}\n.landing-feature:first-child{padding-left:0}\n.landing-feature:last-child{border-right:0}\n.landing-feature h2{font-size:16px;font-weight:730;color:#c3efff;margin-bottom:6px}\n.landing-feature p{font-size:14px;color:var(--muted)}\n@media(max-width:680px){\n  .auth-page .site-bar,.landing-page .site-bar,.landing-hero-inner,.landing-features{width:calc(100% - 36px)}\n  .auth-page .site-bar,.landing-page .site-bar{min-height:60px}\n  .auth-page::before{inset:61px 0 0;transform:none;background:linear-gradient(#040d2155,#040d21d4 75%),var(--sea-art) 66% center/cover no-repeat}\n  .auth-page main.narrow{min-height:calc(100svh - 61px);justify-content:center;padding:42px 0;width:calc(100% - 36px)}\n  .landing-hero{min-height:560px;align-items:start}\n  .landing-hero::before{background:linear-gradient(180deg,#040d21ab 0%,#040d2150 38%,transparent 59%,#040d21 100%),var(--sea-art) 74% 64%/auto 100% no-repeat}\n  .landing-hero-inner{padding:49px 0 184px}\n  .landing-hero h1{font-size:clamp(44px,11vw,70px);max-width:7em}\n  .landing-hero p{max-width:19em;margin:15px 0 23px}\n  .landing-features{grid-template-columns:1fr;margin-bottom:24px}\n  .landing-feature,.landing-feature:first-child{padding:13px 0;border:0}\n  .landing-feature+.landing-feature{border-top:1px solid var(--line)}\n}\n'
)

PORTAL_SCRIPT_VERSION = hashlib.sha256(PORTAL_SCRIPT.encode()).hexdigest()[:16]
PORTAL_SCRIPT_TAG = f'<script src="/portal.js?v={PORTAL_SCRIPT_VERSION}" defer></script>'


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
    return ('<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width, initial-scale=1">'
            '<title>' + html.escape(title) + ' · Cheapcoding</title>' + CSS.replace('</style>', CLIENT_EDITOR_CSS + SEA_THEME_CSS + '</style>') + '</head><body' + body_class + '>'
            '<header class="site-header"><div class="site-bar">'
            '<a class="brand" href="/">' + BRAND_IMAGE_TAG + 'Cheapcoding VPN</a>' +
            nav + '</div></header><main class="' + main_class + '">' + content +
            '</main>' + PORTAL_SCRIPT_TAG + '</body></html>')


CLIENT_GUIDE = '''<details class="client-guide surface">
  <summary>客户端下载与使用教程</summary>
  <p>安卓、iOS、Windows 和 macOS 首推 Hiddify。安装后，从本页上方复制「Clash 订阅链接」。</p>
  <details open>
    <summary>Hiddify · 四个平台首选</summary>
    <h3>按设备下载</h3>
    <dl class="client-platforms">
      <div><dt>安卓</dt><dd>从 <a href="https://play.google.com/store/apps/details?id=app.hiddify.com">Google Play</a> 安装。也可到 <a href="https://github.com/hiddify/hiddify-app/releases/latest">官方发布页</a> 下载通用 APK。</dd></div>
      <div><dt>iPhone / iPad</dt><dd>在 <a href="https://apps.apple.com/us/app/hiddify-proxy-vpn/id6596777532">App Store</a> 点「获取」。开发者为 Holistic Resilience，客户端下载免费。</dd></div>
      <div><dt>Windows</dt><dd>从 <a href="https://apps.microsoft.com/detail/9ndgwm32bnxk">Microsoft Store</a> 安装，或在 <a href="https://github.com/hiddify/hiddify-app/releases/latest">官方发布页</a> 下载 Windows 安装包。普通 Intel 或 AMD 电脑选 x64。</dd></div>
      <div><dt>macOS</dt><dd>从 <a href="https://github.com/hiddify/hiddify-app/releases/latest">官方发布页</a> 下载 macOS 的 <code>.dmg</code>，打开后把 Hiddify 拖到「应用程序」。Apple 芯片机型也可以从 App Store 安装。</dd></div>
    </dl>
    <h3>导入并连接</h3>
    <ol>
      <li>复制本页上方的完整订阅链接。打开 Hiddify，在首页点 <strong>＋</strong>。</li>
      <li>选择「剪贴板（Clipboard）」。也可以选择「手动（Manually）」，名称填 <strong>Cheapcoding</strong>，在 URL 栏粘贴订阅链接后添加。</li>
      <li>在首页选中刚添加的配置，点击连接按钮。手机首次连接时，按系统提示允许添加 VPN。</li>
      <li>如果电脑提示选择模式，选「VPN 模式」可接管所有应用的流量；主要在浏览器使用时，也可选「系统代理」。</li>
    </ol>
    <h3>启用国内外分流</h3>
    <p>Hiddify 导入订阅后，还需要单独导入一次路由规则。应用后，配置的直连域名及其全部子域名、局域网和中国大陆流量直连，其余流量走代理。</p>
    <p class="guide-links"><a href="HIDDIFY_RULE_LINK_PLACEHOLDER">在 Hiddify 中导入分流规则</a><a href="/hiddify-routing.json" download>下载规则文件</a></p>
    <p>如果链接没有打开 App，下载文件后，在 Hiddify 的「设置 → 路由 → ⋯ → 从文件导入规则」中选择该文件，确认后断开并重连。导入会替换 App 里已有的自定义路由，已有自定义规则时可先导出保存。</p>
    <p>导入后确认「Cheapcoding 直连」和「中国大陆直连」已开启。曾导入旧规则的用户，请重新导入新版。临时需要全局代理时，可在「路由」关闭直连规则，包括内置的地区和局域网直连，再重连；恢复分流时重新开启。</p>
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
      <li>选中刚添加的配置。安卓在「仪表盘」开启连接，电脑开启「系统代理」。</li>
      <li>在「代理」页的 <strong>美国-新泽西</strong> 分组中，选择 <strong>美国-新泽西 01</strong>。</li>
      <li>「出站模式」默认是「规则」，按目的地分流。需要所有流量走代理时，切换到「全局」，再选 <strong>美国-新泽西 01</strong>。</li>
    </ol>
  </details>
  <details>
    <summary>续费后怎么更新？连不上怎么办？</summary>
    <dl class="guide-questions">
      <dt>续费后怎么更新？</dt><dd>Hiddify 在首页配置卡片上点刷新；FlClash 在「配置」页更新当前订阅。最新到期时间可在本页查看。</dd>
      <dt>导入失败怎么办？</dt><dd>重新复制本页的完整订阅地址，确认它以 <code>https://</code> 开头且包含 <code>/sub/</code>，并检查订阅状态。</dd>
      <dt>连接后没有效果？</dt><dd>确认已选中刚导入的配置。如果 WireGuard 或其他 VPN 正在运行，先断开，再连接当前客户端。</dd>
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
    features = (
        ('多端连接', '支持 Android、iOS、Windows 和 macOS。'),
        ('规则分流', '国内流量、局域网及本站域名直连，其余流量走代理。'),
        ('订阅自助管理', '查看用量、到期时间和订阅链接，也可自助续期。'),
    )
    cards = ''.join(f'<article class="landing-feature"><h2>{heading}</h2><p>{detail}</p></article>'
                    for heading, detail in features)
    return ('<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width, initial-scale=1">'
            '<meta name="description" content="Cheapcoding VPN 支持多端连接、规则分流和自助订阅管理。">'
            '<title>Cheapcoding VPN</title>' + CSS.replace('</style>', SEA_THEME_CSS + '</style>') +
            '</head><body class="landing-page"><header class="site-header"><div class="site-bar">'
            '<a class="brand" href="/">' + BRAND_IMAGE_TAG + 'Cheapcoding VPN</a></div></header>'
            '<main class="landing-main"><section class="landing-hero" aria-labelledby="landing-title">'
            '<div class="landing-hero-inner"><h1 id="landing-title">连接<span>更远的世界</span></h1>'
            '<p>在常用设备上导入订阅，按规则连接。用量与订阅状态，登录即可查看。</p>'
            '<a class="landing-connect" href="/login">去连接<span aria-hidden="true">↗</span></a>'
            '</div></section><section class="landing-features" aria-label="服务功能">' + cards +
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
            '<tr data-account-id="' + item_id + '"><td class="account-name">' + name +
            '</td><td><span class="' + state_class + '">' + status + '</span></td><td>' +
            expiration(current) + '</td><td class="usage">' + readable_traffic(up) +
            '</td><td class="usage">' + readable_traffic(down) + '</td><td class="usage">' +
            readable_traffic(up+down) + ' / ' + (readable_traffic(quota) if quota else '不限额') +
            '</td><td class="row-actions"><a class="edit-link" href="/admin/operations/?client=' + item_id +
            '">编辑</a><details><summary>链接</summary>' +
            copy_field('Clash 订阅链接', HOME + '/sub/' + account['subscription'], 'subscribe-' + item_id) +
            activation + '</details></td></tr>')
    body = ''.join(rows) if rows else f'<tr><td colspan="7">{empty_message}</td></tr>'
    return ('<section class="surface table-panel"><div class="table-title"><h2>账号列表（' + str(len(rows)) +
            '）</h2></div><div class="table-scroll" role="region" aria-label="账号和流量" tabindex="0">'
            '<table class="account-table"><thead><tr><th scope="col">用户名</th><th scope="col">状态</th>'
            '<th scope="col">到期时间</th><th scope="col">上传</th><th scope="col">下载</th>'
            '<th scope="col">已用 / 限额</th><th scope="col">操作</th></tr></thead><tbody>' +
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


PORTAL_APP_CSS = """
:root{--page:#f6f8fb;--line:#e2e8f0;--accent:#067765;--accent-dark:#056151;--soft:#e9f6f2}
.portal-shell{--sidebar-width:232px;min-height:100vh}
.portal-sidebar{position:fixed;inset:0 auto 0 0;width:var(--sidebar-width);z-index:12;overflow-y:auto;background:#fff;border-right:1px solid var(--line);padding:20px 12px}
.portal-sidebar-head{display:flex;align-items:center;justify-content:space-between;gap:8px;margin:0 5px 25px}
.portal-sidebar .brand{color:var(--ink);white-space:nowrap;font-size:17px}
.portal-sidebar .brand-mark{width:35px;height:35px}
.portal-menu{display:grid;gap:4px}
.portal-menu a{display:flex;align-items:center;gap:10px;text-decoration:none;color:#43536a;font-size:14px;font-weight:600;padding:11px 12px;border-radius:8px;white-space:nowrap}
.portal-menu a:hover,.portal-menu a[aria-current]{color:var(--accent);background:var(--soft)}
.portal-menu a[aria-current]{font-weight:720}
.portal-menu a svg{width:18px;height:18px;flex:none}
.portal-main{margin:0 0 0 var(--sidebar-width);width:calc(100% - var(--sidebar-width));max-width:none;padding:24px clamp(17px,3vw,38px) 44px}
.portal-top{display:flex;justify-content:space-between;align-items:center;gap:12px;margin-bottom:20px;flex-wrap:wrap}
.portal-heading{display:flex;align-items:center;gap:9px;min-width:0}
.portal-menu-open{display:none}
.portal-backdrop{display:none;position:fixed;inset:0;z-index:11;background:#14223775}
@media(max-width:850px){
.portal-shell{--drawer-duration:220ms;--drawer-ease:cubic-bezier(.22,.75,.25,1)}
.portal-sidebar{width:min(var(--sidebar-width),calc(100vw - 40px));padding:18px 12px;
  transform:translateX(-100%);visibility:hidden;
  transition:transform var(--drawer-duration) var(--drawer-ease),visibility 0s var(--drawer-duration)}
.portal-shell{isolation:isolate}
.portal-main{position:relative;z-index:0}
.portal-shell.menu-open .portal-sidebar{transform:translateX(0);visibility:visible;transition-delay:0s}
.portal-menu-open{display:grid;place-items:center;width:36px;min-height:36px;padding:0;appearance:none}
.portal-menu-open,.portal-sidebar a,.portal-backdrop{-webkit-tap-highlight-color:transparent}
.portal-menu-open:focus-visible,.portal-sidebar a:focus-visible{outline:2px solid var(--accent);outline-offset:2px}
.portal-shell.menu-open .portal-menu-open{visibility:hidden}
.portal-menu-open svg{width:19px;height:19px}
.portal-main{margin:0;width:100%;padding-top:16px}.portal-top{margin-bottom:13px}
.portal-backdrop{display:block;opacity:0;visibility:hidden;
  transition:opacity var(--drawer-duration) ease,visibility 0s var(--drawer-duration)}
.portal-shell.menu-open .portal-backdrop{opacity:1;visibility:visible;transition-delay:0s}
body:has(.portal-shell.menu-open){overflow:hidden}
}
@media(prefers-reduced-motion:reduce){.portal-shell{--drawer-duration:0ms}}
.portal-top h1{font-size:clamp(24px,3vw,32px)}.portal-top .actions{margin-left:auto}
.portal-top .actions form{margin:0}.portal-top .actions a,.portal-top button{font-size:14px;white-space:nowrap}
.panel-form{max-width:570px}.panel-form+.panel-form{margin-top:12px}.portal-main .client-guide{margin:0}
.traffic-filters{display:flex;gap:12px;align-items:end;flex-wrap:wrap;margin-bottom:17px}
.traffic-filters label{margin:0;min-width:130px;max-width:min(100%,300px);flex:0 1 210px}
.traffic-metrics{grid-template-columns:repeat(3,minmax(0,1fr))}
.traffic-card{margin-bottom:12px}.traffic-caption,.traffic-legend{font-size:13px;color:var(--muted)}
.traffic-legend{display:flex;gap:15px;flex-wrap:wrap}.traffic-legend span{display:inline-flex;align-items:center;gap:6px}
.traffic-key{display:inline-block;width:9px;height:9px;border-radius:2px;background:#087f6c}
.traffic-key.down,.traffic-bar.down{background:#426add}
.traffic-scroll{overflow:auto;margin:12px 0}
.traffic-chart{min-width:var(--chart-min);display:flex;gap:3px;align-items:stretch;border-bottom:1px solid var(--line)}
.traffic-day{min-width:0;flex:1;display:flex;flex-direction:column;text-align:center;color:var(--muted);font-size:11px;line-height:1.15}
.traffic-bars{height:145px;display:flex;align-items:end;justify-content:center;gap:3px;background:repeating-linear-gradient(to top,#e2e8f0 0 1px,transparent 1px 36px)}
.traffic-bar{width:27%;max-width:20px;min-width:3px;background:#087f6c;border-radius:4px 4px 0 0;height:var(--bar-height)}
.traffic-day time{padding:8px 0;white-space:nowrap}.traffic-day.unobserved .traffic-bars{opacity:.38}
.traffic-interactive{position:relative}
.traffic-chart .traffic-day{border:0;border-radius:5px;background:transparent;padding:0;font-weight:500;cursor:crosshair;user-select:none;-webkit-user-select:none;-webkit-touch-callout:none;touch-action:pan-x pan-y}
.traffic-chart .traffic-day:focus-visible{outline-offset:-2px}
.traffic-chart .traffic-day.is-current{background:#e9f3f3}
.traffic-chart .traffic-day.is-current time{color:var(--ink);font-weight:720}
.traffic-tooltip{position:absolute;z-index:2;top:7px;pointer-events:none;min-width:145px;max-width:100%;padding:9px 11px;border:1px solid #dce5ec;border-radius:8px;background:#fff;color:var(--ink);box-shadow:0 5px 17px #19233225;font-size:13px;line-height:1.4}
.traffic-tooltip strong{display:block;margin-bottom:4px;font-size:13px}
.traffic-tooltip dl,.traffic-tooltip dd{margin:0}
.traffic-tooltip dl div{display:flex;justify-content:space-between;gap:12px}
.traffic-tooltip dt{display:flex;align-items:center;gap:5px;color:var(--muted)}
.traffic-tooltip dd{font-variant-numeric:tabular-nums;font-weight:650;white-space:nowrap}
.traffic-tooltip .traffic-tooltip-total{border-top:1px solid var(--line);margin-top:4px;padding-top:4px}
.traffic-tooltip-status{display:block;color:var(--muted);font-size:12px;margin-top:3px}
.traffic-data{width:100%;border-collapse:collapse;font-size:14px;font-variant-numeric:tabular-nums}
.traffic-data th,.traffic-data td{padding:9px 7px;border-bottom:1px solid var(--line);text-align:left}
.traffic-data th{color:var(--muted);font-size:13px}.traffic-data tr:last-child td{border-bottom:0}
@media(max-width:440px){
.traffic-metrics{grid-template-columns:repeat(2,minmax(0,1fr))}.traffic-metrics .metric:last-child{grid-column:1/-1}
.traffic-filters label{flex-basis:125px;min-width:100px}.traffic-data th,.traffic-data td{padding:7px 4px;font-size:12px}
}
"""

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
    return ('<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width, initial-scale=1">'
            '<title>' + html.escape(title) + ' · Cheapcoding</title>' +
            CSS.replace('</style>', CLIENT_EDITOR_CSS + PORTAL_APP_CSS + TRAFFIC_RANGE_CSS + SEA_THEME_CSS + '</style>') +
            '</head><body><div class="portal-shell"><aside class="portal-sidebar" id="portal-sidebar">'
            '<div class="portal-sidebar-head"><a class="brand" href="' + pages['traffic'][1] +
            '">' + BRAND_IMAGE_TAG + 'Cheapcoding VPN</a></div>'
            '<nav class="portal-menu" aria-label="功能菜单">' + links + '</nav></aside>'
            '<div class="portal-backdrop" aria-hidden="true"></div>'
            '<main class="portal-main"><header class="portal-top"><div class="portal-heading">'
            '<button type="button" class="portal-menu-open secondary" aria-controls="portal-sidebar" '
            'aria-expanded="false" aria-label="打开导航">' + portal_icon('menu') + '</button>'
            '<h1>' + html.escape(title) + '</h1></div><div class="actions">' + actions + '</div></header>' + body +
            '</main></div>' + PORTAL_SCRIPT_TAG + '</body></html>')


TRAFFIC_RANGE_CSS = """
.traffic-toolbar{display:flex;flex-wrap:wrap;align-items:end;gap:12px;margin-bottom:17px}
.traffic-toolbar .traffic-filters{margin:0}
.traffic-user-field{flex:1 1 245px;max-width:340px;min-width:0}
.traffic-user-field label{margin:0}.traffic-user-field input{margin:5px 0 0;padding-right:76px}
.traffic-user-input{position:relative}
.traffic-user-clear{position:absolute;right:39px;top:50%;transform:translateY(-50%);padding:4px 8px;color:var(--muted);font-size:19px;line-height:1;text-decoration:none;border-radius:5px}
.traffic-user-toggle{position:absolute;right:4px;top:50%;transform:translateY(-50%);display:grid;place-items:center;width:31px;min-height:32px;padding:0;background:transparent;color:var(--muted)}
.traffic-user-toggle[hidden]{display:none}
.traffic-user-toggle:hover{background:var(--soft);color:var(--accent)}
.traffic-user-toggle::before{content:"";width:8px;height:8px;border:solid currentColor;border-width:0 1.6px 1.6px 0;transform:translateY(-2px) rotate(45deg)}
.traffic-user-toggle[aria-expanded="true"]::before{transform:translateY(2px) rotate(225deg)}
.traffic-user-input input::-webkit-search-cancel-button{display:none}
.traffic-user-clear:hover{color:var(--accent);background:var(--soft)}
.traffic-user-results{flex:1 0 100%;min-width:0;max-height:244px;overflow:auto;background:#fff;border:1px solid var(--line);border-radius:9px;margin:0;padding:4px 8px;list-style:none}
.traffic-user-results li:not([hidden])+li{border-top:1px solid #edf0f4}
.traffic-user-results a{display:flex;justify-content:space-between;gap:9px;align-items:baseline;color:var(--ink);padding:8px 6px;text-decoration:none;overflow-wrap:anywhere}
.traffic-user-results a:hover,.traffic-user-results a:focus-visible,.traffic-user-results a[aria-current]{color:var(--accent);background:var(--soft);border-radius:5px}
.traffic-user-results small{flex:none}
.traffic-user-empty{padding:7px;font-size:14px;color:var(--muted)}
@media(max-width:560px){.traffic-toolbar{align-items:stretch}.traffic-user-field{flex-basis:100%;max-width:none}}
.range-picker{position:relative;min-width:0}
.range-picker summary{display:flex;align-items:center;gap:9px;list-style:none;cursor:pointer;min-height:44px;padding:8px 12px;border:1px solid #bccdd1;border-radius:9px;background:#fff;color:var(--ink);font-size:14px;font-weight:650}
.range-picker summary::-webkit-details-marker{display:none}
.range-picker summary::after{content:"";width:7px;height:7px;border:solid var(--muted);border-width:0 1.5px 1.5px 0;transform:rotate(45deg);margin:0 2px 3px 3px}
.range-picker[open] summary{border-color:var(--accent)}
.range-picker[open] summary::after{transform:rotate(225deg);margin-bottom:0}
.range-caption{display:block;color:var(--ink);font-size:14px;font-weight:640;margin-bottom:5px}
.range-popover{position:absolute;top:calc(100% + 7px);left:0;z-index:4;width:min(350px,calc(100vw - 36px));padding:12px;background:white;border:1px solid var(--line);border-radius:12px;box-shadow:0 10px 24px #19233224}
.range-presets{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:5px;margin-bottom:11px}
.range-presets[hidden]{display:none}
.range-presets button{min-height:36px;padding:5px 4px;color:var(--muted);background:#f6f8fb;font-size:13px}
.range-presets button:hover,.range-presets button[aria-pressed="true"]{background:var(--soft);color:var(--accent)}
.range-dates{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:9px}
.traffic-filters .range-dates label{min-width:0;font-size:13px;margin-bottom:10px}
.range-dates input{font-size:14px;padding:6px;min-width:0}
.range-warning{margin:0 0 12px;padding:9px 12px;border:1px solid #efd2ac;border-radius:8px;background:#fff7ec;color:#784011;font-size:14px}
.traffic-pages{display:flex;align-items:center;gap:14px;flex-wrap:wrap;margin-top:12px;font-size:14px}
.traffic-pages .muted{font-size:13px}
@media(max-width:850px){
.traffic-filters{gap:9px}.traffic-filters>.range-control{order:-1;flex-basis:100%}
.range-picker{width:max-content;max-width:100%}
}
"""


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
    body = filters + '<dl class="metric-grid traffic-metrics">' + metrics + '</dl>'
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
        quota = readable_traffic(current['totalGB']) if current['totalGB'] else '不限额'
        content = ('<div class="hero-row"><h2>Clash 订阅</h2><span class="' + state_class + '">' + status + '</span></div>'
                   '<p>到期时间 ' + expiration(current) + '</p><p>累计使用 ' + readable_traffic(total) + ' / ' + quota + '</p>' +
                   copy_field('订阅链接', HOME + '/sub/' + account['subscription'], 'subscription-url'))
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


CLIENT_EDITOR_CSS = """
.row-actions{width:1%;white-space:nowrap}
.row-actions>a,.row-actions>details:not([open]){display:inline-block;vertical-align:top;margin-right:12px}
.row-actions>details[open]{margin-top:8px}
.edit-link{font-weight:650;text-decoration:none}
.edit-link:hover{text-decoration:underline}
.client-editor{max-width:830px}
.client-editor details{margin:4px 0 15px;border-top:1px solid var(--line);padding-top:12px}
.client-editor details summary{cursor:pointer;color:var(--accent);font-weight:650;margin-bottom:12px}
.client-editor .surface{margin-bottom:12px}
.client-editor .actions .edit-link{padding:9px 4px}
.editor-ops{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:14px}
@media(max-width:650px){.row-actions{width:auto}}
@media(max-width:560px){.client-editor .field-row,.editor-ops{grid-template-columns:1fr;gap:0}}
"""


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



ADMIN_LIVE_SCRIPT = '(() => {\n    const list = document.getElementById(\'live-subscriptions\');\n    if (!list) return;\n    const search = document.getElementById(\'traffic-user-search\');\n    const results = document.getElementById(\'traffic-user-results\');\n    const filters = new URLSearchParams(window.location.search);\n    const url = new URL(\'/admin/operations/accounts.json\', window.location.origin);\n    if (filters.has(\'filter_client\')) url.searchParams.set(\'filter_client\', filters.get(\'filter_client\'));\n    let lastOptions = null;\n    let updating = false;\n    let lastCards = list.innerHTML;\n    let lastNames = JSON.stringify(Array.from(document.querySelectorAll(\'select[name="name"]\')[0]?.options ?? [], option => option.value));\n    async function refresh() {\n        if (document.hidden || updating) return;\n        updating = true;\n        try {\n            const response = await fetch(url, {cache: \'no-store\'});\n            if (!response.ok) return;\n            const {cards, names, options, selected_name: selectedName} = await response.json();\n            if (results?.hidden && document.activeElement !== search) {\n                if (typeof options === \'string\' && options !== lastOptions) {\n                    results.innerHTML = options;\n                    lastOptions = options;\n                }\n                if (typeof selectedName === \'string\' && search.defaultValue !== selectedName) {\n                    search.defaultValue = selectedName;\n                    search.value = selectedName;\n                }\n            }\n            if (cards !== lastCards && !list.contains(document.activeElement?.closest("details[open]"))) {\n                const expanded = new Set(Array.from(list.querySelectorAll(\'details[open]\'), detail => detail.closest(\'[data-account-id]\').dataset.accountId));\n                list.innerHTML = cards;\n                list.querySelectorAll(\'[data-account-id]\').forEach(card => {\n                    const detail = card.querySelector(\'details\');\n                    if (detail) detail.open = expanded.has(card.dataset.accountId);\n                });\n                lastCards = cards;\n            }\n            const currentNames = JSON.stringify(names);\n            if (currentNames !== lastNames) {\n                document.querySelectorAll(\'select[name="name"]\').forEach(select => {\n                    const selected = select.value;\n                    const options = names.map(name => new Option(name, name));\n                    select.replaceChildren(...options);\n                    if (names.includes(selected)) select.value = selected;\n                    else if (selected || filters.has(\'filter_client\')) select.selectedIndex = -1;\n                });\n                lastNames = currentNames;\n            }\n        } catch (error) {\n            if (!(error instanceof TypeError || error instanceof SyntaxError)) throw error;\n        } finally {\n            updating = false;\n        }\n    }\n    window.setInterval(refresh, 3000);\n    document.addEventListener(\'visibilitychange\', refresh);\n})();\n\n'


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
                         "default-src 'none'; style-src 'unsafe-inline'; script-src 'self'; img-src 'self'; form-action 'self'; frame-ancestors 'none'; base-uri 'none'" +
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
        if path == '/portal.js' and self.command in ('GET', 'HEAD'):
            versioned = urllib.parse.urlsplit(self.path).query == 'v=' + PORTAL_SCRIPT_VERSION
            self.send(200, PORTAL_SCRIPT, 'application/javascript; charset=utf-8',
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
