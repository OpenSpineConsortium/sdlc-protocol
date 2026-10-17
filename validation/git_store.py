"""The git world: the model's refs and commits kept in a real git repository
(DESIGN.md, 3.4 and 14, step 6).

GitStore is a bare repository in a temporary directory with one clone per
actor, built the way tests/test_bare_git.py and tests/test_board.py build
theirs. A model attached to a store keeps its issues, labels, comments and
pull requests in memory, since git has none, and takes every ref, commit,
tree, committer date and standing from the bare repository: a worker's
verb that pushes runs real git in that worker's clone (switch, commit,
push, fetch, rebase, merge, cherry-pick), with GIT_AUTHOR_DATE and
GIT_COMMITTER_DATE from the scenario's clock, and the push is taken or
refused by git itself, in git's own words; after it the store reads the
bare repository's refs back into the model, with every commit they reach
that the model has not seen (parents, author, committer date, tree and
message, under git's own sha), so the fake GitHub, which reads the model,
answers the board run from what git holds. The board's rename and delete
are made in the bare repository (git branch -m, git update-ref -d), and a
branch's standing to main and the files it changes come from git
(merge-tree --write-tree, as adapters/bare_git.py reads them; diff
--name-only from the merge base).

A pull request from a fork (F17) is the one exception: its head is a
commit the model makes on main's tip, which no ref here names and the
bare repository never holds, so its standing and files are the model's
rule's in both worlds. A copy of an attached model is detached from the
store and answers from what the model read, and for a branch's standing
with the word git gave the attached model for that head and main
(model.standings), else by the model's rule. Every copy shares that
word, the states a run in two halves keeps among them, since the oracle
and G9's second run read copies, and git's word and the model's rule
part where three pieces of work change one file, behind against dirty,
and under lenient rules (F16) a branch behind main is owed nothing where
a dirty one is owed "main moved". A commit made on a copy, as a dealing
look's change makes one, is no git object, so a branch at one is judged
by the model's rule.

So the two worlds differ only where git decides (DESIGN.md, 3.6): whether
a push is taken, and whether two changes conflict, which git judges at
line level and the model at file level. A conflict a verb meets in git
(a merge or a rebase that brings main in, a cherry-pick that carries work
on, the owner's merge) is resolved the way the memory verb builds its
tree, so the trees agree wherever git and the model agree a conflict
stands, and the memory rebase drops what git's drops, a merge commit and
a commit whose patch main holds already (verbs.rebase_main); a
difference is what the conformance lane reports (deep.py).

Every git command runs with its own identity, no global or system
configuration, no hooks and no automatic maintenance, in the environment
and in each repository's configuration both, since git does not pass the
environment's configuration to the receive-pack it starts for a local
remote and a detached maintenance run races the temporary directory's
removal.
"""

import os
import shutil
import subprocess
import tempfile
from pathlib import Path

from sdlc_protocol import guard

from . import cycle, oracle, verbs
from .model import BEHIND, CLAIM_PREFIX, CLEAN, DIRTY, MAIN, OWNER, T0, Commit, claim_text, iso
from .verbs import CLAIM_DIR, Outcome, guard_says

# GitHub's own clone, in which the owner's merge and someone else's push
# to main are made.
GITHUB = "_github"
ENV = {**os.environ, "GIT_TERMINAL_PROMPT": "0", "LC_ALL": "C", "GIT_CONFIG_GLOBAL": os.devnull,
       "GIT_CONFIG_NOSYSTEM": "1", "GIT_EDITOR": "true"}
# What every repository is told, in the environment and in its config.
SETTINGS = (("commit.gpgsign", "false"), ("init.defaultBranch", MAIN), ("gc.auto", "0"), ("gc.autoDetach", "false"),
            ("maintenance.auto", "false"), ("maintenance.autoDetach", "false"), ("receive.autogc", "false"),
            ("core.hooksPath", os.devnull), ("advice.detachedHead", "false"))
