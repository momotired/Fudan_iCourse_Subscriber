# iCourse Video Downloader

独立工具：提取本仓库中的 WebVPN + iCourse 验证能力，按课程/课次下载录播视频到本地目录（增量下载，默认跳过已下载课次）。

## 目录结构

```text
tools/icourse_video_downloader/
├── downloader.py
├── .env
└── README.md
```

## 依赖

本工具复用仓库根目录依赖：

```bash
pip install -r requirements.txt
```

## 配置

编辑本目录下的 `.env`：

```env
StuId=你的学号
UISPsw=你的UIS密码
COURSE_IDS=35472,30251
DOWNLOAD_DIR=tools/course
```

说明：

- `StuId`、`UISPsw`：登录 WebVPN/iCourse 必填
- `COURSE_IDS`：默认课程 ID（可被命令行参数覆盖）
- `DOWNLOAD_DIR`：默认下载目录（可被命令行参数覆盖）
- 默认每门课目录格式：`课程号-课程标题`，例如 `35472-高等数学A`

增量下载规则：

- 程序会扫描本地课程目录下的 `*.mp4`
- 从文件名提取 `sub_id`（如 `123456_xxx.mp4` 或 `123456-xxx.mp4`）
- 远端课次 `sub_id` 若本地已存在，则默认跳过，仅下载缺失课次

## 用法

1. 仅列出可下载课次（不下载）：

```bash
python tools/icourse_video_downloader/downloader.py --course-ids 35472 --list-only
```

2. 下载某门课全部有回放的课次（自动跳过已下载）：

```bash
python tools/icourse_video_downloader/downloader.py --course-ids 35472
```

3. 只下载指定课次：

```bash
python tools/icourse_video_downloader/downloader.py --course-ids 35472 --sub-ids 10001,10002
```

4. 覆盖已存在文件并指定输出目录：

```bash
python tools/icourse_video_downloader/downloader.py \
  --course-ids 35472 \
  --overwrite \
  --out-dir /tmp/icourse_videos
```

5. 不传 `--course-ids`，直接按 `.env` 里的 `COURSE_IDS` 全量检查并补齐未下载：

```bash
python tools/icourse_video_downloader/downloader.py
```

## 参数

```text
--env-file       指定 .env 路径（默认 tools/icourse_video_downloader/.env）
--course-ids     课程 ID 列表（逗号分隔，不传则使用 .env 的 COURSE_IDS）
--sub-ids        课次 sub_id 列表（逗号分隔）；不填则下载该课程全部可回放课次
--out-dir        输出目录（默认 .env 的 DOWNLOAD_DIR，否则 tools/course）
--list-only      仅列出课次
--overwrite      覆盖已存在文件
--login-retries  登录重试次数
--sleep          每次下载后暂停秒数
```

## 合规提醒

请仅在校规与课程平台使用规范允许的范围内，出于个人学习目的使用本工具。请勿传播、二次分发或用于任何未授权用途。
