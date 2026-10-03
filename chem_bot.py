"""Local command-line tools for Grok Bot. No model API keys are read."""
import argparse
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from urllib.parse import urlparse
import webbrowser

ROOT = Path(__file__).resolve().parent


class ToolError(Exception):
    pass


class LocalOnlyRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise ToolError('题库地址发生跳转，请确认连接的是本机题库程序')


def server_url(value):
    parsed = urlparse(value)
    if (parsed.scheme != 'http' or parsed.hostname not in ('127.0.0.1', 'localhost')
            or parsed.username or parsed.password or parsed.path not in ('', '/')
            or parsed.query or parsed.fragment):
        raise argparse.ArgumentTypeError('请使用用户电脑上的 http://127.0.0.1:端口 地址')
    try:
        port = parsed.port or 80
    except ValueError as exc:
        raise argparse.ArgumentTypeError('题库端口不正确') from exc
    if not 1 <= port <= 65535:
        raise argparse.ArgumentTypeError('题库端口需要在 1 至 65535 之间')
    return 'http://127.0.0.1:%d' % port


def request(server, path, payload=None):
    body = json.dumps(payload, ensure_ascii=False).encode('utf-8') if payload is not None else None
    req = urllib.request.Request(server + path, data=body, headers={'Content-Type': 'application/json'})
    # Ignore proxy environment variables: these requests must remain on this computer.
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), LocalOnlyRedirect())
    try:
        with opener.open(req, timeout=30) as response:
            return json.loads(response.read().decode('utf-8'))
    except urllib.error.HTTPError as exc:
        try:
            message = json.loads(exc.read().decode('utf-8')).get('error')
        except (ValueError, UnicodeError):
            message = None
        raise ToolError(message or '题库程序版本不支持这个操作，请更新并重启题库程序') from None
    except (urllib.error.URLError, TimeoutError, OSError):
        raise ToolError('无法连接本机题库，请确认用户电脑已连接、题库已启动，并检查 --server 的端口') from None
    except (ValueError, UnicodeError):
        raise ToolError('这个地址没有返回题库结果，请检查地址和程序版本') from None


def start(server):
    parsed = urlparse(server)
    with socket.socket() as sock:
        sock.settimeout(0.5)
        occupied = sock.connect_ex(('127.0.0.1', parsed.port)) == 0
    if occupied:
        return request(server, '/api/bot/info')
    data_dir = Path(os.environ.get('CHEM_DATA_DIR') or ROOT).resolve()
    if not (data_dir / 'bank.sqlite').is_file():
        raise ToolError('这个目录还没有题库，请先准备已有题库或导入 Word 文件：' + str(data_dir))
    env = os.environ.copy()
    env['CHEM_PORT'] = str(parsed.port)
    log_path = data_dir / 'bot-bridge-start.log'
    with log_path.open('ab') as log:
        child = subprocess.Popen([sys.executable, str(ROOT / 'app.py'), 'serve'], cwd=str(ROOT), env=env,
                                 stdin=subprocess.DEVNULL, stdout=log, stderr=log,
                                 creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0,
                                 start_new_session=os.name != 'nt')
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        if child.poll() is not None:
            raise ToolError('题库程序没有启动成功，请查看本机 bot-bridge-start.log')
        with socket.socket() as sock:
            sock.settimeout(0.2)
            if sock.connect_ex(('127.0.0.1', parsed.port)) == 0:
                return request(server, '/api/bot/info')
        time.sleep(0.2)
    raise ToolError('题库启动时间较长，请稍后执行 doctor 查看状态')


def id_list(value):
    try:
        return [int(part.strip()) for part in value.split(',') if part.strip()]
    except ValueError as exc:
        raise argparse.ArgumentTypeError('题号请用英文逗号分隔，例如 1,2,3') from exc


