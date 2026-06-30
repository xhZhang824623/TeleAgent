"""test_fs_browse.py – 远程目录浏览：list_directory 与 apply_pending_fs_requests 单测。"""

import os
import tempfile
import unittest

try:
    from LocalBroker.broker_worker import list_directory, apply_pending_fs_requests
except ModuleNotFoundError:
    from broker_worker import list_directory, apply_pending_fs_requests


class ListDirectoryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = self.tmp.name
        os.mkdir(os.path.join(root, "alpha"))
        os.mkdir(os.path.join(root, "beta"))
        os.mkdir(os.path.join(root, ".hidden"))  # 隐藏目录应被跳过
        with open(os.path.join(root, "file.txt"), "w") as f:
            f.write("x")  # 文件应被跳过（只列目录）
        self.root = root

    def tearDown(self):
        self.tmp.cleanup()

    def test_lists_only_visible_subdirs_sorted(self):
        # 默认拒绝策略：必须提供受限根。用 root=父目录，使 self.root 仍可返回父级。
        listed, parent, entries = list_directory(self.root, root=os.path.dirname(self.root))
        self.assertEqual(listed, os.path.realpath(self.root))
        self.assertEqual(parent, os.path.dirname(os.path.realpath(self.root)))
        self.assertEqual([e["name"] for e in entries], ["alpha", "beta"])
        self.assertTrue(all(e["is_dir"] for e in entries))

    def test_empty_path_defaults_to_root(self):
        # 空路径起点为受限根（confine-by-default）。
        listed, _parent, _entries = list_directory("", root=os.path.expanduser("~"))
        self.assertEqual(listed, os.path.realpath(os.path.expanduser("~")))

    def test_empty_root_file_browse_denied(self):
        # 下载浏览器（include_files=True 暴露文件名/大小）未提供受限根：默认拒绝。
        with self.assertRaises(ValueError):
            list_directory(self.root, include_files=True)

    def test_empty_root_folder_browse_allowed(self):
        # 选工作目录（仅列文件夹、不暴露文件内容）允许无 root 全盘导航——这是选目录的固有需求。
        listed, _parent, entries = list_directory(self.root)
        self.assertEqual(listed, os.path.realpath(self.root))
        self.assertEqual([e["name"] for e in entries], ["alpha", "beta"])
        self.assertTrue(all(e["is_dir"] for e in entries))

    def test_nonexistent_path_raises(self):
        with self.assertRaises(ValueError):
            list_directory(os.path.join(self.root, "nope", "deep"), root=self.root)

    def test_file_path_raises(self):
        with self.assertRaises(ValueError):
            list_directory(os.path.join(self.root, "file.txt"), root=self.root)


class ApplyPendingFsRequestsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        os.mkdir(os.path.join(self.tmp.name, "child"))

    def tearDown(self):
        self.tmp.cleanup()

    def test_done_ack_carries_entries(self):
        acks = []
        apply_pending_fs_requests(
            base="http://x", client_id="c1", token="t",
            get_pending_fs_requests_fn=lambda **kw: [{"id": "r1", "path": self.tmp.name, "root_path": self.tmp.name}],
            ack_fs_request_fn=lambda rid, **kw: acks.append((rid, kw)) or {},
        )
        self.assertEqual(len(acks), 1)
        rid, kw = acks[0]
        self.assertEqual(rid, "r1")
        self.assertEqual(kw["status"], "done")
        self.assertEqual([e["name"] for e in kw["entries"]], ["child"])

    def test_failed_path_acks_failure(self):
        acks = []
        apply_pending_fs_requests(
            base="http://x", client_id="c1", token="t",
            get_pending_fs_requests_fn=lambda **kw: [{"id": "r2", "path": "/no/such/dir/zzz"}],
            ack_fs_request_fn=lambda rid, **kw: acks.append((rid, kw)) or {},
        )
        self.assertEqual(len(acks), 1)
        self.assertEqual(acks[0][1]["status"], "failed")
        self.assertTrue(acks[0][1]["error"])


if __name__ == "__main__":
    unittest.main()
