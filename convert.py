import os
import sys
import shlex
import subprocess
import json
import re
import locale
import signal
import argparse
from typing import Tuple
from typing import NamedTuple
import logging
import winsound
from tqdm import tqdm
info, debug, error, warn, excep = logging.info, logging.debug, logging.error, logging.warning, logging.exception

def sec_hum(sec: int|float) -> str:
    '''将秒数`sec`转换为 H:M:S.N 格式

    10.1 -> '00:00:10.1' 
    62.441 -> '00:01:02.441'
    62.4415 -> '00:01:02.441'
    102 -> '00:01:42.0'
    '''
    sec, d = int(sec), str(sec).split('.')[1][:3] if str(sec).find('.') != -1 else '0'
    h, r = sec // 3600, sec % 3600
    return f"{sec//3600:02d}:{r // 60:02d}:{r % 60:02d}.{d}"

def size_hum(n: int, size: float=1024.0) -> str:
    '''将容量数字`n`转换为方便阅读的格式，默认进制`size`是1024。
    如果`n`大到无法用`YB`表示，则原样返回
    1 Kilobyte (KB) = 1024 Bytes
    1 Megabyte (MB) = 1024 KB
    1 Gigabyte (GB) = 1024 MB
    1 Terabyte (TB) = 1024 GB
    1 Petabyte (PB) = 1024 TB
    1 Exabyte (EB) = 1024 PB
    1 Zettabyte (ZB) = 1024 EB
    1 Yottabyte (YB) = 1024 ZB
    '''
    neg = 0 if n >= 0 else 1
    old = n
    n = abs(n)
    units = ['B', 'KB', 'MB', 'GB', 'TB', 'PB', 'EB', 'ZB', 'YB']
    for s in units:
        if n / size  < 1:
            return f'{'-' * neg}{n:.2f}{s}'
        n /= size
    else:
        return '-'* neg + str(old)

bitrate_hum = lambda x: f'{int(x/1000)}kb/s'


def shorten(s: str, max_length: int, placeholder: str = '...') -> str:
    '''将字符串`s`缩短为最长`max_length`个字符长，中间部分省略用`placeholder`代替 

    shorten('123456789', 5) -> '1...9'
    shorten('123456789', 6) -> '1...89'
    shorten('123456789', 7) -> '12...89'
    shorten('123456789', 3) -> '123'
    shorten('123456789', 0) -> ''
    shorten('123456789', 10) -> '123456789'
    '''
    if len(s) <= max_length:
        return s
    if max_length <= len(placeholder):
        return s[:max_length]  # 无法保留头尾时，直接截断
    
    # 计算头部和尾部的长度
    remaining = max_length - len(placeholder)
    head_length = remaining // 2
    tail_length = remaining - head_length
    
    return f"{s[:head_length]}{placeholder}{s[-tail_length:]}"

class ProgressNotifier(object):
    _DURATION_RX = re.compile(rb'Duration: (\d{2}):(\d{2}):(\d{2})\.\d{2}')
    _PROGRESS_RX = re.compile(rb'time=(\d{2}):(\d{2}):(\d{2})\.\d{2}')
    _SOURCE_RX = re.compile(b"from '(.*)':")
    _FPS_RX = re.compile(rb'(\d{2}\.\d{2}|\d{2}) fps')

    @staticmethod
    def _seconds(hours, minutes, seconds):
        return (int(hours) * 60 + int(minutes)) * 60 + int(seconds)

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        if self.pbar is not None:
            self.pbar.close()

    def __init__(self, file=None, encoding=None, tqdm=tqdm):
        self.lines = []
        self.line_acc = bytearray()
        self.duration = None
        self.source = None
        self.started = False
        self.pbar = None
        self.fps = None
        self.file = file or sys.stderr
        self.encoding = encoding or locale.getpreferredencoding() or 'UTF-8'
        self.tqdm = tqdm

    def __call__(self, char, stdin = None):
        if isinstance(char, str):
            char = char.encode('utf8', errorerrors='replace')
        if char in b"\r\n":
            line = self.newline()
            if self.duration is None:
                self.duration = self.get_duration(line)
            if self.source is None:
                self.source = self.get_source(line)
            if self.fps is None:
                self.fps = self.get_fps(line)
            self.progress(line)
        else:
            self.line_acc.extend(char)
            if self.line_acc[-6:] == bytearray(b"[y/N] "):
                print(self.line_acc.decode(self.encoding), end="", file=self.file)
                self.file.flush()
                if stdin:
                    stdin.put(input() + "\n")
                self.newline()

    def newline(self):
        line = bytes(self.line_acc)
        self.lines.append(line)
        self.lines[:] = self.lines[-2:]
        self.line_acc = bytearray()
        return line

    def get_fps(self, line):
        search = self._FPS_RX.search(line)
        if search is not None:
            return round(float(search.group(1)))
        return None

    def get_duration(self, line):
        search = self._DURATION_RX.search(line)
        if search is not None:
            return self._seconds(*search.groups())
        return None

    def get_source(self, line):
        search = self._SOURCE_RX.search(line)
        if search is not None:
            return shorten(os.path.basename(search.group(1).decode(self.encoding)), 50, '[...]')
        return None

    def progress(self, line):
        search = self._PROGRESS_RX.search(line)
        if search is not None:

            total = self.duration
            current = self._seconds(*search.groups())
            unit = " seconds"

            if self.fps is not None:
                unit = " frames"
                current *= self.fps
                if total:
                    total *= self.fps

            if self.pbar is None:
                self.pbar = self.tqdm(
                    desc=self.source,
                    file=self.file,
                    total=total,
                    dynamic_ncols=True,
                    unit=unit,
                    ncols=0,
                    ascii=os.name=="nt",  # windows cmd has problems with unicode
                )

            self.pbar.update(current - self.pbar.n)

