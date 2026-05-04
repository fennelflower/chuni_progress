# CHUNITHM Progress

由于我看隔壁舞萌的 bot 一般都有某一等级的将牌完成表, 然后进行了一番搜索发现中二竟然没有人开发类似功能, 于是 vibe coding 了这个项目. 

**这个项目实现了**: 
- 把 CHUNITHM 成绩 CSV 导入 PostgreSQL
- 抓取 wiki 曲目信息和曲绘
- 生成指定等级、指定达成评级的进度图
- 项目还包含一个基于 NapCat / OneBot HTTP 的 QQ bot, 可以接收 CSV、维护用户成绩、生成完成表或未完成表. 

由于制作等级完成表肯定先要有曲目信息, 但是我无法获取曲目信息, 所以只能通过抓取 wikiwiki 网站的方式来获取, 这里有一个问题就是只能获取日服信息, 国服信息则是通过用户上传的成绩来反推定数的, 所以如果你没有把整个等级的谱面全部打一遍, 那么你的分数 csv 中就会缺少一部分谱面信息, 进而导致最后的数据库中只包含你玩过的曲目的国服定数, 生成的国服进度图片会少歌. 可以使用日服定数表来生成, 日服定数表根据 `fetch_image.py` 从 wikiwiki 抓取数据, 会包含所有曲目信息

## 功能概览

- 从 wiki 曲目列表抓取曲目信息, 并进入曲目详情页抓取曲绘 (注意这个抓取曲绘比较粗糙, 判定是否为曲绘是通过这个图片是否为正方形来判断的, 可能会抓取到页面中长得像正方形的图片, 可以在 config 文件的`jacket_max_aspect_ratio`一项中修改匹配参数) . 
- 支持落雪查分器和水鱼查分器导出的 CSV. 
- 生成等级进度表, 例如 `15 SSS cn`. 
- 生成未完成进度表, 只显示未达到目标评级的谱面. 
- 支持国服定数和日服定数两套展示方式. 
- bot 支持普通用户手动更新成绩, 可信用户/admin 上传 CSV (防止普通用户上传一些病毒文件) . 

## 文件结构

```text
bot/                                QQ bot HTTP 回调和消息处理
config/config.example.json          配置模板
pyrightconfig.json                  Pylance/Pyright 导入路径配置
data/raw/                           本地 CSV 和 bot 上传缓存
data/jackets/                       抓取的曲绘
data/outputs/                       生成的图片
scripts/auth.py                     用户注册、登录、权限管理
scripts/db.py                       PostgreSQL 连接
scripts/fetch_image.py              抓取 wiki 曲目信息和曲绘
scripts/generate_board.py           生成进度图
scripts/init_db.py                  初始化数据库表
scripts/rebuild_cn_constants.py     重新导入 CSV 以补全/更新国服定数
scripts/refetch_bad_jackets.py      重新抓取可疑或错误曲绘
scripts/upload_score.py             导入成绩 CSV
sql/init_tables.sql                 建表 SQL
```

## 安装

```powershell
pip install -r requirements.txt
```

创建本地配置: 

```powershell
copy config\config.example.json config\config.json
```

然后编辑 `config/config.json`, 填写本机 PostgreSQL 密码、NapCat API 地址和 token (如果你想使用 bot 功能). 

## 数据库初始化

先在 PostgreSQL 中创建数据库: 

```sql
CREATE DATABASE chunithm_progress;
```

然后运行: 

```powershell
python scripts/init_db.py
```

默认会创建一个 admin 账号: 

```text
account: admin
password: admin
user_id: 1
group: admin
```

首次部署后建议尽快修改默认密码. 

## 抓取曲目信息和曲绘

先在 `config/config.json` 中配置 wiki 列表页 (注意网站可能更新其链接, 请粘贴能够访问的wikiwiki链接) : 

```json
"wiki_song_list_urls": [
  "https://wikiwiki.jp/chunithmwiki/CHUNITHM%20X-VERSE-X%20%E6%A5%BD%E6%9B%B2%E4%B8%80%E8%A6%A7%28Lv%E9%A0%861%29#Lv15"
]
```

运行: 

```powershell
python scripts/fetch_image.py
```

只导入曲目信息、不下载曲绘 (第一次使用不想下载曲绘可以运行此命令, 之后想要显示曲绘可以去除 --no-download 参数再运行一次) : 

```powershell
python scripts/fetch_image.py --no-download
```

## 曲绘维护脚本

```powershell
python scripts/refetch_bad_jackets.py
```