def main(argv=None):
    parser = argparse.ArgumentParser(description='Grok Bot 本地化学题库工具')
    parser.add_argument('--server', type=server_url, default=os.environ.get('CHEM_BOT_URL') or 'http://127.0.0.1:' + (os.environ.get('CHEM_PORT') or '8765'))
    sub = parser.add_subparsers(dest='command', required=True)
    sub.add_parser('doctor', help='核对题库目录、题量、版本')
    launch = sub.add_parser('start', help='启动已有本机题库；已启动时不重复启动')
    launch.add_argument('--open', action='store_true', help='打开用户电脑上的浏览器')
    sub.add_parser('catalog', help='查看题库支持的知识点和主题')
    sub.add_parser('papers', help='查看已有试卷')
    paper = sub.add_parser('paper', help='查看一套试卷及题号')
    paper.add_argument('--id', type=int, required=True)
    query = sub.add_parser('search', help='检索已有题目')
    query.add_argument('--query', default='')
    query.add_argument('--keyword', action='append', default=[])
    query.add_argument('--match', choices=['any', 'all'], default='any')
    query.add_argument('--types', default='', help='英文逗号分隔中文题型')
    query.add_argument('--major', default='')
    query.add_argument('--minor', default='')
    query.add_argument('--knowledge', default='')
    query.add_argument('--theme', default='')
    query.add_argument('--images', choices=['any', 'yes', 'no'], default='any')
    query.add_argument('--exclude-ids', type=id_list, default=[])
    query.add_argument('--limit', type=int, default=20)
    query.add_argument('--offset', type=int, default=0)
    show = sub.add_parser('show', help='读取选定题目的题干、图片引用和识别提示')
    show.add_argument('--ids', type=id_list, required=True)
    create = sub.add_parser('create', help='按 UTF-8 JSON 计划新建试卷')
    create.add_argument('--plan', type=Path, required=True)
    create.add_argument('--dry-run', action='store_true', help='核对计划，不创建试卷')
    create.add_argument('--open', action='store_true', help='成功后打开本机试卷页面')
    args = parser.parse_args(argv)
    server = args.server
    if args.command == 'start':
        result = start(server)
    elif args.command == 'doctor':
        result = request(server, '/api/bot/info')
    elif args.command == 'catalog':
        result = request(server, '/api/curriculum')
    elif args.command == 'papers':
        result = request(server, '/api/papers')
    elif args.command == 'paper':
        if args.id < 1:
            raise ToolError('试卷编号必须大于零')
        result = request(server, '/api/papers/%d' % args.id)
    elif args.command == 'search':
        result = request(server, '/api/bot/search', {'query': args.query, 'keywords': args.keyword,
                         'match': args.match, 'types': [t.strip() for t in args.types.split(',') if t.strip()],
                         'major': args.major, 'minor': args.minor, 'knowledge': args.knowledge, 'theme': args.theme,
                         'has_images': {'any': None, 'yes': True, 'no': False}[args.images],
                         'exclude_ids': args.exclude_ids, 'limit': args.limit, 'offset': args.offset})
    elif args.command == 'show':
        result = request(server, '/api/bot/questions', {'question_ids': args.ids})
    else:
        try:
            plan = json.loads(args.plan.read_text(encoding='utf-8-sig'))
        except (OSError, ValueError, UnicodeError):
            raise ToolError('无法读取计划，请保存为 UTF-8 JSON 文件') from None
        if not isinstance(plan, dict):
            raise ToolError('组卷计划必须是一份 JSON 对象')
        if args.dry_run:
            plan['dry_run'] = True
        result = request(server, '/api/bot/papers', plan)
        if result.get('preview_path'):
            result['preview_url'] = server + result['preview_path']
    if getattr(args, 'open', False) and not result.get('dry_run'):
        webbrowser.open(result.get('preview_url', server))
    return result


if __name__ == '__main__':
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8')
    try:
        print(json.dumps(main(), ensure_ascii=False, indent=2))
    except (ToolError, OSError) as exc:
        print(json.dumps({'error': str(exc)}, ensure_ascii=False), file=sys.stdout)
        raise SystemExit(1)