def call_ffmpeg(cmd: str, use_tqdm: bool = False):
    if not use_tqdm:
        subprocess.run(shlex.split(cmd), encoding='utf8')
    else:
        try:
            with ProgressNotifier(file=sys.stderr, encoding='utf8', tqdm=tqdm) as notifier:
                #p = subprocess.Popen(cmd, stderr=subprocess.PIPE, bufsize=1, text=True, encoding='utf8')
                p = subprocess.Popen(cmd, stderr=subprocess.PIPE, bufsize=-1)
                while True:
                    out = p.stderr.read(1)
                    if out:
                        #notifier(out.decode('utf8', errors='replace'))
                        notifier(out)
                    if p.poll() is not None:
                        break
        except KeyboardInterrupt:
            warn(f'KI exit with {signal.SIGINT + 128}')
        except Exception as err:
            error(f'Unexpected exception: {err}')
            p.kill()
            raise
        else:
            if p.returncode != 0:
                error(notifier.lines[-1].decode(notifier.encoding))
            debug(f'\tnormal exit with {p.returncode}')


def calc_bitrate(d:json, a: json, file_size: int, duration: float = 0.0) -> int:
    #debug(f'\t\tcalc bitrate ...')
    bitrate = 0
    if (f := a.get('format')):
        bitrate = int(f.get('bit_rate', '0'))
        if bitrate:
            debug(f'\t\tbitrate in format: {bitrate}')
    if not bitrate:
        if (tags := d.get('tags')):
            bitrate = int(tags.get('BPS-eng', '0'))
            if bitrate:
                debug(f'\t\tbitrate in tags["BPS-eng"]: {bitrate}')
    if not bitrate:
        if not duration:
            if (_duration_ts := d.get('duration_ts')) is not None:
                if (_time_base := d.get('time_base')) is not None:
                    _time_base_a, _time_base_b = _time_base.split('/', 2)
                    duration = int(_duration_ts) * int(_time_base_a) / int(_time_base_b)
        if duration:
            bitrate = file_size * 8 / duration
            debug(f'\t\tbitrate calc: {bitrate}')

    return bitrate

def hms2sec(d: str) -> float:
    '''
    将 `H:M:S.NNN` 形式的`d`字符串转换为以秒为单位的浮点数形式数字
    '''
    _h, _m, _s = d.split(':', 3)
    return int(_h) * 3600 + int(_m) * 60 + float(_s)

def calc_fps(d: json, duration: float = 0.0) -> int|float:
    fps = 0
    if (fps := d.get('r_frame_rate')) is None:
        if (fps := d.get('avg_frame_rate')) is None:
            if (tag := d.get('tags')) is not None:
                _frames = int(tag.get('NUMBER_OF_FRAMES-eng', '0'))
                if _frames and duration:
                    fps = _frames / duration
                    fps = fps if isinstance(fps, int) else round(fps, 3)
                    debug(f'\t\tfps calc: {fps}')
        else:
            if fps.find('/') != -1:
                _a, _b = fps.split('/', 2)
                fps = int(_a) / int(_b)
            else:
                fps = int(fps) if fps.find('.') == -1 else float(fps)
            fps = fps if isinstance(fps, int) else round(fps, 3)
            debug(f'\t\tfps in tags["avg_frame_rate"]: {fps}')

    else:
        if fps.find('/') != -1:
            _a, _b = fps.split('/', 2)
            fps = int(_a) / int(_b)
        else:
            fps = int(fps) if fps.find('.') == -1 else float(fps)
        fps = fps if isinstance(fps, int) else round(fps, 3)
        debug(f'\t\tfps in tags["r_frame_rate"]: {fps}')
    return fps

