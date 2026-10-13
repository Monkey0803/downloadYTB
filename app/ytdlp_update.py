"""yt-dlp 本机更新支持。

**为什么需要**：打包好的 yt-dlp 停在构建时的版本（`app/core.py:280` 的注释就写着
「常因 yt-dlp 版本落后于 YouTube 反爬更新。提示升级或重试」），而冻结在 App 里的副本
没法升级——用户只能重新下载整个 App。这里把官方 wheel 装到用户数据目录，启动时优先
注入 ``sys.path``：不改动 App 自带副本，失败也不影响启动。

**三条约束**（都来自真实踩坑）：

1. ``activate()`` 只读磁盘上的版本号，**绝不导入** :mod:`yt_dlp`。一旦导入，模块就进了
   ``sys.modules``，之后再注入 ``sys.path`` 完全没有作用，用户装的新版会被静默忽略。
   所以它在 ``main.py`` 里必须早于任何 ``import yt_dlp`` 的模块执行。
2. 安装是**原子**的：先解压到同目录下的临时目录，校验通过再 ``os.replace``；任何一步
   失败都清理干净，最终结论保持在「用内置副本」。
3. 版本比较只认能从磁盘读到的数字版本；读不出来时（冻结包常见）**用户明确装过的副本优先**，
   因为那是他们的显式意图。
"""

import importlib.util
import json
import os
import re
import shutil
import sys
import tempfile
import zipfile

from . import i18n

PYPI_JSON = "https://pypi.org/pypi/yt-dlp/json"
MANAGED_DIRNAME = "yt_dlp_pkg"
PACKAGE_NAME = "yt_dlp"
# 构建脚本写进包的版本文件（冻结包读不到 yt_dlp/version.py，见 bundle_root）
BUNDLED_VERSION_FILENAME = "yt_dlp_bundled_version.txt"
# 第二次启动用：告诉 activate() 这次绝对别用本机副本（坏副本兜底重启用）
DISABLE_ENV = "VD_NO_MANAGED_YTDLP"
BROKEN_PREFIX = ".broken-"

_VERSION_RE = re.compile(r'__version__\s*=\s*["\']([^"\']+)["\']')


class YtdlpUpdateError(Exception):
    """检查或安装失败；消息可直接显示给用户。"""


# ---------- 数据目录 ----------


def data_dir() -> str:
    """与 settings.json 同一个目录（复用 settings 的平台约定）。"""
    from . import settings

    return settings._settings_dir()


def managed_root() -> str:
    """本机安装的 yt-dlp 存放目录。"""
    return os.path.join(data_dir(), MANAGED_DIRNAME)


# ---------- 版本 ----------


def read_version_file(package_dir: str) -> str:
    """从 ``yt_dlp/version.py`` 读出 ``__version__``（只读文件，不导入模块）。"""
    if not package_dir:
        return ""
    path = os.path.join(package_dir, "version.py")
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            text = fh.read()
    except OSError:
        return ""
    match = _VERSION_RE.search(text)
    return match.group(1) if match else ""


def parse_version(text) -> tuple:
    """``2026.08.19`` / ``2026.08.19.1`` → 可比较元组；解析不了返回空元组。"""
    parts = []
    for chunk in str(text or "").strip().split("."):
        if not chunk.isdigit():
            return ()
        parts.append(int(chunk))
    return tuple(parts)


def is_newer(candidate, current) -> bool:
    """candidate 是否比 current 新。双方都解析不了时返回 False。"""
    new, old = parse_version(candidate), parse_version(current)
    if not new:
        return False
    if not old:
        return True
    return new > old


def _is_managed_path(path: str) -> bool:
    root = managed_root()
    if not root or not path:
        return False
    return os.path.abspath(path).startswith(os.path.abspath(root) + os.sep)


def bundled_dir() -> str:
    """**内置** yt_dlp 的包目录；找不到返回空串（不导入模块）。

    必须跳过 ``managed_root()`` 下的路径：``activate()`` 注入本机副本之后，
    ``find_spec`` 会优先解析到注入的那份，于是「内置版本」变成「已安装版本」，
    activate() / active_source() / 诊断显示立刻自相矛盾（实测踩到）。
    """
    try:
        spec = importlib.util.find_spec(PACKAGE_NAME)
    except Exception:
        spec = None
    candidates = []
    if spec is not None:
        locations = getattr(spec, "submodule_search_locations", None)
        if locations:
            candidates = list(locations)
        elif getattr(spec, "origin", "") or "":
            candidates = [os.path.dirname(spec.origin)]
    for path in candidates:
        if not _is_managed_path(path):
            return path
    # find_spec 只给出被注入的那份：退回 sys.path 里第一个非托管目录
    for entry in sys.path:
        if not entry or _is_managed_path(entry):
            continue
        candidate = os.path.join(entry, PACKAGE_NAME)
        if os.path.isfile(os.path.join(candidate, "__init__.py")):
            return candidate
    return ""


