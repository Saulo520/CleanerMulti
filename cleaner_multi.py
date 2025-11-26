#!/usr/bin/env python3
"""
Cleaner Multi-language

Suporta 7 linguagens: JavaScript/TypeScript, Python, Java, C#, C/C++, Go e PHP.

Funcionalidades:
 - Detecção automática de linguagem por extensão
 - Scan (varredura) com cache
 - Detecção de dead files (heurísticas por linguagem)
 - Detectar imports quebrados
 - Comentar imports que apontam para uma pasta
 - Remover imports que apontam para uma pasta
 - Remover pasta inteira
 - Mover/renomear com ajuste heurístico de imports
 - Backup / snapshot + undo
 - Dry-run, logs, modo não-interativo (--yes)
 - Cancelamento digitando exit/sair/quit/q em prompts
"""
from _future_ import annotations
import os
import sys
import re
import json
import argparse
import shutil
from datetime import datetime
from typing import List, Dict, Set, Tuple, Optional

# ----------------------------
# CONFIG
# ----------------------------
DEFAULT_CONFIG = {
    "project_root": "src",
    "file_extensions_map": {
        "javascript": [".js", ".jsx", ".mjs", ".cjs"],
        "typescript": [".ts", ".tsx"],
        "python": [".py"],
        "java": [".java"],
        "csharp": [".cs"],
        "cpp": [".c", ".cpp", ".cc", ".cxx", ".h", ".hpp"],
        "go": [".go"],
        "php": [".php"]
    },
    "excluded_dirs": ["node_modules", ".git", "dist", "build", "coverage", ".next"],
    "log_dir": "logs",
    "backup_dir": "backup_snapshots",
    "cache_file": ".cleaner_cache.json",
    "safe_mode": True,
    "skip_patterns": [".d.ts", ".spec.", ".test.*"],
    "allow_undo_count": 12
}

EXIT_WORDS = {"exit", "sair", "quit", "q"}

# ----------------------------
# GLOBALS derived from config
# ----------------------------
CFG = DEFAULT_CONFIG
PROJECT_ROOT = CFG["project_root"]
LOG_DIR = CFG["log_dir"]
BACKUP_DIR = CFG["backup_dir"]
CACHE_FILE = CFG["cache_file"]
EXCLUDED_DIRS = set(CFG["excluded_dirs"])
SAFE_MODE = CFG["safe_mode"]
SKIP_PATTERNS = CFG["skip_patterns"]
ALLOW_UNDO_COUNT = CFG["allow_undo_count"]

# Build flat extension -> language map
EXT_LANG_MAP: Dict[str, str] = {}
for lang, exts in CFG["file_extensions_map"].items():
    for e in exts:
        EXT_LANG_MAP[e] = lang

os.makedirs(LOG_DIR, exist_ok=True)
os.makedirs(BACKUP_DIR, exist_ok=True)
if not os.path.isdir(PROJECT_ROOT):
    PROJECT_ROOT = "."

LOG_PATH = os.path.join(LOG_DIR, datetime.now().strftime("%Y%m%d_%H%M%S.log"))