def calc_duration(d: json, a: json) -> str:
    '''返回形如 H:M:S.NNN 的时间长度
    '''
    #debug(f'\t\tcalc duration ...')
    duration = ''
    if d['codec_type'] == 'video':
        if (tags := d.get('tags')):
            duration = tags.get('DURATION-eng', '')
            if not duration:
                duration = tags.get('DURATION', '')
                if duration:
                    debug(f'\t\tvideo duration in tags["DURATION"]: {duration}')
            else:
                debug(f'\t\tvideo duration in tags["DURATION-eng"]: {duration}')
            if not duration:
                _frames = int(tags.get('NUMBER_OF_FRAMES-eng', '0'))
                _frm_per_sec = d.get('avg_frame_rate', '0')
                if _frm_per_sec.find('/') != -1:
                    _a, _b = _frm_per_sec.split('/', 2)
                    _frm_per_sec = int(int(_a) / int(_b))
                else:
                    _frm_per_sec = int(_frm_per_sec)
                if _frames and _frm_per_sec:
                    duration = sec_hum(_frames / _frm_per_sec)
                    debug(f'\t\tv duration calc: {duration}')
        elif (f := a.get('format')):
            duration = f.get('duration', '')
            if duration:
                duration = sec_hum(float(duration))
                debug(f'\t\tv duration in format: {duration}')
    elif d['codec_type'] == 'audio':
        if (tags := d.get('tags')):
            duration = tags.get('DURATION-eng', '')
            if not duration:
                duration = tags.get('DURATION', '')
                if duration:
                    debug(f'\t\ta duration in tags["DURATION"]: {duration}')
            else:
                debug(f'\t\ta duration in tags["DURATION-eng"]: {duration}')
            if not duration:
                _bps = int(tags.get('BPS-eng', '0'))
                _bytes = int(tags.get('NUMBER_OF_BYTES-eng', '0'))
                if _bps and _bytes:
                    duration = sec_hum(_bytes * 8 / _bps)
                    debug(f'\t\ta duration calc: {duration}')
        elif (f := a.get('format')):
            duration = f.get('duration', '')
            if duration:
                duration = sec_hum(float(duration))
                debug(f'\t\ta duration in format: {duration}')
        if not duration:
            if (_time_base := d.get('time_base')) is not None:
                _time_base_a, _time_base_b = _time_base.split('/', 2)
                if (_nb_frames := d.get('nb_frames')) is not None:
                    duration = int(_nb_frames) * int(_time_base_a) / int(_time_base_b)
                    duration = sec_hum(duration)
                    debug(f'\t\ta duration calc: {duration}')
    elif d['codec_type'] == 'subtitle':
        if (tags := d.get('tags')):
            duration = tags.get('DURATION-eng', '')
            if not duration:
                debug(f'\t\ts duration not found')
                # _bps = int(tags.get('BPS-eng', '0'))
                # _bytes = int(tags.get('NUMBER_OF_BYTES-eng', '0'))
                # if _bps and _bytes:
                #     duration = sec_hum(_bytes * 8 / _bps)
                #     debug(f'\t\ts duration calc: {duration}')
            else:
                debug(f'\t\ts duration in tags["DURATION-eng"]: {duration}')

    return duration

class InfoResult(NamedTuple):
    main_encoder: str
    main_bitrate: int
    main_fps: int|float
    main_resolution: str
    main_duration: str
    cmd_main_stream: str
    cmd_copy_stream: str
    desc: str

class Result(NamedTuple):
    is_valid: bool
    err: str
    info: InfoResult

