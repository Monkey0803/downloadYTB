"""本地化资源完整性测试。

两类检查：
1. 五种语言均必须提供每个已有文案；
2. 源码里 ``i18n.tr("字面量")`` 调用的键必须真的存在于文案表——第 2 类是
   必要的：只查第 1 类时，一个「压根没加进文案表」的键会静默通过，界面
   直接把 key 当文案显示出来（真实踩过一次：字幕卡片标题显示成 subtitles）。
"""

import ast
import pathlib
import re

from app import i18n

EXPECTED_LANGUAGES = {"zh_CN", "zh_TW", "en", "ko", "ja"}
_CJK = re.compile(r"[\u4e00-\u9fff]")

# 这些字面量是「内部标识」或「关键字匹配」，不是给用户看的文案（见 UX-AUDIT.md）：
# - core.py：B 站/风控错误关键字匹配、异常消息（界面用的是自己的 i18n 文案）
# - gui.py：平台身份标识（download_dir_key 再把它映射成 ASCII 键）
# - taskbar_*.py：被静默吞掉的内部异常消息
ALLOWED_HARDCODED = {
    "app/core.py": {
        "下载已暂停", "用户取消下载", "用户暂停下载",
        "大会员", "充电专属", "付费", "风控", "验证", "需要登录",
        "地区", "区域", "已失效", "稿件不可见", "不存在", "已删除",
    },
    "app/dialogs.py": {
        # 字幕语言的「母语名」：按惯例在所有语言下都显示母语写法，属于数据
        # 而非可翻译文案（英文用户看到的也是「日本語」）。随 SUBTITLE_LANGUAGES
        # 从 gui.py 搬到这里，白名单也跟着搬，避免在别处重新放行这些字面量。
        "中文（简体）", "中文（繁體）", "日本語",
    },
    "app/taskbar_linux.py": {
        "找不到 gdbus", "JobViewServer 不在总线上", "requestView 未返回对象路径: ",
    },
    "app/taskbar_win.py": {
        "CoCreateInstance 未返回 ITaskbarList3", "拿不到窗口句柄",
    },
}


def _string_literals(path):
    """产出 (行号, 文本, 是否 f-string 片段)，跳过 docstring。

    必须走 AST 而不是 tokenize：Python 3.12+ 把 f-string 拆成
    FSTRING_START/MIDDLE/END，tokenize 扫不到里面的中文——真实漏过一次
    （清晰度下拉的「约…」和剩余时间的「分/秒」）。
    """
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source)
    docstrings = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef,
                             ast.AsyncFunctionDef)):
            body = getattr(node, "body", [])
            if (body and isinstance(body[0], ast.Expr)
                    and isinstance(body[0].value, ast.Constant)
                    and isinstance(body[0].value.value, str)):
                docstrings.add(id(body[0].value))
    # f-string 里的常量也会被 ast.walk 单独访问到，先记下来避免重复上报
    inside_fstring = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.JoinedStr):
            for part in node.values:
                if isinstance(part, ast.Constant):
                    inside_fstring.add(id(part))

    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            if id(node) not in docstrings and id(node) not in inside_fstring:
                yield node.lineno, node.value, False
        elif isinstance(node, ast.JoinedStr):
            for part in node.values:
                if isinstance(part, ast.Constant) and isinstance(part.value, str):
                    yield part.lineno, part.value, True


def check_no_hardcoded_cjk():
    """用户可见文案必须走 i18n；硬编码中文一律报错（内部标识见白名单）。"""
    offenders = []
    scanned = 0
    for path in sorted((ROOT / "app").glob("*.py")):
        if path.name == "i18n.py":
            continue
        allowed = ALLOWED_HARDCODED.get(f"app/{path.name}", set())
        for line, text, _is_fstring in _string_literals(path):
            scanned += 1
            if not _CJK.search(text) or text in allowed:
                continue
            offenders.append(f"app/{path.name}:{line}: {text[:50]!r}")
    assert scanned > 50, "扫描到的字面量太少，检查规则可能失效"
    assert not offenders, (
        "以下位置硬编码了用户可见文案，请改用 i18n.tr（内部标识请加入白名单）：\n"
        + "\n".join(offenders))
    return scanned


ROOT = pathlib.Path(__file__).resolve().parent
# 只统计字面量调用；形如 i18n.tr(label_key) 的动态键无法静态检查
_LITERAL_CALL = re.compile(r'i18n\.tr\(\s*"([A-Za-z0-9_]+)"')


def check_no_duplicate_keys():
    """字典里重复的键会静默覆盖，必须靠 AST 检查（真实踩过一次）。

    Python 允许 ``{"a": 1, "a": 2}``，所以「文件里有几行同名键」只能从
    语法树里数，不能靠字符串匹配（会漏掉或误判字符串内容）。
    """
    import ast

    source = (ROOT / "app" / "i18n.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    table = None
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and any(
                isinstance(t, ast.Name) and t.id == "T" for t in node.targets):
            table = node.value
            break
    assert isinstance(table, ast.Dict), "没有找到文案表 T"
    seen = {}
    duplicates = []
    for key_node in table.keys:
        key = getattr(key_node, "value", None)
        if key in seen:
            duplicates.append(f"{key}（第 {seen[key]} 行与第 {key_node.lineno} 行）")
        seen[key] = key_node.lineno
    assert not duplicates, f"文案表存在重复键：{duplicates}"


def check_translations_complete():
    actual = {code for code, _ in i18n.LANGUAGES}
    assert actual == EXPECTED_LANGUAGES

    for key, translations in i18n.T.items():
        missing = EXPECTED_LANGUAGES - set(translations)
        assert not missing, f"{key} missing translations: {sorted(missing)}"


def check_every_used_key_exists():
    """源码里用到的字面量键都必须在文案表里。"""
    used = set()
    sources = sorted(ROOT.glob("app/*.py")) + sorted(ROOT.glob("main.py"))
    for path in sources:
        used.update(_LITERAL_CALL.findall(path.read_text(encoding="utf-8")))
    assert used, "没有扫描到任何 i18n.tr 字面量调用，检查规则可能失效了"

    missing = sorted(key for key in used if key not in i18n.T)
    assert not missing, f"这些文案键被使用但未定义（界面会直接显示 key）：{missing}"
    return len(used)


def check_formatting_placeholders():
    for code in sorted(EXPECTED_LANGUAGES):
        i18n.set_language(code)
        assert i18n.tr("app_title")
        assert i18n.tr("downloading_percent", percent=12.3, speed="1 MB/s", eta="2s")
        assert i18n.tr("error_operation", message="test")


def main():
    check_no_duplicate_keys()
    check_translations_complete()
    used = check_every_used_key_exists()
    literals = check_no_hardcoded_cjk()
    check_formatting_placeholders()
    i18n.set_language("zh_CN")
    print(f"PASS: {len(i18n.T)} keys cover {len(EXPECTED_LANGUAGES)} languages; "
          f"{used} literal keys used in source all defined; "
          f"{literals} string literals scanned for hardcoded text")


if __name__ == "__main__":
    main()