# The verbs a store makes with git; every other verb changes the model
# alone, or reaches the store through the model's own methods (a rename,
# a delete, a fetch).
GIT_VERBS = frozenset({"claim", "claim-misnamed", "push-by-hand", "commit", "drop-claim-file", "merge-main",
                       "rebase-main", "lease-onto", "lease-elsewhere", "push-old-head", "drop-commits", "carry-on",
                       "merge", "merge-keep", "move-main"})
EPOCH = int(T0.timestamp())
# How many conflicts a rebase may stop at before the store calls it stuck.
MOST_CONFLICTS = 50


class GitFailed(Exception):
    """A git command the store expected to succeed failed: the harness's
    own failure, never the library's."""


def settings_arguments():
    found = []
    for name, value in SETTINGS:
        found += ["-c", f"{name}={value}"]
    return found


def identity(actor):
    return ["-c", f"user.name={actor}", "-c", f"user.email={actor}@example.invalid"]


def git(cwd, *args, actor=OWNER, when=None, check=True):
    """One git command in cwd, as the actor, with the clock's date on any
    commit it makes."""
    env = dict(ENV)
    if when is not None:
        env["GIT_AUTHOR_DATE"] = env["GIT_COMMITTER_DATE"] = when
    done = subprocess.run(["git", *settings_arguments(), *identity(actor), *args], cwd=cwd, capture_output=True,
                          text=True, errors="replace", env=env, timeout=120)
    if check and done.returncode != 0:
        raise GitFailed(f"git {' '.join(args)} in {cwd}: {done.stderr.strip() or done.stdout.strip()}")
    return done


def configure(repo):
    for name, value in SETTINGS:
        git(repo, "config", name, value)


def push_words(done):
    """Why git refused a push, in its own words, as the model gives them."""
    text = done.stderr + done.stdout
    for words in ("fetch first", "stale info", "non-fast-forward", "already exists"):
        if f"({words})" in text:
            return words
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    return lines[-1] if lines else f"git exited with {done.returncode}"