def check_hevc(fname: str, ffprobe: str = 'ffprobe') -> Result:
    is_valid, err = False, ''
    _main_encoder, _main_bitrate, _main_fps, _main_resolution, _main_duration, _cmd_main_stream, _cmd_copy_stream, _desc = '', 0, 0, '', '', '', '', ''
    if not os.path.exists(fname) or os.stat(fname).st_size == 0:
        err = 'file not exist or 0 bytes'
        return is_valid, err, (_main_encoder, _main_bitrate, _main_fps, _main_resolution, _main_duration, _cmd_main_stream, _cmd_copy_stream, _desc)

    _old_size = os.stat(fname).st_size
    _cmd = f'{ffprobe} -v error -hide_banner -show_format -show_streams -of json -i "{fname}"'
    #debug(f'\t{_cmd=}')
    _rslt = subprocess.run(shlex.split(_cmd), capture_output=True, text=True, encoding='utf8')
    if _rslt.stdout:
        try:
            _i_v, _i_a, _i_s = 0, 0, 0  # index video audio subtitle 
            cmd_vf = ''
            j_data = json.loads(_rslt.stdout)
            for _i, _d in enumerate(j_data['streams']):
                _codec_name, _codec_type, _pix_fmt, _duration, _bitrate = _d['codec_name'], _d['codec_type'], _d.get('pix_fmt', 'N/A'), int(float(_d.get('duration', 0))), int(_d.get('bit_rate', 0) if _d.get('bit_rate', 'N/A') != 'N/A' else 0)
                if _duration:
                    _duration = sec_hum(_duration)
                if not _duration:
                    _duration = calc_duration(_d, j_data)
                if not _duration:
                    error(f'empty duration {_codec_name=} {_codec_type=}')
                assert _duration
                if not _bitrate:
                    _bitrate = calc_bitrate(_d, j_data, _old_size, hms2sec(_duration))
                if (not _main_encoder) and _codec_type == 'video' and _codec_name != 'mjpeg':
                    _main_encoder, _main_bitrate = _codec_name, _bitrate
                    _cmd_main_stream = f'-map 0:v:{_i_v} -c:v:{_i_v} hevc_amf '
                    _main_fps = calc_fps(_d, hms2sec(_duration))
                    if _main_fps > 32:
                        debug(f'\tfps {_main_fps} -> 30')
                        if not cmd_vf:
                            cmd_vf = '-vf "fps=30'
                        else:
                            cmd_vf += ',fps=30'
                    _main_resolution = f'{_d.get("width", "")}x{_d.get("height", "")}'
                    if len(_main_resolution) < 5:
                        _main_resolution = f'{_d.get("coded_width", "")}x{_d.get("coded_height", "")}'
                    w, h =  _main_resolution.split('x', 2)
                    if int(w) > 1920 and int(h) > 1080:
                        debug(f'\tresolution {_main_resolution} -> 1920x1080')
                        if not cmd_vf:
                            cmd_vf = '-vf "scale=1920:-1:force_original_aspect_ratio=decrease,pad=1920:1080:(ow-iw)/2:(oh-ih)/2'
                        else:
                            cmd_vf += ',scale=1920:-1:force_original_aspect_ratio=decrease,pad=1920:1080:(ow-iw)/2:(oh-ih)/2'
                    if (not _main_bitrate) or (not _duration) or (not _main_fps):
                        warn(f'empty bitrate/duration/fps ? {_main_bitrate=} {_duration=} {_main_fps=}')
                        err = 'empty main_bitrate/duration/fps'
                        return is_valid, err, (_main_encoder, _main_bitrate, _main_fps, _main_resolution, _main_duration, _cmd_main_stream, _cmd_copy_stream, _desc)
                    _main_duration = _duration
                    _desc += f'{_duration}, {_codec_type}:{_codec_name} {_d.get("pix_fmt", "")}({_d.get("field_order", "")}), {_d.get("display_aspect_ratio", "")} {_main_resolution}@{_main_fps}fps {bitrate_hum(_main_bitrate)}\n'
                    debug(f'\t*video#{_i}:{_i_v} {_codec_name}:{_codec_type}, {_duration}, {bitrate_hum(_main_bitrate)}, {_main_fps}fps, {_main_resolution}')
                    _i_v += 1
                else:
                    if _codec_type == 'video':  # 非主视频流
                        if _pix_fmt != 'yuv420p':  # 不要非yuv420p的，不知道怎么转
                            #_cmd_copy_stream += f'-map 0:v:{_i_v} -c:v:{_i_v} libx264 -vf:v:{_i_v} "scale=out_color_matrix=bt709,format=yuv420p" '
                            debug(f'\tdrop stream#{_i}:{_codec_name}:{_codec_type} {_pix_fmt}')
                        else:
                            _cmd_copy_stream += f'-map 0:v:{_i_v} -c:v:{_i_v} copy '
                            debug(f'\tvideo#{_i}:{_i_v} {_codec_name}:{_codec_type} {_pix_fmt}, {_duration}, {bitrate_hum(_bitrate)}')
                        _i_v += 1
                    elif _codec_type == 'audio':  # 音频流
                        if _codec_name != 'aac':  # wmav1/wmav2/wmapro 转为 aac
                            _cmd_copy_stream += f'-map 0:a:{_i_a} -c:a:{_i_a} aac -async 1 -apad 1 '
                        else:  # 只copy
                            _cmd_copy_stream += f'-map 0:a:{_i_a} -c:a:{_i_a} copy '
                        debug(f'\taudio#{_i}:{_i_a} {_codec_name}:{_codec_type}  {_duration}, {bitrate_hum(_bitrate)}')
                        _sample_rate, _channel_layout = _d.get('sample_rate', ''), _d.get('channel_layout', '')
                        _desc += f'{_codec_type}:{_codec_name} {_sample_rate} {_channel_layout} {bitrate_hum(_bitrate)}\n'
                        _i_a += 1
                    elif _codec_type == 'subtitle':  # 字幕流只copy
                        _cmd_copy_stream += f'-map 0:s:{_i_s} -c:s:{_i_s} '
                        _cmd_copy_stream += 'copy '
                        # if _codec_name in ('mov_text', 'tx3g'):
                        #     _cmd_copy_stream += 'copy '
                        # else:
                        #     _cmd_copy_stream += 'mov_text '  # mp4格式不支持带格式的和图像格式的字幕，此处未处理，遇到这种字幕，可能会出错
                        debug(f'\tsubtitle#{_i}:{_i_s} {_codec_name}:{_codec_type}  {_duration}, {_bitrate}')
                        _desc += f'{_codec_type}:{_codec_name} {bitrate_hum(_bitrate)}\n'
                        _i_s += 1
                    elif _codec_type == 'data' and _codec_name == 'bin_data':  # 未知流，抛弃
                        warn(f'\tdrop stream#{_i}:{_codec_name}/{_codec_type} {_pix_fmt} in {fname}')
                        #_cmd_copy_stream += f'-map 0:s:{_i_s} -c:s:{_i_s} copy '
                        #_i_s += 1
                    else:
                        warn(f'\tunknown stream#{_i}: {_codec_name}:{_codec_type}, {_duration}, {bitrate_hum(_bitrate)}')
            if cmd_vf:
                cmd_vf += '" '
                _cmd_main_stream = cmd_vf + _cmd_main_stream
                if cmd_vf.find('scale=1920') != -1:
                    _cmd_main_stream += ' -s 1920x1080 '
            if _main_encoder and _main_bitrate and _main_fps and _main_resolution and _main_duration and _cmd_main_stream and _cmd_copy_stream:
                is_valid = True
        except Exception as e:
            err = _rslt.stderr
            debug(f'{_rslt.stderr=}\n{e=}')
            excep(e)
            #raise

    return is_valid, err, (_main_encoder, _main_bitrate, _main_fps, _main_resolution, _main_duration, _cmd_main_stream, _cmd_copy_stream, _desc)


