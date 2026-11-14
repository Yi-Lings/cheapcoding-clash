# Cheapcoding Clash

这是 Cheapcoding VPN 的可移植部署版。包含星海宣传页、登录页、用户面板、管理员面板、Mihomo/Clash 订阅、Hiddify 分流规则、流量统计、3x-ui 初始化和 Nginx HTTPS 配置。

## 架构

```text
浏览器 → Nginx HTTPS :443 → 127.0.0.1:17764 订阅面板
                            └→ 127.0.0.1:17761 3x-ui 管理 API
浏览器访问 /admin/ → Nginx → 127.0.0.1:17761 原生 3x-ui
客户端访问 /sub/<订阅密钥> → Nginx → 订阅面板 → 返回 Mihomo YAML
客户端代理流量 /connect → Nginx → 127.0.0.1:17763/vpn/connect → Xray VLESS WS
systemd 定时器 → 订阅面板 collect-traffic → 3x-ui → traffic.db
```

## 在新服务器安装

需要 Debian 12+ 或 Ubuntu 22.04+、systemd、Python 3.10+、amd64 或 arm64。先让域名解析到新 VPS，并在云防火墙和系统防火墙放行 TCP 80、443。若添加了 AAAA 记录，也要确认 IPv6 可访问。SSH 按你的运维方式放行。安装脚本使用 Nginx 和 Certbot 签发证书，依赖可访问 GitHub 发布资产和发行版软件源。

```bash
sudo apt-get update
sudo apt-get install -y git python3
git clone https://github.com/Yi-Lings/cheapcoding-clash.git
cd cheapcoding-clash
sudo bash deploy/install.sh vpn.example.com ops@example.com example.com
sudo cat /etc/cheapcoding-clash/admin-credentials
```

如果拿到的是源码压缩包，解压后进入 `cheapcoding-clash` 目录，直接运行安装脚本。

第一个参数是站点域名，第二个是 ACME 邮箱，第三个是直连域名，默认与站点域名相同。示例里的 `example.com` 及其子域名会按规则直连。可选第四个参数为新 VPS 公网 IP，用于配置同机网站的 HTTPS 回环特例。用自有域名部署时，把示例值换成自己的值。

安装器固定使用 3x-ui **v3.11.0** 的官方发布包，并按架构校验 SHA-256。管理员密码和本地 token 在安装时随机生成。打开 `https://你的域名/`，点「去连接」后，用管理员凭据登录。进入「开通账号」创建用户，把专属激活链接单独发给对方。用户设置密码后，即可从面板复制订阅。通过「使用教程」安装 Hiddify 或 FlClash，并导入对应的分流规则。

### 3x-ui 怎么连

| 环节 | 实现 |
|---|---|
| 3x-ui 服务 | `clash-panel.service` 用独立系统账号运行。Web 监听 `127.0.0.1:17761`，Base Path 为 `/admin/`。 |
| API token | 初始化脚本用该版本的 `x-ui setting -getApiToken -tokenName ccvpn-portal -tokenScope admin` 生成 token。订阅面板从 `/var/lib/clash-portal/panel-token` 读取，权限为 `0600`，并以 `Authorization: Bearer …` 请求 `/admin/panel/api/…`。公网反代会清除传给原生 3x-ui 的 Authorization 请求头。 |
| 管理员登录 | 站点 `/login` 把匹配配置的管理员用户名交给本机 3x-ui 登录接口，处理 CSRF 和可选两步验证。3x-ui 颁发的 Cookie 用于管理页鉴权。普通用户的密码哈希、会话和兑换码由面板的 `portal.db` 管理。 |
| 客户端管理 | 面板通过 `clients/add`、`clients/list`、`clients/get/:email`、`clients/update/:email`、`clients/del/:email`、`clients/resetTraffic/:email` 等 API 管理入站 ID 1 上的用户。用户名是 3x-ui 的 client email 字段，面板以 3x-ui 客户端记录 ID 对应本地账号。 |
| Xray 入站 | 入站 ID 1，VLESS，监听 `127.0.0.1:17763`。WebSocket 内部路径为 `/vpn/connect`，Nginx 把公网 `/connect` 映射过去，并在 :443 终止 TLS。 |
| 流量统计 | `clash-traffic.timer` 每 60 秒读 3x-ui 累计上下行，以增量写入 `traffic.db`。北京时间跨日归档，历史从首次采集开始。 |

只需公网开放 HTTPS 和 ACME 所需的 HTTP，3x-ui API、Xray 入站和订阅面板端口都留在回环地址。WireGuard 不在这条链路中。客户端应使用规则模式才能应用直连规则。Hiddify 的分流规则需在客户端单独导入。

### 配置与检查

