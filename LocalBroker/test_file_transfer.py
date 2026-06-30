"""test_file_transfer.py – 远程文件下载：读文件 base64、上传网关、include_files 列文件。"""

import base64
import os
import tempfile
import unittest

try:
    from LocalBroker.broker_worker import (
        read_file_b64, apply_pending_file_transfers, list_directory,
    )
except ModuleNotFoundError:
    from broker_worker import read_file_b64, apply_pending_file_transfers, list_directory


class ReadFileB64Tests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.fpath = os.path.join(self.tmp.name, "hello.txt")
        with open(self.fpath, "wb") as f:
            f.write(b"hello remote")

    def tearDown(self):
        self.tmp.cleanup()

    def test_reads_and_encodes(self):
        # 默认拒绝策略：必须提供受限根（这里为该文件所在临时目录）。
        name, ctype, b64, size = read_file_b64(self.fpath, root=self.tmp.name)
        self.assertEqual(name, "hello.txt")
        self.assertEqual(base64.b64decode(b64), b"hello remote")
        self.assertEqual(size, len("hello remote"))
        self.assertTrue(ctype)

    def test_empty_root_denied(self):
        # 未提供受限根：默认拒绝读取任意文件。
        with self.assertRaises(ValueError):
            read_file_b64(self.fpath)

    def test_directory_rejected(self):
        with self.assertRaises(ValueError):
            read_file_b64(self.tmp.name, root=self.tmp.name)

    def test_missing_rejected(self):
        with self.assertRaises(ValueError):
            read_file_b64(os.path.join(self.tmp.name, "nope"), root=self.tmp.name)

    def test_oversize_rejected(self):
        with self.assertRaises(ValueError):
            read_file_b64(self.fpath, max_bytes=3, root=self.tmp.name)


class ListDirectoryFilesTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        os.mkdir(os.path.join(self.tmp.name, "sub"))
        with open(os.path.join(self.tmp.name, "a.txt"), "wb") as f:
            f.write(b"xx")

    def tearDown(self):
        self.tmp.cleanup()

    def test_dirs_only_by_default(self):
        _lp, _p, entries = list_directory(self.tmp.name, root=self.tmp.name)
        self.assertEqual([e["name"] for e in entries], ["sub"])

    def test_include_files_lists_files_with_size_dirs_first(self):
        _lp, _p, entries = list_directory(self.tmp.name, include_files=True, root=self.tmp.name)
        self.assertEqual([e["name"] for e in entries], ["sub", "a.txt"])  # 目录在前
        f = next(e for e in entries if e["name"] == "a.txt")
        self.assertFalse(f["is_dir"])
        self.assertEqual(f["size"], 2)


class ApplyPendingFileTransfersTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.fpath = os.path.join(self.tmp.name, "report.pdf")
        with open(self.fpath, "wb") as f:
            f.write(b"PDFDATA")

    def tearDown(self):
        self.tmp.cleanup()

    def test_uploads_file(self):
        uploads = []
        apply_pending_file_transfers(
            base="http://x", client_id="c1", token="t",
            get_pending_file_transfers_fn=lambda **kw: [{"id": "t1", "source_path": self.fpath, "root_path": self.tmp.name}],
            upload_file_transfer_fn=lambda tid, **kw: uploads.append((tid, kw)) or {},
            fail_file_transfer_fn=lambda tid, **kw: None,
        )
        self.assertEqual(len(uploads), 1)
        tid, kw = uploads[0]
        self.assertEqual(tid, "t1")
        self.assertEqual(kw["filename"], "report.pdf")
        self.assertEqual(base64.b64decode(kw["content_b64"]), b"PDFDATA")

    def test_unreadable_reports_failure(self):
        fails = []
        apply_pending_file_transfers(
            base="http://x", client_id="c1", token="t",
            get_pending_file_transfers_fn=lambda **kw: [{"id": "t2", "source_path": "/no/such/file"}],
            upload_file_transfer_fn=lambda tid, **kw: (_ for _ in ()).throw(AssertionError("should not upload")),
            fail_file_transfer_fn=lambda tid, **kw: fails.append((tid, kw)) or {},
        )
        self.assertEqual(len(fails), 1)
        self.assertEqual(fails[0][0], "t2")
        self.assertTrue(fails[0][1]["error"])