def main():
    parser = argparse.ArgumentParser(description='H264转码为HEVC')
    parser.add_argument('-s', '--src', type=str, help='源文件路径')
    parser.add_argument('-p', '--use_tqdm', action='store_true', help='使用tqdm进度条', default=False)
    parser.add_argument('-k', '--keep_old', action='store_true', help='保留源文件', default=False)
    parser.add_argument('-i', '--log_level', choices=['info', 'debug'], default='debug', help='设置日志级别 (默认: debug)')
    parser.add_argument('-n', '--nr_convert', type=int, default=1, help='处理多少个文件就退出，默认为1')
    args = parser.parse_args()
    print(f'src: {args.src}')
    print(f'use_tqdm: {args.use_tqdm}')
    print(f'keep_old: {args.keep_old}')
    print(f'log_level: {args.log_level}')
    print(f'nr_convert: {args.nr_convert}')
    log_level = getattr(logging, args.log_level.upper())
    #logging.basicConfig(format='%(asctime)s %(levelname).1s %(funcName)+10s:%(lineno).03d| %(message)s', datefmt='%Y%m%d_%H%M%S', level=log_level)
    logging.basicConfig(format='{asctime} {levelname:.1s} {funcName:>10.10s}:{lineno:03d}| {message}', datefmt='%Y%m%d_%H%M%S', style='{', level=log_level)
    
    MIN_SIZE = 0.5 * 1024 * 1024 * 1024
    SIZE_1G = 1 * 1024 *1024 * 1024
    SIZE_2G = 2 * 1024 * 1024 * 1024
    SIZE_3G = 3 * 1024 * 1024 * 1024
    SIZE_4G = 4 * 1024 * 1024 * 1024
    ffprobe = 'ffprobe'
    ffmpeg = 'ffmpeg'
    l_converted, checked, skipped, check_err, convert_err, converted, total_saved = [], 0, 0, 0, 0, 0, 0
    flag_exit = False
    _exit_file = os.path.join(os.path.dirname(os.path.realpath(__file__)), 'exit.txt')
    debug(f'{_exit_file=}')
    for root, dirs, files in os.walk(args.src):
        dirs.sort(key=lambda x: x.upper())
        #dirs[:] = [x for x in dirs if not x.startswith('BEST-')]
        dirs[:] = [x for x in dirs if not x.startswith('BEST-Love.Death')]
        files.sort(key=lambda x: x.upper())
        l_files = [os.path.join(root, x) for x in files if x.endswith(('.mp4', '.avi', '.ts', '.mkv', '.asf', '.wmv', '.mov', '.flv', '.3gp', '.mxf'))]
        for _f in l_files:
            _err = ''
            debug(f'check file {_f} ...')
            checked += 1
            _old_size = os.stat(_f).st_size
            if _old_size < MIN_SIZE:
                debug(f'skip small file {size_hum(_old_size)} {_f}')
                skipped += 1
                continue

            _main_encoder, _main_bitrate, _main_fps, _main_resolution, _main_duration, _cmd_main_stream, _cmd_copy_stream = '', 0, 0, '', '', '', ''
            if os.path.splitext(_f)[0].endswith('.H265'):  # 本身是带.H265字样的，检查是否是有效的H265文件
                _valid, _err, (_main_encoder, _main_bitrate, _main_fps, _main_resolution, _main_duration, _cmd_main_stream, _cmd_copy_stream, _) = check_hevc(_f)
                if _valid and _main_encoder == 'hevc':
                    debug(f'skip h265 file {_f}')
                    skipped += 1
                    continue
                else:
                    warn(f'skip BAD h265 file {_f}')
                    skipped += 1
                    continue

            _old_size = os.stat(_f).st_size
            if _old_size == 0:
                warn(f'skip 0 bytes file {_f}')
                skipped += 1
                continue

            if not _main_encoder:
                _valid, _err, (_main_encoder, _main_bitrate, _main_fps, _main_resolution, _main_duration, _cmd_main_stream, _cmd_copy_stream, _desc) = check_hevc(_f)
                if _err:
                    check_err += 1
                    warn(f'skip err file {_err} {_f}')
                    continue
            if (not _valid) or (not _main_encoder):
                warn(f'skip non-valid file {_valid=} {_main_encoder=} {_f}')
                skipped += 1
                continue
            _filebase, _ext = os.path.splitext(_f)
            if _ext != '.mp4':  # wmv/avi等容器不支持h265或支持有限, 改成mp4
                _ext = '.mp4'
            _f_converted = '.H265'.join((_filebase, _ext))
            if os.path.exists(_f_converted):  # 对应的带.H265字样的文件存在，检查对应的文件是否是有效的H265文件
                _valid, _, (_, _, _, _, _dur, _, _, _) = check_hevc(_f_converted)
                if _valid and abs(hms2sec(_dur) < hms2sec(_main_duration)) < 2:
                    info(f'skip converted file {_f}')
                    skipped += 1
                    continue

            _need = True if _main_encoder != 'hevc'  and _main_encoder != 'av1' else False
            if not _need:
                if _main_encoder != 'hevc':
                    warn(f'skip {_main_encoder} file {_f}')
                skipped += 1
                continue
            info(f'file info: {size_hum(_old_size)} {_desc.replace('\n', ', ')} {"need" if _need else "no_need"} {_err or ""} {_f}')
    #        _cmd = f'''{ffmpeg} -hide_banner -log_level error -hwaccel d3d11va -hwaccel_output_format d3d11 -i "{_f}" -c:v hevc_amf -preanalysis true -quality balanced -rc vbr_peak -maxrate {_bitrate} -c:a copy "{_f_converted}"'''
    #        _cmd = f'''{ffmpeg} -hide_banner -log_level error -hwaccel d3d11va -hwaccel_output_format d3d11 -i "{_f}" -c:v hevc_amf -preanalysis true -quality balanced -rc vbr_latency -c:a copy "{_f_converted}"'''
    #        _cmd = f'''{ffmpeg} -hide_banner -log_level error -hwaccel d3d12va -i "{_f}" -c:v hevc_amf -preanalysis true -quality balanced -rc hqvbr -high_motion_quality_boost_enable true -preencode true -pa_scene_change_detection_enable true -pa_scene_change_detection_sensitivity high -pa_static_scene_detection_enable true -pa_static_scene_detection_sensitivity high -pa_high_motion_quality_boost_mode auto -c:a copy "{_f_converted}"'''
            # 核显解码 独显编码 3.17X 670M
    #        _cmd = f'''{ffmpeg} -hide_banner -log_level error -hwaccel d3d12va -i "{_f}" -c:v hevc_amf -preanalysis true -quality balanced -rc hqvbr -high_motion_quality_boost_enable true -preencode true -pa_scene_change_detection_enable true -pa_scene_change_detection_sensitivity high -pa_static_scene_detection_enable true -pa_static_scene_detection_sensitivity high -pa_high_motion_quality_boost_mode auto -c:a copy -profile:v main -b:v {int(_bitrate/2)} "{_f_converted}"'''
            # 核显解码 独显编码 3.75X 670M
    #        _cmd = f'''{ffmpeg} -hide_banner -log_level error -hwaccel d3d12va -i "{_f}" -c:v hevc_amf -preanalysis true -quality balanced -rc vbr_peak -high_motion_quality_boost_enable true -preencode true -pa_scene_change_detection_enable true -pa_scene_change_detection_sensitivity high -pa_static_scene_detection_enable true -pa_static_scene_detection_sensitivity high -pa_high_motion_quality_boost_mode auto -c:a copy -profile:v main -b:v {int(_bitrate/2)} -maxrate {_bitrate} "{_f_converted}"'''
            # 独显编解码 2.85X 670M
    #        _cmd = f'''{ffmpeg} -hide_banner -log_level error -hwaccel d3d11va -hwaccel_device 1 -i "{_f}" -c:v hevc_amf -preanalysis true -quality balanced -rc vbr_peak -high_motion_quality_boost_enable true -preencode true -pa_scene_change_detection_enable true -pa_scene_change_detection_sensitivity high -pa_static_scene_detection_enable true -pa_static_scene_detection_sensitivity high -pa_high_motion_quality_boost_mode auto -c:a copy -profile:v main -b:v {int(_bitrate/2)} -maxrate {_bitrate} "{_f_converted}"'''
            # 独显编解码 2.48X 670M
    #        _cmd = f'''{ffmpeg} -hide_banner -log_level error -hwaccel d3d11va -hwaccel_device 1 -i "{_f}" -c:v hevc_amf -preanalysis true -quality quality -rc vbr_peak -preencode true -c:a copy -profile:v main -b:v {int(_bitrate/2)} -maxrate {_bitrate} "{_f_converted}"'''
            # 核显编解码 1.15X 慢
    #        _cmd = f'''{ffmpeg} -hide_banner -hwaccel qsv -hwaccel_device 0 -hwaccel_output_format qsv -c:v h264_qsv -i "{_f}" -c:v hevc_qsv -preset slower -global_quality 24 -b_strategy 1 -qsv_params "NumSlice=4" -profile:v main -c:a copy "{_f_converted}"'''

            # 核显解码 独显编码
    #        _cmd = f'''{ffmpeg} -hide_banner -log_level error -hwaccel d3d12va -i "{_f}" -map 0 -c:v hevc_amf -preanalysis true -quality balanced -rc vbr_peak -high_motion_quality_boost_enable true -preencode true -pa_scene_change_detection_enable true -pa_scene_change_detection_sensitivity high -pa_static_scene_detection_enable true -pa_static_scene_detection_sensitivity high -pa_high_motion_quality_boost_mode auto -c:a copy -c:s copy -profile:v main -b:v {int(_main_bitrate * 0.7)} -maxrate {_main_bitrate} "{_f_converted}"'''

            if _old_size > SIZE_4G:
                factor = 0.6
            elif _old_size >= SIZE_3G:
                factor = 0.65
            elif _old_size >= SIZE_2G:
                factor = 0.7
            elif _old_size >= SIZE_1G:
                factor = 0.75
            else:
                factor = 0.8