- `/etc/cheapcoding-clash/portal.env` 保存站点域名、直连域名和管理员用户名。可以增加 `CCVPN_SUBSCRIPTION_NAME`、`CCVPN_GROUP`、`CCVPN_NODE` 自定义订阅显示名。修改后重启 `clash-portal`，定时采集下次运行时会读取同一配置。
- `/etc/nginx/sites-available/clash-subscription` 保存对外路由。修改域名时同时更新 Nginx、TLS 证书和 `portal.env`。若启用了第四个参数的同机回环规则，换 IP 时也要更新 3x-ui 的 Xray 路由。
- `/var/lib/clash-panel/db/x-ui.db` 保存 3x-ui 用户、入站、token 和 Xray 模板。原生 3x-ui 可在管理后台点「打开 3x-ui」进入。
- `/var/lib/clash-portal/portal.db` 保存激活记录、账号登录、订阅映射和兑换码。 `/var/lib/clash-portal/traffic.db` 保存按日统计。备份均包含敏感数据，放在权限受限的位置。
- Certbot 续期使用 webroot。部署包安装了续期后检查并重载 Nginx 的 hook。

```bash
sudo nginx -t
sudo systemctl is-active nginx clash-panel clash-portal clash-traffic.timer
sudo journalctl -u clash-panel -u clash-portal -u clash-traffic.service -n 60
curl -I https://vpn.example.com/
curl -I https://vpn.example.com/login
```

如果安装停在证书步骤，检查 DNS、AAAA、80 端口和 `certbot certificates`。安装器会拒绝覆盖已经存在的安装路径。修好 DNS 或防火墙后，直接运行 `sudo bash deploy/enable-https.sh ops@example.com`，继续申请证书并启动服务。已有实例的更新请先备份，使用相同版本做迁移验证。

### 隔离联调

已安装相关依赖时，可以用原版 3x-ui 发行目录运行 `sudo bash deploy/smoke-test.sh /path/to/x-ui`。测试使用独立网络命名空间、空数据库和临时证书。

## 搬迁已有账号

如果只需重新开通用户，在新机按前面的方式初始化即可。如果要保留现有订阅、密码和历史流量，使用同版本部署新机，并迁移私有数据。保留原域名时，客户端的订阅地址可以保持一致。

1. 在旧机的维护窗口停止 `clash-traffic.timer`、`clash-traffic.service`、`clash-portal` 和 `clash-panel`，冻结账号变更和实时计数。运行 `sudo bash deploy/backup.sh /root/ccvpn-move.tar.gz`。脚本使用 SQLite backup API 捕获各数据库，包括已提交但仍在 WAL 的内容。
2. 用可信的 SSH/SCP 把备份传到新机的 root 私有目录。新机按上述命令安装。原域名仍指向旧机时，可以先用临时域名验收，正式切换时为原域名申请证书并更新站点配置。可以先用 DNS-01 预签原域名证书，或在切换 DNS 后用 HTTP-01 签发。
3. 在新机停止应用和采集，备份新机初始数据。把迁移包解到 root 私有临时目录，按下面命令恢复。旧机生成的 token 要与旧 3x-ui 数据库成对复制。 `portal.env` 里的管理员用户名须与恢复的 3x-ui 用户一致，站点域名用新机最终域名。恢复的 3x-ui Xray 路由若含旧公网 IP，需在启动前更新相应 IP。

```bash
sudo -i
systemctl stop clash-traffic.timer clash-traffic.service clash-portal clash-panel
umask 077
restore=$(mktemp -d)
tar -xzf /root/ccvpn-move.tar.gz -C "$restore"
install -m 0600 -o clashsvc -g clashsvc "$restore/panel/x-ui.db" /var/lib/clash-panel/db/x-ui.db
install -m 0600 -o clashportal -g clashportal "$restore/portal/portal.db" /var/lib/clash-portal/portal.db
install -m 0600 -o clashportal -g clashportal "$restore/portal/traffic.db" /var/lib/clash-portal/traffic.db
install -m 0600 -o clashportal -g clashportal "$restore/portal/panel-token" /var/lib/clash-portal/panel-token
# 清除已停止的目标实例留下的 WAL/SHM，避免与替换后的数据库混用。
rm -f /var/lib/clash-panel/db/x-ui.db-{wal,shm} /var/lib/clash-portal/{portal,traffic}.db-{wal,shm}
# 此时检查 /etc/cheapcoding-clash/portal.env 和 3x-ui 路由中的公网 IP。
systemctl start clash-panel clash-portal clash-traffic.timer
rm -rf -- "$restore"
```

4. 验证首页、管理员登录、用户订阅刷新和真实代理请求，再切换 DNS。新机初始化时生成的管理员密码在恢复旧数据库后失效，登录使用旧面板凭据。切换确认后再退役旧机。恢复的数据库、token 和备份包只在私有渠道传递。

## 源码与许可

`portal/` 是订阅面板和静态素材。 `deploy/` 是部署、初始化、备份、Nginx 与 systemd 配置。3x-ui/Xray 通过固定的官方发行包安装，上游代码与许可证由上游仓库维护。本仓库的部署代码采用 GPL-3.0。

- [3x-ui 上游](https://github.com/MHSanaei/3x-ui)
- [3x-ui v3.11.0 发行版](https://github.com/MHSanaei/3x-ui/releases/tag/v3.11.0)
