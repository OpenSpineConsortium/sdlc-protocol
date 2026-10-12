"""ecosystem.json, read once for the scripts that act on it.

ecosystem.json at the repository's root is the one place that says which
repositories take this package, where each keeps its copy, which modules
it takes, what it uses them for and how it checks itself, and, for a
consumer that ships its copy to users of its own, the file whose version
tells them there is a new one. scripts/vendor.py copies by it,
scripts/sync.py plans the sync's pull requests by it, the workflows'
downstream and sync jobs run a leg per consumer from it, and
scripts/architecture.py draws ECOSYSTEM.md from it. This module is what
they share: reading the file and holding it to its shape, finding a
consumer, the files a consumer's copy holds, the imports between the
package's modules, which decide whether a copy of a few of them can run,
and reading and raising the version a consumer's version file states.
"""

import ast
import importlib.util
import json
import re
from pathlib import Path, PurePosixPath

ROOT = Path(__file__).resolve().parent.parent
ECOSYSTEM = ROOT / "ecosystem.json"
PACKAGE_DIR = ROOT / "sdlc_protocol"


def load_vendoring(package_dir=PACKAGE_DIR):
    """The package's vendoring.py, loaded from its file alone, as a
    consumer's check runs it: the scripts never import the package itself,
    so a package in the middle of a change cannot stop its own copy, and
    the version is read from __init__.py's text."""
    spec = importlib.util.spec_from_file_location("vendoring", package_dir / "vendoring.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


vendoring = load_vendoring()

ALL = "all"
# What GitHub says a repository is. An internal one is seen only inside its
# enterprise, which exists only on GitHub Enterprise Cloud, where auto-merge
# is there too; only a public one can be read by another repository's run
# with that run's own token.
VISIBILITIES = ("public", "internal", "private")
REPOSITORY = re.compile(r"[A-Za-z0-9-]+/[A-Za-z0-9._-]+")
TOP_KEYS = {"library", "about", "package", "always_copied", "consumers"}
CONSUMER_KEYS = {"repository", "visibility", "about", "vendor_dir", "modules", "check", "auto_merge", "uses"}
OPTIONAL_CONSUMER_KEYS = {"version_file"}
TEXT_KEYS = ("about", "check", "uses")
VERSION = re.compile(r"(\d+)\.(\d+)\.(\d+)")


class EcosystemError(Exception):
    """What ecosystem.json, or the choice of a consumer in it, gets wrong."""


def load(path=ECOSYSTEM):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def short_name(repository):
    """OWNER/NAME's NAME, which names a consumer's legs in the workflows."""
    return repository.rpartition("/")[2]


def consumer_named(ecosystem, repository):
    """The consumer whose repository is OWNER/NAME, whatever the case."""
    for consumer in ecosystem["consumers"]:
        if consumer["repository"].lower() == repository.lower():
            return consumer
    names = ", ".join(consumer["repository"] for consumer in ecosystem["consumers"])
    raise EcosystemError(f"{repository} is not a consumer in ecosystem.json; the consumers are {names}")


def files_for(ecosystem, consumer, package_dir=PACKAGE_DIR):
    """The files a consumer's copy holds, as sorted paths inside the
    package: every file for "all", else its modules and the ones every
    copy takes."""
    if consumer["modules"] == ALL:
        return vendoring.copied_files(package_dir)
    return sorted(set(consumer["modules"]) | set(ecosystem["always_copied"]))


# --- the imports between the package's modules ----------------------------------------

def module_files(package_dir=PACKAGE_DIR):
    return [name for name in vendoring.copied_files(package_dir) if name.endswith(".py")]


def module_name(relative):
    """A module's dotted name inside the package, as a reader names it:
    adapters/github.py is adapters.github, adapters/__init__.py adapters."""
    parts = PurePosixPath(relative).with_suffix("").parts
    if parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts) or "__init__"


def file_of(parts, package_dir):
    """The file that a module path inside the package names: a module, or
    a subpackage's __init__.py; a module that is not there is named as one
    that should be, so the caller can say it is missing."""
    if not parts:
        return "__init__.py"
    stem = "/".join(parts)
    if (package_dir / stem).is_dir():
        return f"{stem}/__init__.py"
    return f"{stem}.py"


def is_module(parts, package_dir):
    stem = "/".join(parts)
    return (package_dir / f"{stem}.py").is_file() or (package_dir / stem / "__init__.py").is_file()