#                # 核显解码 独显编码 最初使用
            #_cmd = f'''{ffmpeg} -hide_banner -log_level error -hwaccel d3d12va -i "{_f}" {_cmd_main_stream} -preanalysis true -quality balanced -rc vbr_peak -fps_mode passthrough -fflags +genpts -skip_frame 1 -high_motion_quality_boost_enable true -preencode true -pa_scene_change_detection_enable true -pa_scene_change_detection_sensitivity high -pa_static_scene_detection_enable true -pa_static_scene_detection_sensitivity high -pa_high_motion_quality_boost_mode auto -pa_lookahead_buffer_depth 40 -vbaq true -pa_taq_mode 2 -profile:v main -b:v {int(_main_bitrate * factor)} -maxrate {_main_bitrate} -bufsize {_main_bitrate * 2} {_cmd_copy_stream}  "{_f_converted}"'''
            _cmd = f'''{ffmpeg} -hide_banner -log_level error -hwaccel d3d12va -i "{_f}" {_cmd_main_stream} -preanalysis true -quality balanced -rc vbr_peak -fps_mode passthrough -pix_fmt yuv420p -fflags +genpts -skip_frame 1 -high_motion_quality_boost_enable true -preencode true -pa_scene_change_detection_enable true -pa_scene_change_detection_sensitivity high -pa_static_scene_detection_enable true -pa_static_scene_detection_sensitivity high -pa_initial_qp_after_scene_change 18 -pa_max_qp_before_force_skip 35 -pa_caq_strength high -pa_frame_sad_enable true -pa_ltr_enable true -pa_paq_mode caq -pa_high_motion_quality_boost_mode auto -pa_lookahead_buffer_depth 40 -vbaq true -pa_taq_mode 2 -profile:v main -b:v {int(_main_bitrate * factor)} -maxrate {_main_bitrate} -bufsize {_main_bitrate * 2} {_cmd_copy_stream}  "{_f_converted}"'''

            # 独显编解码的具体例子，注意：如果用这个，则滤镜插件的命令cmd_vf也需要改动, 开头加上hwdownload,format=nv12，结尾加上hwupload
            # -vf "hwdownload,format=nv12,scale=1920:-1:force_original_aspect_ratio=decrease,pad=1920:1080:(ow-iw)/2:(oh-ih)/2,fps=30,hwupload"  因为滤镜需要cpu计算，所以数据要从显存copy到内存，指定为nv12格式，做scale和重设fps，然后再将结果copy回显存
            #_cmd = f'''{ffmpeg} -hide_banner -log_level error -hwaccel d3d11va -hwaccel_device 1 -hwaccel_output_format d3d11  -i "{_f}" -vf "hwdownload,format=nv12,scale=1920:-1:force_original_aspect_ratio=decrease,pad=1920:1080:(ow-iw)/2:(oh-ih)/2,fps=30,hwupload" -map 0:v:0 -c:v:0 hevc_amf -preanalysis true -quality balanced -rc vbr_peak -fps_mode passthrough -fflags +genpts -skip_frame 1 -high_motion_quality_boost_enable true -preencode true -pa_scene_change_detection_enable true -pa_scene_change_detection_sensitivity high -pa_static_scene_detection_enable true -pa_static_scene_detection_sensitivity high -pa_initial_qp_after_scene_change 18 -pa_max_qp_before_force_skip 35 -pa_caq_strength high -pa_frame_sad_enable true -pa_ltr_enable true -pa_paq_mode caq -pa_high_motion_quality_boost_mode auto -pa_lookahead_buffer_depth 40 -vbaq true -pa_taq_mode 2 -profile:v main -b:v {int(_main_bitrate * factor)} -maxrate {_main_bitrate} -bufsize {_main_bitrate * 2} {_cmd_copy_stream}  "{_f_converted}"'''

            # 核显解码 独显编码 
