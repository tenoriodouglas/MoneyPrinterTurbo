import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from growth import cleanup as cleanup_module
from growth.produce import LEDGER_PATH, REPO_ROOT, STORAGE_DIR

TASK_ID = "11111111-2222-3333-4444-555555555555"
OTHER_TASK_ID = "99999999-8888-7777-6666-555555555555"

REAL_STORAGE = REPO_ROOT / "storage"


def _listing(root: Path) -> set[tuple[str, int]]:
    """Every path under `root` with its own size, for proving nothing moved."""
    if not root.is_dir():
        return set()
    return {
        (str(child.relative_to(root)), child.lstat().st_size)
        for child in root.rglob("*")
    }


def _git_status() -> str | None:
    """`git status --short`, or None when git cannot answer."""
    try:
        done = subprocess.run(
            ["git", "status", "--short"],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            timeout=60,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return done.stdout if done.returncode == 0 else None


# Taken before any test runs, so the guards at the bottom compare against the
# state this module was imported into rather than against a clean checkout.
_STORAGE_AT_IMPORT = _listing(REAL_STORAGE)
_GIT_AT_IMPORT = _git_status()


def _inside(path: Path, root: Path) -> bool:
    return path == root or root in path.parents


class CleanupCase(unittest.TestCase):
    """Points the module at a throwaway tree, with somewhere outside it.

    Both REMOVABLE_ROOTS and TASKS_DIR are patched. purge_task builds its path
    from TASKS_DIR, so patching the roots alone would leave it aimed at the
    real storage/tasks: every test below would then pass by refusal, proving
    nothing, while a careless one could delete a real render.
    """

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="test-cleanup-")).resolve()
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.tasks = self.tmp / "tasks"
        self.delivered = self.tmp / "delivered"
        # Inside the temp tree so cleanup is automatic, outside both roots so
        # the module must refuse it.
        self.outside = self.tmp / "outside"
        for folder in (self.tasks, self.delivered, self.outside):
            folder.mkdir()
        patcher = patch.multiple(
            cleanup_module,
            TASKS_DIR=self.tasks,
            REMOVABLE_ROOTS=(self.tasks, self.delivered),
        )
        patcher.start()
        self.addCleanup(patcher.stop)

    def _task_dir(self, sizes: dict[str, int], task_id: str = TASK_ID) -> Path:
        """A render's working directory, holding files of the given sizes."""
        folder = self.tasks / task_id
        folder.mkdir()
        for name, size in sizes.items():
            (folder / name).write_bytes(b"x" * size)
        return folder

    def _delivered_file(self, name: str, size: int) -> Path:
        target = self.delivered / name
        target.write_bytes(b"d" * size)
        return target

    def _victim(self, name: str = "victim.mp4", size: int = 100_000) -> Path:
        """A file outside the roots that no test may be allowed to destroy."""
        target = self.outside / name
        target.write_bytes(b"v" * size)
        return target


class TestByteCountIsWhatWasFreed(CleanupCase):
    """The caller reports this number to the owner in MB. Too low and a full
    disk looks like it was cleaned; too high and the owner is told space was
    reclaimed that never was. Either way the chat message is a lie."""

    def test_plain_files_are_counted_byte_for_byte(self):
        first = self._delivered_file("a.mp4", 1234)
        second = self._delivered_file("b.mp4", 77)
        freed = cleanup_module.purge_delivered(
            {"files": [str(first), str(second)], "task_id": ""}
        )
        self.assertEqual(freed, 1234 + 77)

    def test_a_task_directory_counts_its_tree_and_nothing_else(self):
        """Every entry the delete actually removed, directory inodes included,
        which is what `du -sb` would have reported for the same tree."""
        folder = self._task_dir({"final-1.mp4": 2000, "audio.mp3": 500})
        nested = folder / "scenes"
        nested.mkdir()
        (nested / "1.png").write_bytes(b"p" * 300)
        expected = (
            folder.lstat().st_size
            + nested.lstat().st_size
            + 2000
            + 500
            + 300
        )
        self.assertEqual(cleanup_module.purge_task(TASK_ID), expected)

    def test_a_symlinks_target_is_not_counted_as_freed(self):
        """A link in the working directory is removed as a link. Counting the
        file it points at would report a 100 KB saving from deleting 30 bytes,
        and the disk would not budge."""
        victim = self._victim(size=100_000)
        folder = self._task_dir({"final-1.mp4": 400})
        link = folder / "borrowed.mp4"
        link.symlink_to(victim)
        expected = (
            folder.lstat().st_size + 400 + link.lstat().st_size
        )
        self.assertEqual(cleanup_module.purge_task(TASK_ID), expected)
        self.assertTrue(victim.is_file())

    def test_a_second_purge_adds_nothing_to_the_total(self):
        """A retry must not let the same bytes be reported twice."""
        self._task_dir({"final-1.mp4": 900})
        first = cleanup_module.purge_task(TASK_ID)
        self.assertGreater(first, 0)
        self.assertEqual(cleanup_module.purge_task(TASK_ID), 0)