`refetch_bad_jackets.py` 用于重新抓取可疑曲绘. 它会检查已有曲绘是否不存在、打不开, 或长宽比超过 `config/config.json` 中的 `jacket_max_aspect_ratio`; 对这些可疑项, 脚本会根据数据库里的 `source_url` 回到曲目详情页重新寻找更像正方形曲绘的图片, 下载后更新 `songs.jacket_path`, 并删除旧的坏图片. 适合发现进度图里某些曲绘明显抓错时运行.

## CSV 适配说明

`scripts/upload_score.py` 会通过 CSV 表头自动识别来源. 

落雪查分器格式: 

```text
id,song_name,level,level_index,score,rating,...
```

特点: 

- `level_index` 表示谱面难度, `0-4` 分别对应 `basic/advanced/expert/master/ultima`. 
- 可能包含同一谱面的多次游玩记录. 
- 导入时同一用户、同一谱面只保留最高分. 

水鱼查分器格式: 

```text
排名,乐曲名,难度,定数,分数,Rating
```

特点: 

- 一行通常对应一个谱面的当前成绩. 
- `难度` 字段是 `14 / 14+ / 15` 这种等级, 不是 `master/ultima`. 
- 导入时会用 `乐曲名 + 定数` 到数据库 `songs` 表中反推真实谱面难度.   

**注意事项: 第一次使用先用落雪查分器的 csv 进行 upload , 因为落雪的 csv 记录了难度名信息, 可以进行建表, 之后才可以使用水鱼来更新成绩, 否则无法建表, 也就无法使用该项目的功能了.**


导入示例: 

```powershell
python scripts/upload_score.py data/raw/chunithm-scores.csv
python scripts/upload_score.py data/raw/中二节奏.csv
```

导入范围由 `config/config.json` 控制 (注意如果你的 csv 本身就没有某一难度的数据, 你的数据当然不可能被写入表中) : 

```json
"import_difficulties": ["master", "ultima"],
"min_level": 14.0
```

## 生成图片

启动图形界面: 

```powershell
python scripts/generate_board.py
```

图中可以选择: 

- 等级, 例如 `15` 或 `15+`
- 最低目标评级, 例如 `SSS`
- 定数来源, `cn` 为国服定数, `jp` 为日服定数
- 是否只显示未完成曲目

## QQ Bot

启动 bot HTTP 服务: 

```powershell
python bot/bot_main.py
```

默认监听: 

```text
http://127.0.0.1:8088/
```

NapCat 网络配置建议: 

- 新建 `HTTP 客户端`, URL 填 `http://127.0.0.1:8088/`, 用于事件上报. 
- 新建 `HTTP 服务器`, 端口填 `3001` 或你在 `config.json` 中设置的端口, 用于 bot 调 NapCat API 发消息. 
- `config.json` 中的 `napcat_access_token` 要填写 NapCat HTTP 服务器的 token. 

群聊中需要使用结构化 @ 机器人后再输入命令；私聊不需要 @. 

### 用户命令

```text
/help
```

查看当前账号可用命令. 

```text
/register account password
```

注册普通用户并绑定当前 QQ. 普通用户组为 `normal_users`. 

```text
/login account password
```

登录并绑定当前 QQ. 

```text
/logout
```

解绑当前 QQ. 

```text
/whoami
```

查看当前绑定账号、用户 ID 和用户组. 

```text
/upsert "song name" master 1009000
```

手动更新一条成绩. 所有已登录用户都可以使用. 

```text
/chuni_board 15 SSS cn
/中二等级完成表 15 SSS cn
```

生成等级完成表. 

```text
/chuni_unfinished 15 SSS cn
/中二未完成表 15 SSS cn
/中二等级未完成表 15 SSS cn
/中二未完成进度表 15 SSS cn
```

生成只包含未达到目标评级谱面的进度表. 

### honored_users / admin 命令

```text
/chuni_upload
/中二上传
```

进入 5 分钟 CSV 上传等待状态. 发送 CSV 后, bot 会导入成绩并回复导入数量. 

### admin 命令

```text
/grant account honored_users
/grant account normal_users
```

将用户升为 `honored_users`, 或从 `honored_users` 降回 `normal_users`. 

权限设计: 

- `normal_users`: 可以注册、登录、生成图片、手动 `/upsert` 成绩. 
- `honored_users`: 可以上传 CSV 更新自己的成绩. 
- `admin`: 可以上传 CSV, 并调整其他账号的用户组. 

## 缓存策略

- 同一用户上传同一个 CSV 时, 会跳过重复导入. 
- 同一用户对同一个 CSV、同一命令参数生成图片时, 会复用上一张图片. 
- 每个用户只保留一张 bot 生成图片, 新任务会覆盖旧图. 