if __name__ == "__main__":
    unittest.main()


class TeleagentSendTests(unittest.TestCase):
    def setUp(self):
        try:
            from LocalBroker.teleagent_send import send_file
        except ModuleNotFoundError:
            from teleagent_send import send_file
        self.send_file = send_file
        self.tmp = tempfile.TemporaryDirectory()
        self.fpath = os.path.join(self.tmp.name, "out.txt")
        with open(self.fpath, "wb") as f:
            f.write(b"agent output")

    def tearDown(self):
        self.tmp.cleanup()

    def test_send_creates_and_uploads(self):
        calls = {}

        def _create(cid, path, **kw):
            calls["create"] = (cid, path, kw)
            return {"id": "tt"}

        def _upload(tid, **kw):
            calls["upload"] = (tid, kw)
            return {"status": "ready"}

        res = self.send_file(
            self.fpath, base="http://x", token="t", client_id="c1", conversation_id="conv1",
            root=self.tmp.name, create_fn=_create, upload_fn=_upload,
        )
        self.assertEqual(res["status"], "ready")
        self.assertEqual(res["filename"], "out.txt")
        # create 带上 agent_initiated + conversation_id
        self.assertEqual(calls["create"][0], "c1")
        self.assertTrue(calls["create"][2]["agent_initiated"])
        self.assertEqual(calls["create"][2]["conversation_id"], "conv1")
        self.assertEqual(calls["upload"][0], "tt")
        self.assertEqual(base64.b64decode(calls["upload"][1]["content_b64"]), b"agent output")

    def test_missing_context_errors(self):
        with self.assertRaises(RuntimeError):
            self.send_file(self.fpath, base="", token=None, client_id="",
                           create_fn=lambda *a, **k: {}, upload_fn=lambda *a, **k: {})

    def test_missing_root_errors(self):
        # 无受限根（未注入 TELEAGENT_SESSION_ROOT）：禁止外发任意文件。
        import os as _os
        self._orig_root = _os.environ.pop("TELEAGENT_SESSION_ROOT", None)
        try:
            with self.assertRaises(RuntimeError):
                self.send_file(self.fpath, base="http://x", token="t", client_id="c1",
                               create_fn=lambda *a, **k: {"id": "x"}, upload_fn=lambda *a, **k: {})
        finally:
            if self._orig_root is not None:
                _os.environ["TELEAGENT_SESSION_ROOT"] = self._orig_root

    def test_escape_outside_root_rejected(self):
        # 受限根之外的路径（如 /etc/hostname）禁止外发。
        with self.assertRaises(Exception):
            self.send_file("/etc/hostname", base="http://x", token="t", client_id="c1",
                           root=self.tmp.name,
                           create_fn=lambda *a, **k: {"id": "x"}, upload_fn=lambda *a, **k: {})


class ConfinementTests(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp()
        self.sub = os.path.join(self.root, "proj")
        os.mkdir(self.sub)
        with open(os.path.join(self.sub, "f.txt"), "w") as f:
            f.write("hi")

    def tearDown(self):
        import shutil
        shutil.rmtree(self.root, ignore_errors=True)

    def test_list_within_root_ok_and_no_parent_at_root(self):
        _lp, parent_root, _e = list_directory(self.root, include_files=True, root=self.root)
        self.assertIsNone(parent_root)  # 根处不能向上
        _lp2, parent_sub, _e2 = list_directory(self.sub, include_files=True, root=self.root)
        self.assertIsNotNone(parent_sub)  # 子目录可回到 root

    def test_list_escape_rejected(self):
        with self.assertRaises(ValueError):
            list_directory(os.path.dirname(self.root), root=self.root)

    def test_read_within_root_ok(self):
        name, _ct, _b, _s = read_file_b64(os.path.join(self.sub, "f.txt"), root=self.root)
        self.assertEqual(name, "f.txt")

    def test_read_escape_rejected(self):
        with self.assertRaises(ValueError):
            read_file_b64("/etc/hostname", root=self.root)