class TestDeliveredRecordsThatPredateThisFeature(CleanupCase):
    """The ledger is append-only and rows written before this module existed
    are still read by the bot. A row it cannot handle raises inside the purge
    and takes the chat down, long after the video was delivered."""

    def test_rows_that_name_nothing_removable_return_zero(self):
        rows = {
            "no keys at all": {},
            "empty file list": {"files": []},
            "nulls where the keys should be": {"files": None, "task_id": None},
            "only a subject": {"subject": "an old row", "published": True},
            "a task that was already cleaned": {"files": [], "task_id": TASK_ID},
            "files stored as one string": {"files": str(self.delivered / "a.mp4")},
        }
        for label, row in rows.items():
            with self.subTest(label):
                self.assertEqual(cleanup_module.purge_delivered(row), 0)

    def test_a_missing_task_directory_is_not_an_error(self):
        """Rows outlive their renders: the working directory may have been
        swept by hand, or by an earlier run of this very module."""
        self.assertEqual(cleanup_module.purge_task(OTHER_TASK_ID), 0)

    def test_a_row_still_purges_what_it_does_name(self):
        """The tolerance above is worthless if it also swallows real work."""
        kept = self._delivered_file("a.mp4", 120)
        self._task_dir({"final-1.mp4": 30})
        freed = cleanup_module.purge_delivered(
            {"files": [str(kept)], "task_id": TASK_ID}
        )
        self.assertGreater(freed, 0)
        self.assertFalse(kept.exists())
        self.assertFalse((self.tasks / TASK_ID).exists())


class TestDiskReportOnlyReads(CleanupCase):
    def test_it_leaves_the_tree_exactly_as_it_found_it(self):
        """It is the call a caller makes to decide whether to purge, so it runs
        on trees nobody has agreed to delete yet."""
        self._task_dir({"final-1.mp4": 400, "audio.mp3": 50})
        self._delivered_file("a.mp4", 120)
        before = _listing(self.tmp)
        cleanup_module.disk_report()
        self.assertEqual(_listing(self.tmp), before)

    def test_it_reports_a_figure_per_root(self):
        self._task_dir({"final-1.mp4": 400})
        report = cleanup_module.disk_report()
        self.assertEqual(set(report), {str(self.tasks), str(self.delivered)})
        self.assertGreater(report[str(self.tasks)], 400)

    def test_a_root_that_does_not_exist_reports_zero(self):
        """storage/growth/out only appears after the first batch is filed."""
        shutil.rmtree(self.delivered)
        self.assertEqual(cleanup_module.disk_report()[str(self.delivered)], 0)


class TestIdempotentDeletion(CleanupCase):
    """A purge can die halfway - the box is out of disk, or the bot restarts -
    so the caller retries. A second call must finish the job quietly, not
    raise on what the first call already took."""

    def test_purging_the_same_task_twice_does_not_raise(self):
        self._task_dir({"final-1.mp4": 900})
        cleanup_module.purge_task(TASK_ID)
        self.assertEqual(cleanup_module.purge_task(TASK_ID), 0)

    def test_purging_the_same_record_twice_does_not_raise(self):
        delivered = self._delivered_file("a.mp4", 120)
        self._task_dir({"final-1.mp4": 300})
        record = {"files": [str(delivered)], "task_id": TASK_ID}
        self.assertGreater(cleanup_module.purge_delivered(record), 0)
        self.assertEqual(cleanup_module.purge_delivered(record), 0)

    def test_a_retry_finishes_what_a_partial_run_left(self):
        """The real retry: some of the row went, the rest did not."""
        gone = self._delivered_file("a.mp4", 120)
        remaining = self._delivered_file("b.mp4", 340)
        record = {"files": [str(gone), str(remaining)], "task_id": ""}
        gone.unlink()
        self.assertEqual(cleanup_module.purge_delivered(record), 340)
        self.assertFalse(remaining.exists())