# ----------------------------
# UTILITIES
# ----------------------------
def log(msg: str):
    ts = datetime.now().isoformat(sep=" ", timespec="seconds")
    line = f"[{ts}] {msg}"
    print(line)
    try:
        with open(LOG_PATH, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass

def safe_input(prompt: str) -> str:
    try:
        s = input(prompt)
    except EOFError:
        sys.exit(0)
    stripped = s.strip()
    if stripped.lower() in EXIT_WORDS:
        log("Processo finalizado pelo usuário via entrada.")
        sys.exit(0)
    return stripped

def ask_yes_no(prompt: str, default: bool = False, assume_yes: bool = False) -> bool:
    if assume_yes:
        return True
    ans = safe_input(f"{prompt} (s/n ou 'exit'): ")
    if ans == "":
        return default
    return ans.strip().lower().startswith("s")

# ----------------------------
# CACHE: simple cache of file list mtimes
# ----------------------------
import fnmatch

def build_file_list(root: str, use_cache: bool = True) -> List[str]:
    try:
        if use_cache and os.path.exists(CACHE_FILE):
            with open(CACHE_FILE, "r", encoding="utf-8") as f:
                cache = json.load(f)
            if cache.get("root") == os.path.abspath(root):
                entries = cache.get("files", [])
                ok = True
                for e in entries:
                    p = e.get("path")
                    if not p or not os.path.exists(p):
                        ok = False
                        break
                    if os.path.getmtime(p) != e.get("mtime"):
                        ok = False
                        break
                if ok:
                    return [e["path"] for e in entries]
    except Exception:
        pass

    files: List[str] = []
    for r, dirs, fs in os.walk(root, topdown=True):
        dirs[:] = [d for d in dirs if d not in EXCLUDED_DIRS]
        for name in fs:
            skip = False
            for patt in SKIP_PATTERNS:
                if fnmatch.fnmatch(name, patt):
                    skip = True
                    break
            if skip:
                continue
            _, ext = os.path.splitext(name)
            if ext in EXT_LANG_MAP:
                files.append(os.path.normpath(os.path.join(r, name)))
    try:
        entries = []
        for p in files:
            try:
                m = os.path.getmtime(p)
            except Exception:
                m = 0
            entries.append({"path": p, "mtime": m})
        with open(CACHE_FILE, "w", encoding="utf-8") as f:
            json.dump({"root": os.path.abspath(root), "files": entries}, f, indent=2)
    except Exception:
        pass
    return files

# ----------------------------
# LANGUAGE HANDLER
# ----------------------------
class LanguageHandler:
    def _init_(self, name: str, extensions: List[str], import_regexes: List[re.Pattern]):
        self.name = name
        self.extensions = extensions
        self.import_regexes = import_regexes

    def extract_imports(self, file_text: str) -> List[str]:
        found: List[str] = []
        for rx in self.import_regexes:
            for m in rx.finditer(file_text):
                # find first non-empty capturing group
                g = None
                if m.lastindex:
                    for i in range(1, m.lastindex + 1):
                        try:
                            val = m.group(i)
                        except IndexError:
                            val = None
                        if val:
                            g = val
                            break
                else:
                    g = m.group(0)
                if g:
                    found.append(g)
        return found

    def resolve_import(self, base_file: str, imp: str, all_files_set: Set[str]) -> Optional[str]:
        imp = imp.strip()
        if not imp:
            return None
        imp = imp.strip('"\'')
        # relative
        if imp.startswith(".") or imp.startswith("/"):
            base_dir = os.path.dirname(base_file)
            candidate = os.path.normpath(os.path.join(base_dir, imp))
            for ext in self.extensions:
                c_ext = candidate if candidate.endswith(ext) else candidate + ext
                idx = os.path.join(candidate, "index" + ext)
                for possible in (c_ext, idx):
                    if os.path.normpath(possible) in all_files_set:
                        return os.path.normpath(possible)
            if os.path.normpath(candidate) in all_files_set:
                return os.path.normpath(candidate)
            return None
        # path-like
        if "/" in imp:
            candidate = os.path.normpath(os.path.join(PROJECT_ROOT, imp))
            for ext in self.extensions:
                c_ext = candidate if candidate.endswith(ext) else candidate + ext
                idx = os.path.join(candidate, "index" + ext)
                for possible in (c_ext, idx):
                    if os.path.normpath(possible) in all_files_set:
                        return os.path.normpath(possible)
        # namespace-style (java, csharp)
        if "." in imp and self.name in ("java", "csharp"):
            candidate = os.path.normpath(os.path.join(PROJECT_ROOT, imp.replace('.', os.sep)))
            for ext in self.extensions:
                c_ext = candidate + ext
                if os.path.normpath(c_ext) in all_files_set:
                    return os.path.normpath(c_ext)
        return None

# compile regexes for languages
JS_IMPORTS = [
    re.compile(r"import\s+[^'\"]+from\s+['\"]([^'\"]+)['\"]"),
    re.compile(r"require\(\s*['\"]([^'\"]+)['\"]\s*\)"),
    re.compile(r"import\(\s*['\"]([^'\"]+)['\"]\s*\)")
]
TS_IMPORTS = JS_IMPORTS.copy()
PY_IMPORTS = [
    re.compile(r"^\s*from\s+([\w\.]+)\s+import", re.M),
    re.compile(r"^\s*import\s+([\w\.]+)", re.M)
]
JAVA_IMPORTS = [re.compile(r"^\s*import\s+([\w\.\]+)\s;", re.M)]
CSHARP_IMPORTS = [re.compile(r"^\s*using\s+([\w\.]+)\s*;", re.M)]
CPP_IMPORTS = [re.compile(r"^\s*#include\s+[\"<]([^\">]+)[\">]", re.M)]
GO_IMPORTS = [re.compile(r"import\s+\(?\s*['\"]([^'\"]+)['\"]", re.M), re.compile(r"\bimport\s*\(.*?\)", re.S)]
PHP_IMPORTS = [re.compile(r"(?:require_once|require|include_once|include)\s*\(?\s*['\"]([^'\"]+)['\"]\s*\)?")]

LANG_HANDLERS: Dict[str, LanguageHandler] = {}
LANG_HANDLERS["javascript"] = LanguageHandler("javascript", CFG["file_extensions_map"]["javascript"], JS_IMPORTS)
LANG_HANDLERS["typescript"] = LanguageHandler("typescript", CFG["file_extensions_map"]["typescript"], TS_IMPORTS)
LANG_HANDLERS["python"] = LanguageHandler("python", CFG["file_extensions_map"]["python"], PY_IMPORTS)
LANG_HANDLERS["java"] = LanguageHandler("java", CFG["file_extensions_map"]["java"], JAVA_IMPORTS)
LANG_HANDLERS["csharp"] = LanguageHandler("csharp", CFG["file_extensions_map"]["csharp"], CSHARP_IMPORTS)
LANG_HANDLERS["cpp"] = LanguageHandler("cpp", CFG["file_extensions_map"]["cpp"], CPP_IMPORTS)
LANG_HANDLERS["go"] = LanguageHandler("go", CFG["file_extensions_map"]["go"], GO_IMPORTS)
LANG_HANDLERS["php"] = LanguageHandler("php", CFG["file_extensions_map"]["php"], PHP_IMPORTS)

# ----------------------------
# util: detect language by extension
# ----------------------------
def detect_language_for_file(path: str) -> Optional[str]:
    _, ext = os.path.splitext(path)
    return EXT_LANG_MAP.get(ext)

# ----------------------------
# analyze project graph
# ----------------------------
def analyze_project(root: str, use_cache: bool = True) -> Tuple[List[str], Dict[str, Set[str]], Dict[str, Set[str]]]:
    log("Iniciando análise do projeto...")
    all_files = build_file_list(root, use_cache=use_cache)
    all_files_set = set(all_files)
    referenced_by: Dict[str, Set[str]] = {f: set() for f in all_files}
    imports_of: Dict[str, Set[str]] = {f: set() for f in all_files}

    for f in all_files:
        lang = detect_language_for_file(f)
        if not lang:
            continue
        handler = LANG_HANDLERS.get(lang)
        if not handler:
            continue
        try:
            with open(f, "r", encoding="utf-8", errors="ignore") as fh:
                text = fh.read()
        except Exception:
            continue
        imps: List[str] = []
        if lang == "go":
            for m in re.finditer(r"import\s*\((.*?)\)", text, re.S):
                block = m.group(1)
                for line in block.splitlines():
                    s = line.strip().strip('"')
                    if s:
                        imps.append(s)
            for rx in handler.import_regexes:
                for m in rx.finditer(text):
                    if m.lastindex and m.lastindex >= 1:
                        g = m.group(1)
                        if g:
                            imps.append(g)
        else:
            imps = handler.extract_imports(text)
        for imp in imps:
            target = handler.resolve_import(f, imp, all_files_set)
            if target:
                referenced_by[target].add(f)
                imports_of[f].add(target)
    return all_files, referenced_by, imports_of

# ----------------------------
# dead file heuristics
# ----------------------------
def detect_dead_files(all_files: List[str], referenced_by: Dict[str, Set[str]]) -> List[str]:
    dead: List[str] = []
    for f in all_files:
        refs = referenced_by.get(f)
        if refs:
            continue
        path_norm = f.replace("\\", "/").lower()
        basename = os.path.basename(f).lower()
        if "/pages/" in path_norm or "/routes/" in path_norm:
            continue
        if basename.startswith("index."):
            continue
        if basename in ("app.py", "main.py", "index.js", "index.ts", "server.js"):
            continue
        lang = detect_language_for_file(f)
        if lang == "java" and ("/src/main/" in path_norm or "/src/" in path_norm):
            continue
        dead.append(f)
    return sorted(dead)

# ----------------------------
# detect unused exports (heuristics) for JS/TS & Python
# ----------------------------
JS_EXPORT_DEF = re.compile(r"export\s+(?:default\s+)?(?:function|const|let|var|class)\s+([A-Za-z0-9_]+)")
JS_NAMED_EXPORTS = re.compile(r"export\s*\{([^}]+)\}")
PY_DEF = re.compile(r"^\s*def\s+([A-Za-z0-9_]+)\s*\(|^\s*class\s+([A-Za-z0-9_]+)\s*\(", re.M)
PY_ALL = re.compile(r"_all_\s*=\s*\[(.*?)\]", re.S)

def detect_unused_exports(all_files: List[str], imports_of: Dict[str, Set[str]]) -> Dict[str, List[str]]:
    exports_map: Dict[str, List[str]] = {}
    texts: Dict[str, str] = {}
    for f in all_files:
        try:
            with open(f, "r", encoding="utf-8", errors="ignore") as fh:
                txt = fh.read()
                texts[f] = txt
        except Exception:
            texts[f] = ""
    for f, txt in texts.items():
        lang = detect_language_for_file(f)
        names: List[str] = []
        if lang in ("javascript", "typescript"):
            for m in JS_EXPORT_DEF.finditer(txt):
                n = m.group(1)
                if n:
                    names.append(n)
            for m in JS_NAMED_EXPORTS.finditer(txt):
                grp = m.group(1)
                for p in grp.split(','):
                    n = p.strip().split(' as ')[0].strip()
                    if n:
                        names.append(n)
        elif lang == "python":
            m = PY_ALL.search(txt)
            if m:
                content = m.group(1)
                for part in re.split(r',\s*', content):
                    n = part.strip().strip('\"\'')
                    if n:
                        names.append(n)
            else:
                for m in PY_DEF.finditer(txt):
                    name = m.group(1) or m.group(2)
                    if name:
                        names.append(name)
        if names:
            exports_map[f] = names
    unused: Dict[str, List[str]] = {}
    for f, names in exports_map.items():
        for name in names:
            used = False
            for other, txt in texts.items():
                if other == f:
                    continue
                if re.search(rf"\b{name}\b", txt):
                    used = True
                    break
            if not used:
                unused.setdefault(f, []).append(name)
    return unused

# ----------------------------
# backup / snapshot / undo
# ----------------------------
def now_ts() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")

def create_snapshot(label: str) -> str:
    ts = now_ts()
    name = f"{ts}{label}".replace(" ", "")
    dest = os.path.join(BACKUP_DIR, name)
    try:
        shutil.copytree(PROJECT_ROOT, dest)
        with open(os.path.join(dest, ".snapshot_meta.json"), "w", encoding="utf-8") as f:
            json.dump({"label": label, "timestamp": ts}, f)
        trim_snapshots()
        log(f"Snapshot criado: {dest}")
        return dest
    except Exception as e:
        log(f"Falha ao criar snapshot: {e}")
        return ""

def list_snapshots() -> List[str]:
    try:
        items = sorted([os.path.join(BACKUP_DIR, d) for d in os.listdir(BACKUP_DIR)], reverse=True)
        return [p for p in items if os.path.isdir(p)]
    except Exception:
        return []

def trim_snapshots():
    snaps = list_snapshots()
    if len(snaps) <= ALLOW_UNDO_COUNT:
        return
    for p in snaps[ALLOW_UNDO_COUNT:]:
        try:
            shutil.rmtree(p)
        except Exception:
            pass

def undo_last() -> bool:
    snaps = list_snapshots()
    if not snaps:
        log("Nenhum snapshot para restaurar.")
        return False
    last = snaps[0]
    try:
        for name in os.listdir(PROJECT_ROOT):
            path = os.path.join(PROJECT_ROOT, name)
            try:
                if os.path.isdir(path):
                    shutil.rmtree(path)
                else:
                    os.remove(path)
            except Exception:
                pass
        for item in os.listdir(last):
            if item == ".snapshot_meta.json":
                continue
            s = os.path.join(last, item)
            d = os.path.join(PROJECT_ROOT, item)
            if os.path.isdir(s):
                shutil.copytree(s, d)
            else:
                shutil.copy2(s, d)
        log("Snapshot restaurado com sucesso.")
        return True
    except Exception as e:
        log(f"Falha no undo: {e}")
        return False

# ----------------------------
# actions
# ----------------------------
def show_preview(title: str, items: List[str]):
    log(f"PRÉVIA — {title}")
    if not items:
        log("  (Nada encontrado)")
        return
    for p in items:
        log(" - " + p)

def comment_imports(target_folder: str, dry_run: bool = False, assume_yes: bool = False):
    files = build_file_list(PROJECT_ROOT)
    preview: List[str] = []
    for f in files:
        lang = detect_language_for_file(f)
        if not lang:
            continue
        handler = LANG_HANDLERS.get(lang)
        if not handler:
            continue
        try:
            with open(f, "r", encoding="utf-8", errors="ignore") as fh:
                lines = fh.readlines()
        except Exception:
            continue
        for line in lines:
            for rx in handler.import_regexes:
                m = rx.search(line)
                if m:
                    imp = None
                    if m.lastindex and m.lastindex >= 1:
                        imp = m.group(1)
                    if not imp:
                        imp = m.group(0)
                    if target_folder in imp.replace("\\", "/"):
                        preview.append(f)
                        break
            else:
                continue
            break
    show_preview(f"Arquivos que terão imports comentados (folder={target_folder})", preview)
    if dry_run:
        log("Dry-run ativado: nada será alterado.")
        return
    if SAFE_MODE and not assume_yes:
        if not ask_yes_no("Confirmar comentário dos imports?"):
            log("Operação cancelada.")
            return
    create_snapshot(f"comment_imports_{target_folder}")
    for f in preview:
        try:
            with open(f, "r", encoding="utf-8", errors="ignore") as fh:
                lines = fh.readlines()
        except Exception:
            continue
        changed = False
        new_lines: List[str] = []
        handler = LANG_HANDLERS.get(detect_language_for_file(f))
        for line in lines:
            commented = False
            for rx in handler.import_regexes:
                if rx.search(line) and target_folder in line:
                    # comment respecting common comment style: use // for C-like, # for python, // fallback
                    lang = detect_language_for_file(f)
                    if lang == "python":
                        new_lines.append("# " + line.rstrip("\n") + "  # removido pelo script\n")
                    elif lang in ("javascript", "typescript", "java", "csharp", "cpp", "go", "php"):
                        new_lines.append("// " + line.rstrip("\n") + " // removido pelo script\n")
                    else:
                        new_lines.append("// " + line.rstrip("\n") + " // removido pelo script\n")
                    commented = True
                    changed = True
                    break
            if not commented:
                new_lines.append(line)
        if changed:
            try:
                with open(f, "w", encoding="utf-8") as fh:
                    fh.writelines(new_lines)
                log(f"Arquivo modificado: {f}")
            except Exception as e:
                log(f"Falha ao escrever {f}: {e}")

def remove_imports(target_folder: str, dry_run: bool = False, assume_yes: bool = False):
    files = build_file_list(PROJECT_ROOT)
    preview: List[str] = []
    for f in files:
        lang = detect_language_for_file(f)
        if not lang:
            continue
        handler = LANG_HANDLERS.get(lang)
        try:
            with open(f, "r", encoding="utf-8", errors="ignore") as fh:
                text = fh.read()
        except Exception:
            continue
        for rx in handler.import_regexes:
            for m in rx.finditer(text):
                imp = m.group(1) if m.lastindex and m.lastindex >= 1 else None
                if imp and target_folder in imp:
                    preview.append(f)
                    break
            else:
                continue
            break
    show_preview(f"Arquivos com imports a remover (folder={target_folder})", preview)
    if dry_run:
        log("Dry-run ativado: nada será alterado.")
        return
    if SAFE_MODE and not assume_yes:
        if not ask_yes_no("Confirmar remoção dos imports?"):
            log("Operação cancelada.")
            return
    create_snapshot(f"remove_imports_{target_folder}")
    for f in preview:
        try:
            with open(f, "r", encoding="utf-8", errors="ignore") as fh:
                lines = fh.readlines()
        except Exception:
            continue
        new_lines: List[str] = []
        removed = False
        handler = LANG_HANDLERS.get(detect_language_for_file(f))
        for line in lines:
            keep = True
            for rx in handler.import_regexes:
                m = rx.search(line)
                if m:
                    imp = m.group(1) if m.lastindex and m.lastindex >= 1 else None
                    if imp and target_folder in imp:
                        keep = False
                        removed = True
                        break
            if keep:
                new_lines.append(line)
        if removed:
            try:
                with open(f, "w", encoding="utf-8") as fh:
                    fh.writelines(new_lines)
                log(f"Imports removidos em: {f}")
            except Exception as e:
                log(f"Falha ao escrever {f}: {e}")

def remove_folder(target_folder: str, dry_run: bool = False, assume_yes: bool = False):
    path = os.path.join(PROJECT_ROOT, target_folder)
    if not os.path.isdir(path):
        log("Pasta não encontrada: " + path)
        return
    files_to_delete: List[str] = []
    for r, _, fs in os.walk(path):
        for name in fs:
            files_to_delete.append(os.path.join(r, name))
    show_preview(f"Arquivos que serão apagados da pasta {target_folder}", files_to_delete)
    if dry_run:
        log("Dry-run ativado: nada será alterado.")
        return
    if SAFE_MODE and not assume_yes:
        if not ask_yes_no("Confirmar exclusão da pasta?"):
            log("Operação cancelada.")
            return
    create_snapshot(f"remove_folder_{target_folder}")
    try:
        shutil.rmtree(path)
        log(f"Pasta removida: {path}")
    except Exception as e:
        log(f"Falha ao remover pasta: {e}")

def detect_and_handle_dead(dry_run: bool = False, assume_yes: bool = False, use_cache: bool = True):
    all_files, referenced_by, imports_of = analyze_project(PROJECT_ROOT, use_cache=use_cache)
    dead = detect_dead_files(all_files, referenced_by)
    show_preview("Arquivos possivelmente mortos", dead)
    if not dead:
        log("Nenhum arquivo morto detectado.")
        return
    if dry_run:
        log("Dry-run ativado: nada será apagado.")
        return
    to_delete: List[str] = []
    for f in dead:
        if SAFE_MODE and not assume_yes:
            if ask_yes_no(f"Apagar este arquivo?\n{f}"):
                to_delete.append(f)
        else:
            to_delete.append(f)
    if not to_delete:
        log("Nenhum arquivo selecionado para remoção.")
        return
    create_snapshot("delete_dead_files")
    for f in to_delete:
        try:
            os.remove(f)
            log("Apagado: " + f)
        except Exception as e:
            log(f"Falha ao apagar {f}: {e}")
    log("Operação de dead code finalizada.")

def detect_broken_imports(use_cache: bool = True) -> List[Tuple[str, str]]:
    all_files, referenced_by, imports_of = analyze_project(PROJECT_ROOT, use_cache=use_cache)
    all_set = set(all_files)
    broken: List[Tuple[str, str]] = []
    for src in all_files:
        lang = detect_language_for_file(src)
        if not lang:
            continue
        handler = LANG_HANDLERS.get(lang)
        try:
            with open(src, "r", encoding="utf-8", errors="ignore") as fh:
                text = fh.read()
        except Exception:
            continue
        imps = handler.extract_imports(text)
        for imp in imps:
            if imp and handler.resolve_import(src, imp, all_set) is None:
                # if looks like local-ish import (contains '.' or '/'), mark broken
                if imp.startswith(".") or "/" in imp or ("." in imp and lang in ("java", "csharp")):
                    broken.append((src, imp))
    return broken

def move_and_fix(src_rel: str, dest_rel: str, dry_run: bool = False, assume_yes: bool = False):
    abs_src = os.path.normpath(os.path.join(PROJECT_ROOT, src_rel))
    abs_dest = os.path.normpath(os.path.join(PROJECT_ROOT, dest_rel))
    if not os.path.exists(abs_src):
        log("Origem não existe: " + abs_src)
        return
    all_files = build_file_list(PROJECT_ROOT)
    all_set = set(all_files)
    affected: Set[str] = set()
    for f in all_files:
        lang = detect_language_for_file(f)
        if not lang:
            continue
        handler = LANG_HANDLERS.get(lang)
        try:
            with open(f, "r", encoding="utf-8", errors="ignore") as fh:
                txt = fh.read()
        except Exception:
            continue
        for imp in handler.extract_imports(txt):
            target = handler.resolve_import(f, imp, all_set)
            if target and (target == abs_src or target.startswith(abs_src + os.sep)):
                affected.add(f)
    show_preview("Arquivos que serão atualizados", sorted(affected))
    if dry_run:
        log("Dry-run ativado: nada será movido/alterado.")
        return
    if SAFE_MODE and not assume_yes:
        if not ask_yes_no(f"Confirmar mover {abs_src} → {abs_dest} e ajustar imports?"):
            log("Cancelado.")
            return
    create_snapshot(f"move_{os.path.basename(src_rel)}")
    try:
        shutil.move(abs_src, abs_dest)
    except Exception as e:
        log(f"Falha ao mover: {e}")
        return
    old_rel = os.path.relpath(abs_src, PROJECT_ROOT).replace("\\", "/")
    new_rel = os.path.relpath(abs_dest, PROJECT_ROOT).replace("\\", "/")
    for f in affected:
        try:
            with open(f, "r", encoding="utf-8", errors="ignore") as fh:
                txt = fh.read()
            new_txt = txt.replace(old_rel, new_rel)
            with open(f, "w", encoding="utf-8") as fh:
                fh.write(new_txt)
            log(f"Atualizado: {f}")
        except Exception as e:
            log(f"Falha ao atualizar {f}: {e}")
    log("Move + fix concluído.")

# ----------------------------
# CLI
# ----------------------------
def main():
    parser = argparse.ArgumentParser(description="Cleaner Multi-language")
    sub = parser.add_subparsers(dest="cmd", required=True)

    base = argparse.ArgumentParser(add_help=False)
    base.add_argument("--dry-run", action="store_true")
    base.add_argument("--yes", action="store_true")
    base.add_argument("--no-cache", action="store_true")

    p_scan = sub.add_parser("scan", parents=[base], help="Scan project and report")
    p_scan.add_argument("--detailed-unused-exports", action="store_true")

    p_comment = sub.add_parser("comment-imports", parents=[base], help="Comment imports pointing to folder")
    p_comment.add_argument("folder")

    p_remove_imports = sub.add_parser("remove-imports", parents=[base], help="Remove imports pointing to folder")
    p_remove_imports.add_argument("folder")

    p_remove_folder = sub.add_parser("remove-folder", parents=[base], help="Remove folder whole")
    p_remove_folder.add_argument("folder")

    p_dead = sub.add_parser("dead", parents=[base], help="Detect and optionally remove dead files")
    p_broken = sub.add_parser("broken", parents=[base], help="Detect broken imports")

    p_move = sub.add_parser("move", parents=[base], help="Move file/folder and fix imports")
    p_move.add_argument("src")
    p_move.add_argument("dest")

    p_undo = sub.add_parser("undo", help="Undo last snapshot")

    args = parser.parse_args()
    dry = getattr(args, "dry_run", False)
    assume_yes = getattr(args, "yes", False)
    use_cache = not getattr(args, "no_cache", False)

    log(f"Executando comando: {args.cmd}")

    if args.cmd == "scan":
        all_files, referenced_by, imports_of = analyze_project(PROJECT_ROOT, use_cache=use_cache)
        log(f"Arquivos analisados: {len(all_files)}")
        dead = detect_dead_files(all_files, referenced_by)
        show_preview("Possíveis dead files", dead)
        if getattr(args, "detailed_unused_exports", False):
            unused = detect_unused_exports(all_files, imports_of)
            log("Possíveis exports não usados:")
            for f, names in unused.items():
                log(f" - {f}: {', '.join(names)}")
    elif args.cmd == "comment-imports":
        comment_imports(args.folder, dry_run=dry, assume_yes=assume_yes)
    elif args.cmd == "remove-imports":
        remove_imports(args.folder, dry_run=dry, assume_yes=assume_yes)
    elif args.cmd == "remove-folder":
        remove_folder(args.folder, dry_run=dry, assume_yes=assume_yes)
    elif args.cmd == "dead":
        detect_and_handle_dead(dry_run=dry, assume_yes=assume_yes, use_cache=use_cache)
    elif args.cmd == "broken":
        broken = detect_broken_imports(use_cache=use_cache)
        if not broken:
            log("Nenhum import quebrado detectado.")
        else:
            log("Imports quebrados:")
            for src, imp in broken:
                log(f" - {src} importa {imp}")
    elif args.cmd == "move":
        move_and_fix(args.src, args.dest, dry_run=dry, assume_yes=assume_yes)
    elif args.cmd == "undo":
        ok = undo_last()
        if not ok:
            log("Undo não realizado.")
    else:
        log("Comando desconhecido")

if _name_ == '_main_':
    try:
        main()
    except KeyboardInterrupt:
        log("Interrompido pelo usuário (Ctrl+C)")
        sys.exit(0)