class GitStore:
    """The bare repository and the clones, bound to one model by attach()."""

    def __init__(self):
        self.root = Path(tempfile.mkdtemp(prefix="sdlc-git-world-"))
        self.bare = self.root / "origin.git"
        git(self.root, "init", "-q", "--bare", "--initial-branch", MAIN, str(self.bare))
        configure(self.bare)
        self.clones = {}
        self.closed = False

    def __deepcopy__(self, memo):
        """A model copied whole (RepoModel.copied_whole: the states a run
        in two halves keeps) is detached from the store, as RepoModel.clone
        detaches a copy: it keeps what the model read from git, shares the
        standings git gave, and changes no repository."""
        return None

    def close(self):
        self.closed = True
        shutil.rmtree(self.root, ignore_errors=True)

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()

    # --- the clones -----------------------------------------------------

    def clone_of(self, actor):
        if actor not in self.clones:
            path = self.root / "clones" / actor
            path.parent.mkdir(exist_ok=True)
            git(self.root, "clone", "-q", str(self.bare), str(path), actor=actor)
            configure(path)
            self.clones[actor] = path
        return self.clones[actor]

    def fetch_refs(self, actor, *names):
        """Bring the remote's branches named, or every branch, into the
        clone's remote-tracking refs, as a worker's fetch does."""
        clone = self.clone_of(actor)
        specs = [f"+refs/heads/{n}:refs/remotes/origin/{n}" for n in names] or ["+refs/heads/*:refs/remotes/origin/*"]
        git(clone, "fetch", "-q", "--prune", "origin", *specs, actor=actor)
        return clone

    def start_from(self, actor, branch, base):
        """The clone on the branch, reset to the remote's base, with nothing
        left over from an earlier verb."""
        clone = self.clone_of(actor)
        for abort in (("merge", "--abort"), ("rebase", "--abort"), ("cherry-pick", "--abort")):
            git(clone, *abort, actor=actor, check=False)
        git(clone, "reset", "-q", "--hard", actor=actor)
        git(clone, "clean", "-fdq", actor=actor)
        git(clone, "switch", "-q", "-C", branch, base, actor=actor)
        return clone

    # --- attaching and reading back -------------------------------------

    def attach(self, model, actors=()):
        """Make the bare repository hold what the model holds, every commit
        of every ref, and key the model by git's own shas; the model then
        reads from the store. Each actor named gets its clone now, before
        anyone pushes, so a worker's first push of a name another worker
        took meets git's "fetch first", as a clone made before the claim
        does, and not the "non-fast-forward" of a clone that knew the
        branch."""
        model.store = None
        shas = {}
        clone = self.clone_of(GITHUB)
        for old in model.in_order(model.reachable()):
            record = model.commits[old]
            parents = [shas[p] for p in record.parents]
            tree = self.write_tree(clone, dict(record.tree))
            done = git(clone, "commit-tree", tree, *[a for p in parents for a in ("-p", p)], "-m", record.message,
                       actor=record.author, when=iso(record.committed_at))
            shas[old] = done.stdout.strip()
        for name, sha in sorted(model.refs.items()):
            git(clone, "push", "-q", "origin", f"{shas[sha]}:refs/heads/{name}", actor=GITHUB)
        model.commits = {}
        model.refs = {}
        model.ever_heads = frozenset(shas[s] for s in model.ever_heads)
        model.tracking = {w: {r: shas[s] for r, s in refs.items()} for w, refs in model.tracking.items()}
        model.store = self
        model.standings = {}
        self.sync(model)
        for actor in actors:
            self.clone_of(actor)
        return model

    def write_tree(self, clone, tree):
        """A tree object of the paths and texts, written into the clone's
        object database without touching its work tree."""
        index = clone / ".git" / "store-index"
        env_index = {"GIT_INDEX_FILE": str(index)}
        if index.exists():
            index.unlink()
        for path, text in sorted(tree.items()):
            blob = subprocess.run(["git", *settings_arguments(), "hash-object", "-w", "--stdin"], cwd=clone,
                                  input=text, capture_output=True, text=True, env=ENV, check=True).stdout.strip()
            subprocess.run(["git", *settings_arguments(), "update-index", "--add", "--cacheinfo", f"100644,{blob},{path}"],
                           cwd=clone, capture_output=True, text=True, env={**ENV, **env_index}, check=True)
        done = subprocess.run(["git", *settings_arguments(), "write-tree"], cwd=clone, capture_output=True, text=True,
                              env={**ENV, **env_index}, check=True)
        return done.stdout.strip()

    def refs(self):
        done = git(self.bare, "for-each-ref", "--format=%(refname)%09%(objectname)", "refs/heads/")
        found = {}
        for line in done.stdout.splitlines():
            name, sha = line.split("\t")
            found[name.removeprefix("refs/heads/")] = sha
        return found

    def sync(self, model):
        """Read the bare repository's refs into the model, with every commit
        they reach that the model lacks."""
        refs = self.refs()
        for sha in sorted(set(refs.values())):
            if sha not in model.commits:
                self.import_history(model, sha)
        model.refs = refs
        model.ever_heads = model.ever_heads | {sha for name, sha in refs.items() if name.startswith(CLAIM_PREFIX)}

    def import_history(self, model, sha):
        done = git(self.bare, "log", "--format=%H%x1f%P%x1f%ct%x1f%an%x1f%B%x1e", sha)
        for entry in done.stdout.split("\x1e"):
            if not entry.strip():
                continue
            commit, parents, when, author, message = entry.lstrip("\n").split("\x1f", 4)
            if commit in model.commits:
                continue
            minute = (int(when) - EPOCH) // 60
            model.commits[commit] = Commit(tuple(parents.split()), author, minute, tuple(sorted(self.tree(commit).items())),
                                           message.strip("\n"))

    def tree(self, sha):
        """The whole tree of a commit, as (path, text) pairs."""
        listed = git(self.bare, "ls-tree", "-r", "-z", sha).stdout
        entries = []
        for item in listed.split("\0"):
            if item:
                meta, path = item.split("\t", 1)
                entries.append((meta.split()[2], path))
        if not entries:
            return {}
        batch = subprocess.run(["git", *settings_arguments(), "cat-file", "--batch"], cwd=self.bare,
                               input="".join(f"{blob}\n" for blob, _ in entries).encode("ascii"),
                               capture_output=True, env=ENV, check=True).stdout
        found, at = {}, 0
        for blob, path in entries:
            header_end = batch.index(b"\n", at)
            size = int(batch[at:header_end].split()[2])
            found[path] = batch[header_end + 1:header_end + 1 + size].decode("utf-8")
            at = header_end + 1 + size + 1
        return found

    # --- what the model asks the store ----------------------------------

    def standing(self, ref, main=MAIN):
        head = self.refs().get(ref)
        if head is None:
            return CLEAN
        base = self.refs()[main]
        if git(self.bare, "merge-base", "--is-ancestor", base, head, check=False).returncode == 0:
            return CLEAN
        probe = git(self.bare, "merge-tree", "--write-tree", "--name-only", "--no-messages", base, head, check=False)
        if probe.returncode == 0:
            return BEHIND
        if probe.returncode == 1:
            return DIRTY
        raise GitFailed(f"git merge-tree failed: {probe.stderr.strip()}")

    def files_changed(self, ref, main=MAIN):
        refs = self.refs()
        head = refs.get(ref)
        if head is None:
            return []
        base = git(self.bare, "merge-base", refs[main], head).stdout.strip()
        return sorted(git(self.bare, "diff", "--name-only", base, head).stdout.split())

    def rename(self, old, new):
        git(self.bare, "branch", "-m", old, new)

    def delete(self, ref):
        git(self.bare, "update-ref", "-d", f"refs/heads/{ref}", check=False)

    def fetch(self, actor):
        self.fetch_refs(actor)

    # --- the verbs made with git ------------------------------------------

    def handles(self, name):
        return name in GIT_VERBS

    def act(self, model, verb, scope):
        name, actor, args = verb
        number = args[0] if args else None
        when = iso(model.now)
        if name == "claim":
            skew = args[1] if len(args) > 1 else 0
            return self.claim(model, actor, number, scope.files_of(number), iso(model.now + skew))
        if name == "claim-misnamed":
            return self.claim(model, actor, number, scope.files_of(number), when, name=args[1])
        if name == "push-by-hand":
            return self.claim(model, actor, number, scope.files_of(number), when, name=args[1], guarded=False)
        if name == "commit":
            skew = args[1] if len(args) > 1 else 0
            return self.commit(model, actor, number, scope.files_of(number), iso(model.now + skew))
        if name == "drop-claim-file":
            return self.drop_claim_file(model, actor, number, when)
        if name == "merge-main":
            return self.merge_main(model, actor, number, when)
        if name == "rebase-main":
            return self.rebase_main(model, actor, number, when)
        if name == "lease-onto":
            return self.lease_onto(model, actor, number, scope.files_of(number), when)
        if name == "lease-elsewhere":
            return self.lease_elsewhere(model, actor, number, scope.files_of(number), args[1], when)
        if name == "push-old-head":
            return self.push_old_head(model, actor, number, args[1])
        if name == "drop-commits":
            return self.drop_commits(model, actor, number)
        if name == "carry-on":
            return self.carry_on(model, actor, number, args[1], when)
        if name == "merge":
            return self.merge(model, number, when)
        if name == "merge-keep":
            return self.merge(model, number, when, keep_head=True)
        if name == "move-main":
            return self.move_main(model, args[0] if args else "README.md", when)
        raise ValueError(f"the store has no verb {name}")

    def pushed(self, model, worker, ref, command, allowed=True, guarded=True, lease=False, source=None,
               clone=None):
        """One push through the guard, then git, from the clone the command
        reaches, the worker's own unless clone names another (F21). A push
        taken fires push, and create when it made the branch; the model
        then reads the repository back."""
        # The guard judges the command from the worker's own clone, as the
        # template's hook does from the session's directory, with the
        # library's own readers of the branches and the claim files of the
        # clone each push runs in (F7's fence, F21's place).
        refused = (guard_says(command, str(self.clone_of(worker)), guard.branches, guard.claim_file_reader)
                   if guarded else None)
        if refused:
            return Outcome(False, "refused by the guard", command, refused, allowed_shape=allowed)
        created = ref not in model.refs
        before = model.refs.get(ref)
        spec = f"{source or ref}:refs/heads/{ref}"
        done = git(self.clone_of(clone or worker), "push", "-q", *(["--force-with-lease"] if lease else []), "origin",
                   spec, actor=worker, check=False)
        if done.returncode != 0:
            return Outcome(False, push_words(done), command, allowed_shape=allowed, guarded=guarded)
        self.sync(model)
        sha = model.refs[ref]
        if created:
            model.made_by(ref, worker)
        if before != sha:
            model.pushed_now(ref, before, before is None or model.is_ancestor(before, sha))
        model.tracking.setdefault(clone or worker, {})[ref] = sha
        events = ((cycle.PUSH, ref),) + (((cycle.CREATE, ref),) if created else ())
        return Outcome(True, "taken", command, allowed_shape=allowed, guarded=guarded, events=events)

    def write(self, clone, path, text):
        file = clone / path
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_text(text, encoding="utf-8")

    def claim(self, model, worker, number, files, when, name=None, guarded=True):
        ref = name or verbs.claim_ref(number)
        self.fetch_refs(worker, MAIN)
        clone = self.start_from(worker, ref, f"origin/{MAIN}")
        self.write(clone, f"{CLAIM_DIR}/{number}.md", claim_text(number, worker, files))
        git(clone, "add", "-A", CLAIM_DIR, actor=worker)
        git(clone, "commit", "-q", "-m", f"Claim #{number}", actor=worker, when=when)
        return self.pushed(model, worker, ref, f"git push origin {ref}", allowed=not oracle.reads_as_claim(ref),
                           guarded=guarded)

    def on_own_head(self, model, worker, number):
        ref = verbs.claim_ref(number)
        self.fetch_refs(worker, ref, MAIN)
        return ref, self.start_from(worker, ref, f"origin/{ref}")

    def commit(self, model, worker, number, files, when):
        ref, clone = self.on_own_head(model, worker, number)
        for path in files:
            file = clone / path
            old = file.read_text(encoding="utf-8") if file.exists() else ""
            self.write(clone, path, old + f"{worker} {model.now}\n")
        git(clone, "add", "-A", "--", *files, actor=worker)
        git(clone, "commit", "-q", "-m", f"Work on #{number}", actor=worker, when=when)
        return verbs.touch_pull(model, ref, self.pushed(model, worker, ref, f"git push origin {ref}"))

    def drop_claim_file(self, model, worker, number, when):
        ref, clone = self.on_own_head(model, worker, number)
        git(clone, "rm", "-q", "--ignore-unmatch", f"{CLAIM_DIR}/{number}.md", actor=worker)
        git(clone, "commit", "-q", "--allow-empty", "-m", f"Finish #{number}", actor=worker, when=when)
        return verbs.touch_pull(model, ref, self.pushed(model, worker, ref, f"git push origin {ref}"))

    def unmerged(self, clone, actor):
        done = git(clone, "diff", "--name-only", "--diff-filter=U", actor=actor)
        return sorted(set(done.stdout.split()))

    def side(self, clone, stage, path, actor):
        done = git(clone, "show", f":{stage}:{path}", actor=actor, check=False)
        return done.stdout if done.returncode == 0 else ""

    def resolve(self, clone, actor, first=2, second=3):
        """Resolve every conflict as the memory verb builds its tree: both
        texts, the stage named first before the other."""
        for path in self.unmerged(clone, actor):
            self.write(clone, path, self.side(clone, first, path, actor) + self.side(clone, second, path, actor))
            git(clone, "add", "--", path, actor=actor)

    def merge_main(self, model, worker, number, when):
        ref, clone = self.on_own_head(model, worker, number)
        attempt = git(clone, "merge", "--no-edit", "-m", f"Merge {MAIN} into {ref}", f"origin/{MAIN}", actor=worker,
                      when=when, check=False)
        if attempt.returncode != 0:
            self.resolve(clone, worker)
            git(clone, "commit", "-q", "--no-edit", actor=worker, when=when)
        return verbs.touch_pull(model, ref, self.pushed(model, worker, ref, f"git push origin {ref}"))

    def rebase_main(self, model, worker, number, when):
        """Fetch, replay the branch's own commits onto main, each conflict
        resolved for the branch's commit as the memory verb takes its tree,
        and push with a lease."""
        model.fetch(worker)
        ref = verbs.claim_ref(number)
        clone = self.start_from(worker, ref, f"origin/{ref}")
        attempt = git(clone, "rebase", f"origin/{MAIN}", actor=worker, when=when, check=False)
        for _ in range(MOST_CONFLICTS):
            if attempt.returncode == 0 or "No rebase in progress" in attempt.stderr:
                break
            for path in self.unmerged(clone, worker):
                theirs = self.side(clone, 3, path, worker)
                if theirs:
                    self.write(clone, path, theirs)
                    git(clone, "add", "--", path, actor=worker)
                else:
                    git(clone, "rm", "-q", "--", path, actor=worker)
            attempt = git(clone, "rebase", "--continue", actor=worker, when=when, check=False)
        else:
            raise GitFailed(f"the rebase of {ref} did not finish: {attempt.stderr.strip()}")
        command = f"git push --force-with-lease origin {ref}"
        return verbs.touch_pull(model, ref, self.pushed(model, worker, ref, command, lease=True))

    def lease_onto(self, model, worker, number, files, when):
        model.fetch(worker)
        ref = verbs.claim_ref(number)
        clone = self.start_from(worker, ref, f"origin/{MAIN}")
        self.write(clone, f"{CLAIM_DIR}/{number}.md", claim_text(number, worker, files))
        git(clone, "add", "-A", CLAIM_DIR, actor=worker)
        git(clone, "commit", "-q", "-m", f"Claim #{number}", actor=worker, when=when)
        return self.pushed(model, worker, ref, f"git push --force-with-lease origin {ref}", lease=True, allowed=False)

    def lease_elsewhere(self, model, worker, number, files, how, when):
        """verbs.lease_elsewhere with git: the clone verbs.leasing_clone
        names, the worker's second made the first time it is fetched into,
        fetches, makes the claim commit and is pushed from with a lease, by
        a command run from the worker's own clone, which the guard reads as
        the hook does, a directory that is not there being one a cd does not
        reach, and which names the clones by where they are (F21)."""
        pusher = verbs.leasing_clone(worker, how)
        model.fetch(pusher)
        ref = verbs.claim_ref(number)
        clone = self.start_from(pusher, ref, f"origin/{MAIN}")
        self.write(clone, f"{CLAIM_DIR}/{number}.md", claim_text(number, worker, files))
        git(clone, "add", "-A", CLAIM_DIR, actor=worker)
        git(clone, "commit", "-q", "-m", f"Claim #{number}", actor=worker, when=when)
        command = verbs.leasing_command(worker, how, ref, root=str(self.root / "clones"))
        return self.pushed(model, worker, ref, command, lease=True, allowed=False, clone=pusher)

    def drop_commits(self, model, worker, number):
        """Fetch, reset the clone to the claim commit, which the model read
        from the repository (verbs.claim_commit), and push with a lease."""
        model.fetch(worker)
        ref, sha = verbs.claim_ref(number), verbs.claim_commit(model, number)
        if sha == model.refs[ref]:
            return Outcome(True, "nothing to drop")
        clone = self.start_from(worker, ref, f"origin/{ref}")
        git(clone, "reset", "-q", "--hard", sha, actor=worker)
        command = f"git push --force-with-lease origin {ref}"
        return verbs.touch_pull(model, ref, self.pushed(model, worker, ref, command, lease=True))

    def push_old_head(self, model, worker, number, released_ref):
        ref = verbs.claim_ref(number)
        self.fetch_refs(worker, released_ref)
        return self.pushed(model, worker, ref, f"git push origin {released_ref}:{ref}",
                           source=f"refs/remotes/origin/{released_ref}")

    def carry_on(self, model, worker, number, released_ref, when):
        ref = verbs.claim_ref(number)
        self.fetch_refs(worker, ref, released_ref, MAIN)
        clone = self.start_from(worker, ref, f"origin/{ref}")
        head = model.refs[ref]
        claim_path = f"{CLAIM_DIR}/{number}.md"
        own = git(clone, "rev-list", "--reverse", f"origin/{MAIN}..origin/{released_ref}", actor=worker).stdout.split()
        for sha in own:
            git(clone, "cherry-pick", "-n", sha, actor=worker, check=False)
            for path in self.unmerged(clone, worker):
                if path == claim_path:
                    git(clone, "checkout", "HEAD", "--", path, actor=worker)
                else:
                    self.write(clone, path, self.side(clone, 3, path, worker))
                    git(clone, "add", "--", path, actor=worker)
            git(clone, "cherry-pick", "--quit", actor=worker, check=False)
            self.keep_own(clone, worker, claim_path)
            if git(clone, "diff", "--cached", "--quiet", actor=worker, check=False).returncode == 0:
                continue
            message = git(clone, "log", "-1", "--format=%B", sha, actor=worker).stdout.strip("\n")
            git(clone, "commit", "-q", "-m", message, actor=worker, when=when)
        if git(clone, "rev-parse", "HEAD", actor=worker).stdout.strip() == head:
            return Outcome(True, "nothing to carry on")
        return verbs.touch_pull(model, ref, self.pushed(model, worker, ref, f"git push origin {ref}"))

    def keep_own(self, clone, worker, path):
        """The path as the branch's head has it, whatever a pick staged for
        it: restored when the head holds it, dropped when it does not."""
        restored = git(clone, "checkout", "HEAD", "--", path, actor=worker, check=False)
        if restored.returncode != 0:
            git(clone, "rm", "-q", "--cached", "--force", "--ignore-unmatch", "--", path, actor=worker)
            (clone / path).unlink(missing_ok=True)

    def merge(self, model, number, when, keep_head=False):
        """The owner merges a pull request on GitHub: a merge commit on
        main, the branch's text before main's where both changed a path,
        the issue closed and the head deleted, unless the repository keeps
        head branches (keep_head, F22); the four events at once, or three
        with the head kept (verbs.merge)."""
        pull = model.pulls[number]
        self.fetch_refs(GITHUB, MAIN, pull.head)
        clone = self.start_from(GITHUB, MAIN, f"origin/{MAIN}")
        attempt = git(clone, "merge", "--no-ff", "--no-edit", "-m", f"Merge #{number}", f"origin/{pull.head}",
                      actor=OWNER, when=when, check=False)
        if attempt.returncode != 0:
            self.resolve(clone, OWNER, first=3, second=2)
            git(clone, "commit", "-q", "--no-edit", actor=OWNER, when=when)
        git(clone, "push", "-q", "origin", f"{MAIN}:refs/heads/{MAIN}", actor=GITHUB)
        pull.state, pull.merged_at = "merged", model.now
        verbs.apply_pull_change(model, pull)
        verbs.close_issue(model, oracle.claim_number(pull.head))
        if keep_head:
            self.sync(model)
            return Outcome(True, "merged, the head kept", events=verbs.MERGED_EVENTS[:3])
        model.delete_ref(pull.head)
        self.sync(model)
        return Outcome(True, "merged", events=verbs.MERGED_EVENTS)

    def move_main(self, model, path, when):
        self.fetch_refs(GITHUB, MAIN)
        clone = self.start_from(GITHUB, MAIN, f"origin/{MAIN}")
        file = clone / path
        old = file.read_text(encoding="utf-8") if file.exists() else ""
        self.write(clone, path, old + f"{OWNER} {model.now}\n")
        git(clone, "add", "-A", "--", path, actor=OWNER)
        git(clone, "commit", "-q", "-m", "Elsewhere", actor=OWNER, when=when)
        git(clone, "push", "-q", "origin", f"{MAIN}:refs/heads/{MAIN}", actor=GITHUB)
        self.sync(model)
        return Outcome(True, "main moved", events=((cycle.PUSH, MAIN),))