def base_of(node, relative):
    """The module path inside the package that an import names, or None
    for an import from outside the package."""
    named = node.module.split(".") if node.module else []
    if node.level == 0:
        return named[1:] if named and named[0] == vendoring.PACKAGE else None
    here = list(PurePosixPath(relative).parent.parts)
    climbed = here[:len(here) - (node.level - 1)] if node.level > 1 else here
    return climbed + named


def targets_of_from(node, relative, package_dir):
    """What "from <base> import <names>" needs: each name that is a module
    of its own, and base itself for each name that is not. The packages
    around a module are enclosing_packages' to add, not this."""
    base = base_of(node, relative)
    if base is None:
        return set()
    found = set()
    for alias in node.names:
        named = base + [alias.name]
        found.add(file_of(named if is_module(named, package_dir) else base, package_dir))
    return found


def targets_of_import(node, package_dir):
    found = set()
    for alias in node.names:
        parts = alias.name.split(".")
        if parts[0] == vendoring.PACKAGE and parts[1:]:
            found.add(file_of(parts[1:], package_dir))
    return found


def imports_of(relative, package_dir=PACKAGE_DIR):
    """The package's files the module at relative imports, anywhere in it
    (an import inside a function counts: it runs when the function does)."""
    tree = ast.parse((package_dir / relative).read_text(encoding="utf-8"))
    found = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            found |= targets_of_from(node, relative, package_dir)
        elif isinstance(node, ast.Import):
            found |= targets_of_import(node, package_dir)
    found.discard(relative)
    return sorted(found)


def outside_imports(relative, package_dir=PACKAGE_DIR):
    """The top-level names of what the module imports from outside the
    package, which must all be the standard library's."""
    tree = ast.parse((package_dir / relative).read_text(encoding="utf-8"))
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names |= {alias.name.split(".")[0] for alias in node.names}
        elif isinstance(node, ast.ImportFrom) and node.level == 0:
            names.add(node.module.split(".")[0])
    names.discard(vendoring.PACKAGE)
    return sorted(names)


def enclosing_packages(relative):
    """The __init__.py of each package a module sits in, which Python runs
    before the module itself."""
    parents = PurePosixPath(relative).parent.parts
    return ["/".join([*parents[:depth], "__init__.py"]) for depth in range(len(parents) + 1)]


def missing_imports(files, package_dir=PACKAGE_DIR):
    """Each (module, file) where a module of the copy needs a file of the
    package that the copy leaves out."""
    copied = set(files)
    missing = []
    for name in sorted(copied):
        if not name.endswith(".py"):
            continue
        needed = set(imports_of(name, package_dir)) | set(enclosing_packages(name))
        missing += [(name, file) for file in sorted(needed - copied - {name})]
    return missing


# --- a consumer's version file ---------------------------------------------------------
#
# A consumer that ships its copy on to users of its own names the file
# whose "version" tells their installs that something new is there: the
# plugin marketplace's plugins/sdlc/.claude-plugin/plugin.json, since
# Claude Code keeps an installed plugin on its cached copy until the
# version in its manifest changes. scripts/vendor.py raises that version's
# patch number whenever the copy's files change, so the release is part of
# the sync's own commit and not a step someone has to remember.

def stated_version(text, where):
    """The MAJOR.MINOR.PATCH version a version file's JSON states."""
    try:
        data = json.loads(text)
    except ValueError as problem:
        raise EcosystemError(f"{where} is not JSON: {problem}") from None
    version = data.get("version") if isinstance(data, dict) else None
    if not isinstance(version, str) or not VERSION.fullmatch(version):
        raise EcosystemError(f"{where} states no version of the form MAJOR.MINOR.PATCH")
    return version


def next_patch(version):
    """The version a release of a new copy takes: the patch number plus one."""
    major, minor, patch = VERSION.fullmatch(version).groups()
    return f"{major}.{minor}.{int(patch) + 1}"


def with_version(text, version, where):
    """The version file's text with its version raised to version and every
    other byte left as it was, so the sync's diff there is one line."""
    old = stated_version(text, where)
    pattern = re.compile(r'("version"\s*:\s*")' + re.escape(old) + '"')
    raised, count = pattern.subn(lambda found: f'{found.group(1)}{version}"', text)
    if count != 1 or stated_version(raised, where) != version:
        raise EcosystemError(f"{where}: its version {old} cannot be told apart from the rest of the file; "
                             "raise it by hand")
    return raised


# --- the file's shape ----------------------------------------------------------------