def bundle_root() -> str:
    """冻结包的资源根目录；开发环境返回仓库根目录。

    PyInstaller 把 ``yt_dlp`` 编进归档，运行期**拿不到**它的 ``version.py``：实测
    ``find_spec`` 给出的路径在磁盘上并不存在，``loader.get_data`` 也只是普通文件读取
    （同样失败）。所以构建脚本会另外写一份版本文件放进包里，这里读它。
    """
    meipass = getattr(sys, "_MEIPASS", "")
    if meipass:
        return meipass
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _read_plain_version(path: str) -> str:
    """版本文件里只有版本号本身（不是 ``__version__ = ...``）。"""
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            text = fh.read().strip()
    except OSError:
        return ""
    return text if parse_version(text) else ""


def bundled_version() -> str:
    """内置 yt-dlp 的版本号；都读不到时返回空串（调用方会优先用户副本）。"""
    version = read_version_file(bundled_dir())
    if version:
        return version
    for candidate in (os.path.join(bundle_root(), BUNDLED_VERSION_FILENAME),
                      os.path.join(bundle_root(), "build", "work",
                                   BUNDLED_VERSION_FILENAME)):
        version = _read_plain_version(candidate)
        if version:
            return version
    try:
        from importlib.metadata import version as dist_version

        return dist_version("yt-dlp")
    except Exception:
        return ""


def should_use_managed(managed_version, bundled: str) -> bool:
    """是否该用本机安装的副本。"""
    if not parse_version(managed_version):
        return False
    return is_newer(managed_version, bundled)


# ---------- 已安装副本 ----------


def installed_versions() -> list:
    """已安装的版本列表（按版本倒序）。只认结构完整的目录。"""
    root = managed_root()
    found = []
    try:
        names = os.listdir(root)
    except OSError:
        return []
    for name in names:
        if name.startswith("."):
            continue
        package = os.path.join(root, name, PACKAGE_NAME)
        if parse_version(name) and os.path.isfile(
                os.path.join(package, "__init__.py")):
            found.append(name)
    found.sort(key=parse_version, reverse=True)
    return found


def best_managed():
    """最新的一份本机副本，返回 ``(版本, 可注入的路径)``；没有则 None。"""
    versions = installed_versions()
    if not versions:
        return None
    version = versions[0]
    return version, os.path.join(managed_root(), version)


def active_version() -> tuple:
    """当前实际生效的 ``(版本, 来源)``；版本读不到时是空串。

    「生效」= activate() 会做的同一个判断，界面与诊断都以此为准，避免两处算法不一致。
    """
    manager = best_managed()
    if manager is not None and should_use_managed(manager[0], bundled_version()):
        return manager[0], "managed"
    return bundled_version(), "bundled"


def active_source() -> str:
    """当前实际生效的来源：``managed`` 或 ``bundled``（供界面与诊断显示）。"""
    return active_version()[1]


def activate() -> str:
    """在导入 yt_dlp 之前调用：有更新的本机副本就优先注入 ``sys.path``。

    返回生效来源（``managed`` / ``bundled``），便于测试与诊断。
    """
    if os.environ.get(DISABLE_ENV):
        # 上一次启动因为本机副本坏了而重启，这次直接用内置副本
        return "bundled"
    manager = best_managed()
    if manager is None:
        return "bundled"
    version, root = manager
    if not should_use_managed(version, bundled_version()):
        return "bundled"
    if root not in sys.path:
        sys.path.insert(0, root)
    return "managed"


def discard(version: str, reason: str = "") -> bool:
    """把某个本机副本标记为坏（改名放一边），返回是否真的处理了。

    必须留着而不是直接删：出问题时那份东西是唯一的线索，而且改名后
    ``installed_versions()`` 会自然忽略它（只认版本号形式的目录名）。
    """
    if not parse_version(version):
        return False
    source = os.path.join(managed_root(), version)
    if not os.path.isdir(source):
        return False
    target = os.path.join(managed_root(), BROKEN_PREFIX + version)
    try:
        if os.path.isdir(target):
            shutil.rmtree(target, ignore_errors=True)
        os.replace(source, target)
        if reason:
            with open(os.path.join(target, "broken.txt"), "w",
                      encoding="utf-8") as fh:
                fh.write(reason[:500] + "\n")
    except OSError:
        return False
    return True


