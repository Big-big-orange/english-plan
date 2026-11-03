# english-plan

12 个月英语自学计划（旅游英语 + 远程工作英文会议），每天 07:00 自动发一封「今天做什么」到邮箱。

- `english-plan.md` —— 52 周打卡表，每天一条具体动作，App 名字写死在里面
- `english-mail-reminder.py` —— 读 md，生成邮件，通过 163 SMTP 发送（纯标准库）
- `.github/workflows/daily-mail.yml` —— 每天 07:00（北京时间）在云端跑一次，不依赖本机开机

## 一次性配置（10 分钟）

### 1. 建仓库并推送

```bash
# 在 GitHub 网页上新建仓库，名字 english-plan，选 Private
cd D:/Code/PI/english-plan-repo
git remote add origin https://github.com/Big-big-orange/english-plan.git
git branch -M main
git push -u origin main
```

> 第一次推送会弹浏览器要求登录，用你的 GitHub 账号授权即可（Git Credential Manager）。

### 2. 加 Secret

仓库 → **Settings** → **Secrets and variables** → **Actions** → **New repository secret**

- Name：`ENGLISH_MAIL_CONFIG`
- Secret：把下面这段填进去（换成你的授权码）

```json
{
  "smtp_host": "smtp.163.com",
  "smtp_port": 465,
  "user": "bigbigrao_model@163.com",
  "auth_code": "你的16位授权码",
  "to": "bigbigrao_model@163.com",
  "sender_name": "English Coach",
  "start_date": "2026-11-03",
  "trip_date": "2027-11-02"
}
```

### 3. 手动跑一次验证

仓库 → **Actions** → 左侧 `english-mail` → 右侧 **Run workflow** → 绿色按钮

跑完看日志，最后一行是 `已发送: [英语] ...` 就成功了。

## 日常怎么用

| 想做什么 | 怎么做 |
|---|---|
| 每天自动收信 | 什么都不用做，07:00 自动发 |
| 今天没收到 | Actions 里看那次 run 的日志，或补跑一次 Run workflow |
| 改计划 | 直接改 `english-plan.md` 里对应那周的文字，push 上去，第二天生效 |
| 打卡 | 把 `☐` 改成 `✅`，commit 一下 |
| 换邮箱/授权码 | 改 Secret，不用改代码 |

```bash
git add -A && git commit -m "week(07): 5/7 天，卡点=didja 连读" && git push
```

## 需要注意

- **延迟**：GitHub 的 cron 不是精确闹钟，高峰期可能晚 5–30 分钟，属正常。
- **60 天不活跃会被停**：仓库连续 60 天没有任何提交，定时任务会被自动禁用。每周打卡 commit 一次就不会触发。
- **额度**：私有仓库免费 2000 分钟/月，这个任务每天约 20 秒，一个月不到 10 分钟。
- **失败会通知**：脚本报错会让 job 失败，GitHub 会发邮件提醒你（就发到你 GitHub 注册邮箱）。
- **别把授权码提交进仓库**：`.gitignore` 已屏蔽 `english-mail.config.json`，Secret 里的值只有 Actions 能读。

## 本地想手动发一封

```bash
cp english-mail.config.example.json english-mail.config.json   # 首次
python english-mail-reminder.py --dry-run     # 预览
python english-mail-reminder.py --test        # 真发
```