class TestPathsOutsideTheRootsAreRefused(CleanupCase):
    """REMOVABLE_ROOTS is the whole safety boundary. A ledger row is a line of
    JSON on disk that anything may have written, so `files` is input, not fact."""

    def test_an_absolute_path_elsewhere_is_left_alone(self):
        victim = self._victim("elsewhere.mp4", size=5000)
        self.assertEqual(
            cleanup_module.purge_delivered({"files": [str(victim)]}), 0
        )
        self.assertTrue(victim.is_file())
        self.assertEqual(victim.stat().st_size, 5000)

    def test_a_tampered_file_list_removes_nothing(self):
        """Each of these either leaves the roots or names a root itself. The
        decoys are shaped like real repo paths but live in the temp tree, so a
        regression here cannot reach the checkout."""
        decoys = self.outside / "repo"
        (decoys / "storage" / "growth").mkdir(parents=True)
        config = decoys / "config.toml"
        config.write_text("api keys live here")
        ledger = decoys / "storage" / "growth" / "ledger.jsonl"
        ledger.write_text('{"published": true}\n')
        hostile = {
            "a config file": str(config),
            "the ledger itself": str(ledger),
            "an escape with ..": str(self.delivered / ".." / "outside" / "repo"),
            "the delivered root": str(self.delivered),
            "the tasks root": str(self.tasks),
            "a relative path": "../outside/repo/config.toml",
            "an empty string": "",
            "a bare dot": ".",
        }
        for label, entry in hostile.items():
            with self.subTest(label):
                self.assertEqual(
                    cleanup_module.purge_delivered({"files": [entry]}), 0
                )
        self.assertTrue(config.is_file())
        self.assertTrue(ledger.is_file())
        self.assertTrue(self.delivered.is_dir())
        self.assertTrue(self.tasks.is_dir())

    def test_one_bad_entry_does_not_stop_the_good_ones(self):
        """A row mixing a refused path with a real one must still reclaim the
        real one, or a single bad row pins that disk forever."""
        victim = self._victim("elsewhere.mp4", size=5000)
        real = self._delivered_file("a.mp4", 250)
        freed = cleanup_module.purge_delivered(
            {"files": [str(victim), str(real)], "task_id": ""}
        )
        self.assertEqual(freed, 250)
        self.assertTrue(victim.is_file())
        self.assertFalse(real.exists())


class TestRemovableRootsAreNarrow(unittest.TestCase):
    """Read against the real, unpatched constants. Widening a root is a one
    line change that no other test here would notice, because every other test
    replaces them."""

    def test_every_root_is_a_directory_under_storage(self):
        self.assertTrue(cleanup_module.REMOVABLE_ROOTS)
        for root in cleanup_module.REMOVABLE_ROOTS:
            with self.subTest(str(root)):
                self.assertTrue(_inside(root, REAL_STORAGE.resolve()))
                self.assertNotEqual(root, REAL_STORAGE.resolve())

    def test_nothing_worth_keeping_sits_inside_a_root(self):
        """These are the files a purge must never be able to name: the ledger
        is the only record of what was published, and the plans and config are
        not reproducible from it."""
        precious = [
            LEDGER_PATH,
            STORAGE_DIR / "plans",
            STORAGE_DIR / "history",
            REPO_ROOT / "config.toml",
            REPO_ROOT / "niches",
            REPO_ROOT,
        ]
        for path in precious:
            for root in cleanup_module.REMOVABLE_ROOTS:
                with self.subTest(path=str(path), root=str(root)):
                    self.assertFalse(_inside(path.resolve(), root))