#                _cmd = f'''{ffmpeg} -hide_banner -log_level error -hwaccel d3d12va -i "{_f}" {_cmd_main_stream} -usage transcoding -rc qvbr -qvbr_quality_level 28 -qp_i 24 -qp_p 26 -min_qp_i 24 -max_qp_i 24 -min_qp_p 26 -max_qp_p 26 -preset quality -g {int(10 * round(_main_fps))} -profile:v main -header_insertion_mode gop -preanalysis true -bf 4 -refs 5 -bufsize {_main_bitrate * 2} -maxrate {_main_bitrate} {_cmd_copy_stream}  "{_f_converted}"'''
#                _cmd = f'''{ffmpeg} -hide_banner -log_level error -hwaccel d3d12va -i "{_f}" {_cmd_main_stream} -usage transcoding -global_quality 28 -rc cqp -qp_i 24 -qp_p 26 -min_qp_i 24 -max_qp_i 24 -min_qp_p 26 -max_qp_p 26 -quality quality -pix_fmt yuv420p {_cmd_copy_stream}  "{_f_converted}"'''

            debug(f'convert ({factor=}) to {_f_converted} ...')
            info(f'{converted + convert_err + 1}{f"/{args.nr_convert}" if args.nr_convert > 0 else ""} {factor=} cmd={_cmd}')
            if args.nr_convert == 0:
                warn(f'skip convert file cause nr_convert=0')
                break
            call_ffmpeg(_cmd, args.use_tqdm)
            winsound.PlaySound('d:/ding-101492.wav', winsound.SND_FILENAME | winsound.SND_ASYNC)
            _new_size = os.stat(_f_converted).st_size if os.path.exists(_f_converted) else 0
            _single_saved = (_old_size - _new_size) if _new_size else 0
            _valid, _, (_, _, _, _, _dur, _, _, _) = check_hevc(_f_converted)  # 通过获取信息确定生成的文件是否有效
            if _valid and _single_saved > 0 and abs(hms2sec(_dur) - hms2sec(_main_duration)) < 2:
                converted += 1
                info(f'\t{size_hum(_old_size)} -> {size_hum(_new_size)} saved {size_hum(_single_saved)} {round(_single_saved / _old_size * 100, 2)}% factor_target={round((1 - factor) * 100, 2)}%')
                l_converted.append(_f_converted)
                if (not args.keep_old) and int(_main_fps) <= 30 and _main_resolution.find('3840') == -1:
                    try:
                        #warn(f'TODO!!!!! to remove old file {_f}')
                        os.remove(_f)
                        info(f'\told file removed {_f}')
                    except FileNotFoundError:
                        debug(f'old file not found. {_f}')
                    try:
                        #warn(f'TODO!!!!! to remove img file {_f + ".jpg"}')
                        os.remove(_f + '.jpg')
                        info(f'\timg file removed {_f + ".jpg"}')
                    except FileNotFoundError:
                        debug(f'img file not found. {_f + ".jpg"}')
                flag_exit = os.path.exists(_exit_file)  # 转码操作完毕后检查下退出文件是否存在
            else:
                warn(f'convert error {_f} {_valid=} {_single_saved=} dur_diff={hms2sec(_dur) - hms2sec(_main_duration)}')
                if _single_saved < 0:  # 转码后尺寸变大了
                    warn(f'converted file is larger!!! {size_hum(_old_size)} -> {size_hum(_new_size)} +{size_hum(abs(_single_saved))}')
                convert_err += 1
            total_saved += _single_saved
            if args.nr_convert >0 and (converted + convert_err >= args.nr_convert):
                break
            if flag_exit:
                info(f'got exit flag {_exit_file}, stop process.')
                try:
                    os.remove(_exit_file)
                except:
                    pass
                break
        if args.nr_convert >0 and (converted + convert_err >= args.nr_convert):
            break
        if flag_exit:
            break
        
    info(f'done. {checked=:,} {skipped=:,} {check_err=:,} {convert_err=:,} {converted=:,} total_saved={size_hum(total_saved)}')
    info(f'processed: \n{'\n'.join(f'{_i:>3d}) {_f}' for _i, _f in enumerate(l_converted, start=1))}')
    winsound.PlaySound('d:/ding-101492.wav', winsound.SND_FILENAME)

if __name__ == "__main__":
    sys.exit(main())