def disable_current(reason: str = "") -> bool:
    """停用当前生效的本机副本；返回是否停用了（False 表示本来就没在用）。"""
    if active_source() != "managed":
        return False
    version, _root = best_managed()
    return discard(version, reason)


# ---------- 安装 ----------


def _extract_package(zf: zipfile.ZipFile, target: str) -> None:
    """把 wheel 里的 ``yt_dlp/`` 解到 target，并挡住路径穿越。"""
    prefix = PACKAGE_NAME + "/"
    members = [name for name in zf.namelist() if name.startswith(prefix)]
    if prefix + "__init__.py" not in zf.namelist():
        raise YtdlpUpdateError(i18n.tr("ytdlp_bad_wheel"))
    for name in members:
        if name.endswith("/"):
            continue
        relative = name[len(prefix):]
        if not relative or relative.startswith("/") or ".." in relative.split("/"):
            raise YtdlpUpdateError(i18n.tr("ytdlp_bad_wheel"))
        destination = os.path.join(target, *relative.split("/"))
        os.makedirs(os.path.dirname(destination), exist_ok=True)
        with zf.open(name) as source, open(destination, "wb") as out:
            shutil.copyfileobj(source, out)


def install_wheel(wheel_path: str, version: str) -> str:
    """把已下载的 wheel 装到数据目录，返回安装后的目录。

    原子：先解压到同目录临时目录，校验（结构 + 内部版本号）通过后改名。失败时不留下
    半成品，调用方只需保证不影响启动。
    """
    if not parse_version(version):
        raise YtdlpUpdateError(i18n.tr("ytdlp_bad_version"))
    root = managed_root()
    final = os.path.join(root, version)
    if os.path.isdir(os.path.join(final, PACKAGE_NAME)):
        return final
    os.makedirs(root, exist_ok=True)
    staging = tempfile.mkdtemp(prefix=".staging-", dir=root)
    try:
        try:
            with zipfile.ZipFile(wheel_path) as zf:
                # _extract_package 剥掉 "yt_dlp/" 前缀，所以要给它包目录本身
                _extract_package(zf, os.path.join(staging, PACKAGE_NAME))
        except zipfile.BadZipFile as exc:
            raise YtdlpUpdateError(i18n.tr("ytdlp_bad_wheel")) from exc
        inner = read_version_file(os.path.join(staging, PACKAGE_NAME))
        if inner and parse_version(inner) != parse_version(version):
            raise YtdlpUpdateError(
                i18n.tr("ytdlp_version_mismatch", inner=inner, wheel=version))
        os.replace(staging, final)
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    return final


# ---------- 网络 ----------


def _session(session=None):
    if session is not None:
        return session
    import requests

    return requests.Session()


def check_latest(session=None):
    """查询 PyPI 上的最新版本，返回 ``(版本, wheel 直链)``。"""
    http = _session(session)
    try:
        response = http.get(PYPI_JSON, timeout=(5, 20))
        response.raise_for_status()
        payload = json.loads(response.content.decode("utf-8", "replace"))
    except YtdlpUpdateError:
        raise
    except Exception as exc:
        raise YtdlpUpdateError(i18n.tr("ytdlp_network_failed", message=exc)) from exc
    info = payload.get("info") if isinstance(payload, dict) else None
    version = str((info or {}).get("version") or "")
    url = ""
    for entry in (payload.get("urls") or []) if isinstance(payload, dict) else []:
        name = str(entry.get("filename") or "")
        if name.endswith(".whl"):
            url = str(entry.get("url") or "")
            break
    if not parse_version(version) or not url:
        raise YtdlpUpdateError(i18n.tr("ytdlp_no_wheel"))
    return version, url


def download_and_install(version: str, url: str, session=None, progress=None) -> str:
    """下载 wheel 并安装，返回安装目录。progress(已下载字节, 总字节) 可选。"""
    http = _session(session)
    handle, temp_path = tempfile.mkstemp(prefix="yt-dlp-", suffix=".whl")
    os.close(handle)
    try:
        try:
            with http.get(url, stream=True, timeout=(5, 60)) as response:
                response.raise_for_status()
                total = int(response.headers.get("Content-Length") or 0)
                done = 0
                with open(temp_path, "wb") as out:
                    for chunk in response.iter_content(chunk_size=65536):
                        if not chunk:
                            continue
                        out.write(chunk)
                        done += len(chunk)
                        if progress:
                            progress(done, total)
        except YtdlpUpdateError:
            raise
        except Exception as exc:
            raise YtdlpUpdateError(
                i18n.tr("ytdlp_download_failed", message=exc)) from exc
        return install_wheel(temp_path, version)
    finally:
        try:
            os.remove(temp_path)
        except OSError:
            pass