class TestSymlinkOutOfTheRootsIsNotFollowed(CleanupCase):
    """The classic way a cleanup routine eats something it should not: the
    path it was handed is inside the tree, and what it resolves to is not."""

    def test_a_link_inside_a_task_directory_does_not_take_its_target(self):
        """rmtree walks the working directory. If it descended into a link,
        this purge would delete the thing on the other end."""
        victim = self._victim(size=100_000)
        folder = self._task_dir({"final-1.mp4": 400})
        (folder / "borrowed.mp4").symlink_to(victim)
        cleanup_module.purge_task(TASK_ID)
        self.assertFalse(folder.exists())
        self.assertTrue(victim.is_file())
        self.assertEqual(victim.read_bytes(), b"v" * 100_000)

    def test_a_link_named_by_a_record_does_not_take_its_target(self):
        victim = self._victim(size=100_000)
        link = self.delivered / "delivered.mp4"
        link.symlink_to(victim)
        cleanup_module.purge_delivered({"files": [str(link)], "task_id": ""})
        self.assertTrue(victim.is_file())
        self.assertEqual(victim.read_bytes(), b"v" * 100_000)

    def test_a_task_directory_that_is_itself_a_link_is_refused(self):
        """A whole directory moved off the boot volume and linked back in is
        an ordinary thing to do on a box that is out of disk."""
        elsewhere = self.outside / "moved-task"
        elsewhere.mkdir()
        (elsewhere / "final-1.mp4").write_bytes(b"k" * 900)
        (self.tasks / TASK_ID).symlink_to(elsewhere)
        self.assertEqual(cleanup_module.purge_task(TASK_ID), 0)
        self.assertTrue(elsewhere.is_dir())
        self.assertTrue((elsewhere / "final-1.mp4").is_file())

    def test_a_link_pointing_back_inside_the_roots_is_still_removable(self):
        """The rule is where the path lands, not whether a link was involved;
        refusing every link would leave real working directories behind."""
        real = self.tasks / TASK_ID
        real.mkdir()
        (real / "final-1.mp4").write_bytes(b"r" * 700)
        link = self.delivered / "shortcut.mp4"
        link.symlink_to(real / "final-1.mp4")
        self.assertEqual(
            cleanup_module.purge_delivered({"files": [str(link)], "task_id": ""}),
            700,
        )
        self.assertFalse((real / "final-1.mp4").exists())


class TestTaskIdIsNotAPath(CleanupCase):
    """The id is joined onto storage/tasks and the result is handed to rmtree.
    It arrives from a ledger row, so it decides which directory is destroyed."""

    def test_an_id_that_is_not_a_bare_uuid_removes_nothing(self):
        sentinel = self._task_dir({"final-1.mp4": 400})
        keep = self.tmp / "keep.txt"
        keep.write_text("not under any root")
        hostile = [
            "..",
            "../..",
            "../../../etc",
            "a/b",
            f"{TASK_ID}/..",
            f"/{TASK_ID}",
            f"{TASK_ID}/../{TASK_ID}",
            "",
            "   ",
            None,
            ".",
            "*",
            f"{TASK_ID}\n",
            f"{TASK_ID}\x00",
            f" {TASK_ID} extra",
            TASK_ID.replace("-", ""),
            "{" + TASK_ID + "}",
            f"urn:uuid:{TASK_ID}",
        ]
        for task_id in hostile:
            with self.subTest(task_id=repr(task_id)):
                self.assertEqual(cleanup_module.purge_task(task_id), 0)
        self.assertTrue(sentinel.is_dir())
        self.assertTrue((sentinel / "final-1.mp4").is_file())
        self.assertTrue(keep.is_file())
        self.assertTrue(self.tasks.is_dir())

    def test_a_real_id_still_works(self):
        """The refusals above prove nothing if the rule also blocks the ids the
        engine actually generates."""
        from app.utils.utils import get_uuid

        for task_id in (get_uuid(), TASK_ID, TASK_ID.upper()):
            with self.subTest(task_id):
                self._task_dir({"final-1.mp4": 100}, task_id=task_id)
                self.assertGreater(cleanup_module.purge_task(task_id), 0)
                self.assertFalse((self.tasks / task_id).exists())


class TestTheRepoIsUntouched(unittest.TestCase):
    """The backstop for everything above. These tests delete real files, and a
    patch that silently failed to take would point them at the checkout."""

    def test_nothing_under_the_real_storage_directory_went_missing(self):
        """storage/ is in .gitignore, so git status cannot see a render that
        was deleted here. Only this comparison can."""
        missing = _STORAGE_AT_IMPORT - _listing(REAL_STORAGE)
        self.assertEqual(missing, set())

    def test_git_status_is_what_it_was_at_import(self):
        now = _git_status()
        if _GIT_AT_IMPORT is None or now is None:
            self.skipTest("git could not report the working tree")
        self.assertEqual(now, _GIT_AT_IMPORT)

    def test_no_test_pointed_the_module_at_the_real_storage(self):
        """setUp patches the roots; addCleanup must have put them back, or the
        next module to import cleanup inherits a temp tree that no longer
        exists - and a purge against a missing root fails open, not closed."""
        for root in cleanup_module.REMOVABLE_ROOTS:
            with self.subTest(str(root)):
                self.assertTrue(_inside(root, REAL_STORAGE.resolve()))
        self.assertTrue(_inside(cleanup_module.TASKS_DIR, REAL_STORAGE.resolve()))


if __name__ == "__main__":
    unittest.main()