def problems(ecosystem, package_dir=PACKAGE_DIR):
    """What is wrong with ecosystem.json, one sentence each; [] when the
    scripts and the workflows can act on it."""
    if not isinstance(ecosystem, dict):
        return ["ecosystem.json is not a JSON object"]
    found = key_problems("ecosystem.json", ecosystem, TOP_KEYS)
    if ecosystem.get("package") != vendoring.PACKAGE:
        found.append(f"package is not {vendoring.PACKAGE}")
    if not REPOSITORY.fullmatch(str(ecosystem.get("library", ""))):
        found.append("library is not OWNER/NAME")
    found += module_list_problems("always_copied", ecosystem.get("always_copied"), package_dir)
    consumers = ecosystem.get("consumers")
    if not isinstance(consumers, list) or not consumers:
        return found + ["consumers is not a list of at least one consumer"]
    for consumer in consumers:
        found += consumer_problems(ecosystem, consumer, package_dir)
    names = [str(consumer.get("repository", "")).lower() for consumer in consumers if isinstance(consumer, dict)]
    found += [f"{name} is listed more than once" for name in sorted(set(names)) if names.count(name) > 1]
    return found


def key_problems(where, value, keys, optional=frozenset()):
    found = [f"{where} lacks {key}" for key in sorted(keys - set(value))]
    return found + [f"{where} has {key}, which nothing reads" for key in sorted(set(value) - keys - optional)]


def module_list_problems(where, modules, package_dir):
    if not isinstance(modules, list) or not modules or not all(isinstance(name, str) for name in modules):
        return [f"{where} is not a list of the package's files"]
    found = [f"{where} names {name}, which sdlc_protocol/ does not have" for name in modules
             if not (package_dir / name).is_file() or not vendoring.is_inside_the_copy(name)]
    return found + [f"{where} names {name} twice" for name in sorted(set(modules)) if modules.count(name) > 1]


def consumer_problems(ecosystem, consumer, package_dir):
    if not isinstance(consumer, dict):
        return ["a consumer is not a JSON object"]
    where = str(consumer.get("repository", "a consumer"))
    found = key_problems(where, consumer, CONSUMER_KEYS, OPTIONAL_CONSUMER_KEYS)
    if not REPOSITORY.fullmatch(where):
        found.append(f"{where}: repository is not OWNER/NAME")
    found += [f"{where}: {key} is not a sentence" for key in TEXT_KEYS
              if not isinstance(consumer.get(key), str) or not consumer.get(key).strip()]
    found += visibility_problems(where, consumer)
    vendor_dir = consumer.get("vendor_dir")
    if not isinstance(vendor_dir, str) or not vendoring.is_inside_the_copy(vendor_dir):
        found.append(f"{where}: vendor_dir is not a relative path inside the repository")
    found += version_file_problems(where, consumer)
    found += copy_problems(ecosystem, where, consumer.get("modules"), package_dir)
    return found


def version_file_problems(where, consumer):
    """Whether a consumer's version file, when it names one, is a JSON file
    of its own tree that the copy, which the sync replaces, does not hold."""
    if "version_file" not in consumer:
        return []
    path = consumer["version_file"]
    if not isinstance(path, str) or not vendoring.is_inside_the_copy(path) or not path.endswith(".json"):
        return [f"{where}: version_file is not a relative path to a .json file inside the repository"]
    copy = f"{consumer.get('vendor_dir')}/{vendoring.PACKAGE}/"
    if path.startswith(copy):
        return [f"{where}: version_file lies inside the copy, which the sync replaces"]
    return []


def visibility_problems(where, consumer):
    visibility, auto_merge = consumer.get("visibility"), consumer.get("auto_merge")
    if visibility not in VISIBILITIES:
        return [f"{where}: visibility is not one of {', '.join(VISIBILITIES)}"]
    if not isinstance(auto_merge, bool):
        return [f"{where}: auto_merge is not true or false"]
    return []


def copy_problems(ecosystem, where, modules, package_dir):
    """Whether the modules a consumer takes exist and can run as copied."""
    if modules == ALL:
        files = vendoring.copied_files(package_dir)
    else:
        found = module_list_problems(f"{where}: modules", modules, package_dir)
        if found or module_list_problems("always_copied", ecosystem.get("always_copied"), package_dir):
            return found
        files = files_for(ecosystem, {"modules": modules}, package_dir)
    return [f"{where}: {module} imports {needed}, which its copy leaves out"
            for module, needed in missing_imports(files, package_dir)]
