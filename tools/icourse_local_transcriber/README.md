# iCourse Local Transcriber

给定本地 `icourse` 视频目录，自动执行两步：

1. 扫描课程目录下的视频文件
2. 将视频转成音频文件，并转写为文本

本工具复用仓库内的 `src/transcriber.py`（SenseVoice + silero VAD）。

## 目录结构（输出）

默认输出到 `<icourse_dir>/_asr_output`，按课程分组：

```text
_asr_output/
  35472-高等数学A/
    audio/
      123456_第一讲.wav
    transcripts/
      123456_第一讲.txt
```

## 依赖

```bash
pip install -r requirements.txt
```

并确保本机有 `ffmpeg`：

```bash
ffmpeg -version
```

还需要可用的 ASR 模型文件（与主项目一致）：

- `SENSEVOICE_MODEL_DIR`（默认：`sherpa-onnx-sense-voice-zh-en-ja-ko-yue-2024-07-17`）
- `SILERO_VAD_PATH`（默认：`silero_vad.onnx`）

可通过 `--env-file` 加载对应环境变量。

## 用法

### 1) 扫描并转写

```bash
python tools/icourse_local_transcriber/transcribe_folder.py tools/course
```

### 2) 指定输出目录

```bash
python tools/icourse_local_transcriber/transcribe_folder.py tools/course \
  --out-dir tools/course_asr
```

### 3) 只提取音频，不转写

```bash
python tools/icourse_local_transcriber/transcribe_folder.py tools/course --audio-only
```

### 4) 仅列出可处理视频

```bash
python tools/icourse_local_transcriber/transcribe_folder.py tools/course --list-only
```

### 5) 多进程并发转写（例如 4 进程）

```bash
python tools/icourse_local_transcriber/transcribe_folder.py tools/course --workers 4
```

## 参数

```text
icourse_dir       课程根目录（必填）
--out-dir         输出根目录（默认 <icourse_dir>/_asr_output）
--env-file        可选 .env 文件路径（用于模型相关环境变量）
--audio-format    音频格式：wav/mp3（默认 wav）
--overwrite       覆盖已存在音频/文本
--audio-only      只抽音频，不转写
--list-only       仅列出视频，不处理
--non-recursive   每个课程目录仅扫描一层
--workers         并发进程数（默认 1）
```
